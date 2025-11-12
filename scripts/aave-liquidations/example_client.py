#!/usr/bin/env python3
"""
Example client that subscribes to liquidation WebSocket and stores to DuckDB.

This shows how trading bots would consume the WebSocket stream.

Usage:
    python example_client.py
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import websockets
from eth_defi.realtime_liquidations import LiquidationRecord, ParquetWriter

# Load .env file if it exists
env_file = Path(__file__).parent.parent.parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key, value.strip('"'))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def consume_liquidations(websocket_url: str, output_path: str = "./liquidations"):
    """Subscribe to WebSocket and store liquidations locally."""

    # Create local writer
    writer = ParquetWriter(
        base_path=output_path,
        source="aave-multichain",
        batch_size=50,
        flush_interval=30.0,
    )

    await writer.start()
    logger.info(f"Writing liquidations to {output_path}")

    try:
        async with websockets.connect(websocket_url) as websocket:
            logger.info(f"Connected to {websocket_url}")

            async for message in websocket:
                data = json.loads(message)

                # Skip welcome message
                if data.get("type") == "welcome":
                    logger.info(f"Connected! Monitoring chains: {data['chains']}")
                    continue

                # Process liquidation
                logger.info(
                    f"[{data['chain']}] Liquidation: "
                    f"block={data['block_number']}, "
                    f"tx={data['tx_hash'][:10]}..."
                )

                # Convert to LiquidationRecord
                record = LiquidationRecord(
                    ts=datetime.fromtimestamp(data["timestamp"], tz=timezone.utc),
                    source=f"aave-{data['chain']}",
                    protocol="aave-v3",
                    chain_id=data["chain_id"],
                    block_number=data["block_number"],
                    tx_hash=data["tx_hash"],
                    log_index=data["log_index"],
                    collateral_symbol="UNKNOWN",
                    collateral_address=data["collateral_asset"],
                    debt_symbol="UNKNOWN",
                    debt_address=data["debt_asset"],
                    debt_covered=float(data["debt_to_cover"]),
                    collateral_liquidated=float(data["liquidated_collateral_amount"]),
                    user=data["user"],
                    liquidator=data["liquidator"],
                    receive_a_token=data["receive_a_token"],
                )

                # Store locally
                await writer.write(record)

                # Here you would also update your local candles, cascades, etc.
                # This is where trading logic would go

    except websockets.exceptions.ConnectionClosed:
        logger.warning("Connection closed, reconnecting...")
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await writer.stop()
        logger.info(f"Final stats: {writer.get_stats()}")


async def main():
    """Main entry point."""
    websocket_url = "ws://localhost:8001/liquidations"

    while True:
        try:
            await consume_liquidations(websocket_url)
        except Exception as e:
            logger.error(f"Error: {e}")
            logger.info("Reconnecting in 5 seconds...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
