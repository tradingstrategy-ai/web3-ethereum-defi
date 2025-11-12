#!/usr/bin/env python3
"""
Aave V3 Multi-Chain Liquidation WebSocket Server

Streams real-time liquidation events from multiple chains via WebSocket.

Usage:
    python websocket_server_hypersync.py
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Set

import websockets
import hypersync

from eth_defi.aave_v3.liquidation import AaveLiquidationReader
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.hypersync.timestamp import get_hypersync_block_height
from eth_defi.provider.multi_provider import create_multi_provider_web3

env_file = Path(__file__).parent.parent.parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key, value.strip('"'))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress benign WebSocket handshake errors from health checks and port scanners
logging.getLogger("websockets.server").setLevel(logging.CRITICAL)

TARGET_CHAINS = {
    "ethereum": {"chain_id": 1, "rpc_env": "ETH_RPC_URL"},
    "arbitrum": {"chain_id": 42161, "rpc_env": "ARBITRUM_RPC_URL"},
    "optimism": {"chain_id": 10, "rpc_env": "OPTIMISM_RPC_URL"},
    "polygon": {"chain_id": 137, "rpc_env": "POLYGON_RPC_URL"},
    "base": {"chain_id": 8453, "rpc_env": "BASE_RPC_URL"},
    "avalanche": {"chain_id": 43114, "rpc_env": "AVALANCHE_RPC_URL"},
    "bsc": {"chain_id": 56, "rpc_env": "BSC_RPC_URL"},
}


class ChainCollector:
    """Collects liquidations from a single chain."""

    def __init__(self, chain_name: str, chain_id: int, rpc_url: str):
        self.chain_name = chain_name
        self.chain_id = chain_id
        self.rpc_url = rpc_url
        self.web3 = create_multi_provider_web3(rpc_url)
        hypersync_server = get_hypersync_server(self.web3)
        config = hypersync.ClientConfig(url=hypersync_server)
        self.client = hypersync.HypersyncClient(config)
        self.reader = AaveLiquidationReader(web3=self.web3, client=self.client)
        self.last_block = None

    async def start(self):
        try:
            loop = asyncio.get_event_loop()
            self.last_block = await loop.run_in_executor(None, get_hypersync_block_height, self.client)
            logger.info(f"[{self.chain_name}] Starting from block {self.last_block}")
        except Exception as e:
            logger.error(f"[{self.chain_name}] Failed to connect: {e}")
            raise

    async def get_new_liquidations(self) -> List[Dict]:
        try:
            loop = asyncio.get_event_loop()
            current_block = await loop.run_in_executor(None, get_hypersync_block_height, self.client)

            if current_block <= self.last_block:
                return []

            events = await loop.run_in_executor(
                None,
                self.reader.fetch_liquidations,
                self.last_block + 1,
                current_block,
            )

            self.last_block = current_block

            if not events:
                return []

            logger.info(f"[{self.chain_name}] Found {len(events)} liquidation(s)")

            liquidations = []
            for event in events:
                row = event.as_row()
                liquidations.append({
                    "chain": self.chain_name,
                    "chain_id": self.chain_id,
                    "block_number": row["block_number"],
                    "tx_hash": row["tx_hash"],
                    "log_index": row["log_index"],
                    "timestamp": int(row["timestamp"].timestamp()),
                    "timestamp_iso": row["timestamp"].isoformat(),
                    "collateral_asset": row["collateral_asset"],
                    "debt_asset": row["debt_asset"],
                    "user": row["user"],
                    "debt_to_cover": str(row["debt_to_cover"]),
                    "liquidated_collateral_amount": str(row["liquidated_collateral_amount"]),
                    "liquidator": row["liquidator"],
                    "receive_a_token": row["receive_a_token"],
                })

            return liquidations

        except Exception as e:
            logger.error(f"[{self.chain_name}] Error: {e}")
            return []


class LiquidationBroadcaster:
    """Manages WebSocket connections and broadcasts liquidations."""

    def __init__(self):
        self.connections: Set[websockets.WebSocketServerProtocol] = set()
        self.collectors: Dict[str, ChainCollector] = {}
        self.running = False

    def add_chain(self, chain_name: str, chain_id: int, rpc_url: str):
        collector = ChainCollector(chain_name, chain_id, rpc_url)
        self.collectors[chain_name] = collector
        logger.info(f"Added chain: {chain_name}")

    async def start(self):
        logger.info(f"Starting {len(self.collectors)} chain collectors...")
        for collector in self.collectors.values():
            await collector.start()
        self.running = True
        asyncio.create_task(self._poll_loop())

    async def _poll_loop(self):
        while self.running:
            try:
                tasks = [
                    collector.get_new_liquidations()
                    for collector in self.collectors.values()
                ]
                results = await asyncio.gather(*tasks)

                for liquidations in results:
                    for liq in liquidations:
                        await self.broadcast(liq)

                await asyncio.sleep(30)

            except Exception as e:
                logger.error(f"Error in poll loop: {e}")
                await asyncio.sleep(30)

    async def broadcast(self, message: Dict):
        if not self.connections:
            return

        message_json = json.dumps(message)
        disconnected = set()

        for connection in self.connections:
            try:
                await connection.send(message_json)
            except Exception:
                disconnected.add(connection)

        self.connections -= disconnected

    async def handle_client(self, websocket):
        self.connections.add(websocket)
        logger.info(f"Client connected. Total clients: {len(self.connections)}")

        try:
            await websocket.send(json.dumps({
                "type": "welcome",
                "chains": list(self.collectors.keys()),
                "message": "Connected to Aave liquidation stream"
            }))

            async for _ in websocket:
                pass

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.connections.discard(websocket)
            logger.info(f"Client disconnected. Total clients: {len(self.connections)}")


async def main():
    broadcaster = LiquidationBroadcaster()

    for chain_name, config in TARGET_CHAINS.items():
        rpc_url = os.getenv(config["rpc_env"])
        if rpc_url:
            broadcaster.add_chain(chain_name, config["chain_id"], rpc_url)

    if not broadcaster.collectors:
        logger.error("No chains configured")
        raise RuntimeError("No chains configured")

    await broadcaster.start()

    port = int(os.getenv("PORT", "8001"))
    logger.info(f"Starting WebSocket server on ws://0.0.0.0:{port}")

    async with websockets.serve(broadcaster.handle_client, "0.0.0.0", port):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
