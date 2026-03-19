"""Scan Derive perp snapshots (OI + prices) and store in DuckDB.

Fetches hourly snapshots for Derive perpetual instruments by reading
on-chain state from the Derive Chain archive node.  Three view functions
are batched via Multicall3 per hour per instrument: ``openInterest(uint256)``,
``getPerpPrice()``, ``getIndexPrice()``.

On first run, fetches the full available history from the instrument's
``scheduled_activation`` date.  Subsequent runs resume incrementally from
the last stored timestamp.  The insert is crash-resumeable via
``INSERT OR IGNORE``.

Usage::

    # Scan all active perps (full history on first run, incremental after)
    poetry run python scripts/derive/scan-open-interest.py

    # Scan specific instruments
    INSTRUMENTS=ETH-PERP,BTC-PERP poetry run python scripts/derive/scan-open-interest.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``DB_PATH``: DuckDB path. Default: ~/.tradingstrategy/derive/funding-rates.duckdb
- ``INSTRUMENTS``: Comma-separated instrument names. Default: all active perps
- ``DERIVE_RPC_URL``: Derive Chain RPC URL. Default: https://rpc.derive.xyz
"""

import datetime
import logging
import os
from pathlib import Path

from tabulate import tabulate
from web3 import Web3

from eth_defi.derive.api import fetch_perpetual_instruments
from eth_defi.derive.constants import DERIVE_MAINNET_RPC_URL
from eth_defi.derive.historical import DEFAULT_FUNDING_RATE_DB_PATH, DeriveFundingRateDatabase
from eth_defi.derive.session import create_derive_session
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    # Read configuration from environment
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    db_path_str = os.environ.get("DB_PATH")
    instruments_str = os.environ.get("INSTRUMENTS", "").strip()
    rpc_url = os.environ.get("DERIVE_RPC_URL", DERIVE_MAINNET_RPC_URL)
    max_workers = int(os.environ.get("MAX_WORKERS", "2"))

    # Setup logging
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/derive-open-interest.log"),
    )

    # Resolve database path
    db_path = Path(db_path_str).expanduser() if db_path_str else DEFAULT_FUNDING_RATE_DB_PATH

    # Create HTTP session and verify RPC connection
    session = create_derive_session()
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to Derive Chain RPC at {rpc_url}")

    # Discover or parse instruments
    if instruments_str:
        instruments = [i.strip() for i in instruments_str.split(",") if i.strip()]
        logger.info("Using %d instruments from INSTRUMENTS env var", len(instruments))
    else:
        logger.info("Discovering active perpetual instruments from Derive API...")
        instruments = fetch_perpetual_instruments(session)
        logger.info("Discovered %d active perpetual instruments", len(instruments))

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    latest = w3.eth.block_number
    print(f"Database: {db_path}")
    print(f"Instruments: {len(instruments)}")
    print(f"Derive Chain RPC: {rpc_url} (block {latest:,})")
    print(f"Workers: {max_workers}")
    print(f"Scan end: {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print()

    db = DeriveFundingRateDatabase(db_path)
    try:
        # Print current DB stats before starting
        db_file_size = db_path.stat().st_size if db_path.exists() else 0
        if db_file_size >= 1_000_000:
            size_str = f"{db_file_size / 1_000_000:.1f} MB"
        elif db_file_size >= 1_000:
            size_str = f"{db_file_size / 1_000:.1f} kB"
        else:
            size_str = f"{db_file_size} bytes"
        existing_rows = db.conn.execute("SELECT COUNT(*) FROM open_interest").fetchone()[0]
        print(f"Current DB size: {size_str}, existing entries: {existing_rows:,}")
        print()

        results = db.sync_open_interest_instruments(
            session,
            instruments,
            rpc_url=rpc_url,
            max_workers=max_workers,
        )
        db.save()

        # Build summary table
        table_rows = []
        for name in sorted(results.keys()):
            state = db.get_open_interest_sync_state(name)
            row_count = db.get_open_interest_row_count(name)
            if state and state["oldest_ts"] and state["newest_ts"]:
                oldest = datetime.datetime.fromtimestamp(state["oldest_ts"] / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
                newest = datetime.datetime.fromtimestamp(state["newest_ts"] / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")
            else:
                oldest = "-"
                newest = "-"
            table_rows.append([name, results[name], row_count, oldest, newest])

        print()
        print(
            tabulate(
                table_rows,
                headers=["Instrument", "New rows", "Total rows", "Oldest", "Newest"],
                tablefmt="simple",
            )
        )

        total_new = sum(results.values())
        total_rows = sum(db.get_open_interest_row_count(name) for name in results)
        print(f"\nTotal: {total_new} new rows, {total_rows} total rows across {len(results)} instruments")

    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
