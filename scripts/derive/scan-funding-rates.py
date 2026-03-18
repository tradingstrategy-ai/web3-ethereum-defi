"""Scan Derive funding rate history and store in DuckDB.

Fetches hourly funding rate snapshots for all (or selected) Derive
perpetual instruments and stores them in a local DuckDB database.

The scan is resumeable — running it again fetches only new data
since the last sync.  On first run it fetches the full available
history (up to ~2 years back).

Usage::

    # Full sync (all available history, resumable)
    poetry run python scripts/derive/scan-funding-rates.py

    # Quick test run (1 day, single instrument)
    LIMIT_DAYS=1 INSTRUMENTS=ETH-PERP poetry run python scripts/derive/scan-funding-rates.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``DB_PATH``: DuckDB path. Default: ~/.tradingstrategy/derive/funding-rates.duckdb
- ``INSTRUMENTS``: Comma-separated instrument names. Default: all active perps (auto-discovered)
- ``LIMIT_DAYS``: Limit history to N days (for quick test runs). Default: not set (full history / resume)
"""

import datetime
import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.derive.api import fetch_perpetual_instruments
from eth_defi.derive.historical import DEFAULT_FUNDING_RATE_DB_PATH, DeriveFundingRateDatabase
from eth_defi.derive.session import create_derive_session
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    # Read configuration from environment
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    db_path_str = os.environ.get("DB_PATH")
    instruments_str = os.environ.get("INSTRUMENTS", "").strip()
    limit_days_str = os.environ.get("LIMIT_DAYS", "").strip()

    # Setup logging
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/derive-funding-rates.log"),
    )

    # Resolve database path
    db_path = Path(db_path_str).expanduser() if db_path_str else DEFAULT_FUNDING_RATE_DB_PATH

    # Create session
    session = create_derive_session()

    # Discover or parse instruments
    if instruments_str:
        instruments = [i.strip() for i in instruments_str.split(",") if i.strip()]
        logger.info("Using %d instruments from INSTRUMENTS env var", len(instruments))
    else:
        logger.info("Discovering active perpetual instruments from Derive API...")
        instruments = fetch_perpetual_instruments(session)
        logger.info("Discovered %d active perpetual instruments", len(instruments))

    # When LIMIT_DAYS is set, use it as the start time.
    # When not set, start_time=None lets sync_instrument auto-discover
    # the inception date and fetch the full history.
    if limit_days_str:
        limit_days = int(limit_days_str)
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        start_time = now - datetime.timedelta(days=limit_days)
        mode_label = f"Window: {limit_days} days (from {start_time.strftime('%Y-%m-%d %H:%M')} UTC)"
    else:
        start_time = None
        mode_label = "Window: full history (auto-detect inception, resumable)"

    print(f"Database: {db_path}")
    print(f"Instruments: {len(instruments)}")
    print(mode_label)
    print()

    # Sync
    db = DeriveFundingRateDatabase(db_path)
    try:
        results = db.sync_instruments(
            session,
            instruments,
            start_time=start_time,
        )
        db.save()

        # Build summary table
        table_rows = []
        for name in sorted(results.keys()):
            state = db.get_sync_state(name)
            row_count = db.get_row_count(name)
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
        total_rows = sum(db.get_row_count(name) for name in results)
        print(f"\nTotal: {total_new} new rows, {total_rows} total rows across {len(results)} instruments")

    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
