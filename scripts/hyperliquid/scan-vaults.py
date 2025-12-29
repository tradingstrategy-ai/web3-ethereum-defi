"""Scan all Hyperliquid vaults and store snapshots in DuckDB.

This script fetches all vault metadata from the Hyperliquid API and stores
point-in-time snapshots in a DuckDB database for historical tracking.

Usage:

.. code-block:: shell

    # Basic usage (scans all vaults, stores in default location)
    python scripts/hyperliquid/scan-vaults.py

    # With debug logging
    LOG_LEVEL=info python scripts/hyperliquid/scan-vaults.py

    # Custom database path
    DB_PATH=/path/to/vaults.duckdb python scripts/hyperliquid/scan-vaults.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``DB_PATH``: Path to DuckDB database file. Default: ~/.tradingstrategy/hyperliquid/vaults.duckdb
- ``LIMIT``: Limit the number of vaults to scan (for testing). Default: None (scan all)

"""

import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault_scanner import HYPERLIQUID_VAULT_METADATA_DATABASE, scan_vaults
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    # Set up logging
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/hyperliquid-scan-vaults.log"),
    )

    logger.info("Using log level: %s", default_log_level)

    # Get database path
    db_path_str = os.environ.get("DB_PATH")
    if db_path_str:
        db_path = Path(db_path_str).expanduser()
    else:
        db_path = HYPERLIQUID_VAULT_METADATA_DATABASE

    # Get optional limit for testing
    limit_str = os.environ.get("LIMIT")
    limit = int(limit_str) if limit_str else None

    print(f"Scanning Hyperliquid vaults...")
    print(f"Database path: {db_path}")
    if limit:
        print(f"Limit: {limit} vaults")

    # Create session and scan
    session = create_hyperliquid_session()

    db = scan_vaults(
        session=session,
        db_path=db_path,
        limit=limit,
    )

    try:
        # Print summary
        vault_count = db.get_vault_count()
        snapshot_count = db.get_count()
        timestamps = db.get_snapshot_timestamps()
        disabled_count = len(db.get_disabled_vault_addresses())

        print(f"\nScan complete!")
        print(f"Total vaults: {vault_count:,}")
        print(f"Total snapshots: {snapshot_count:,}")
        print(f"Scan timestamps: {len(timestamps)}")
        print(f"Disabled vaults: {disabled_count:,}")

        # Show top 10 vaults by TVL
        df = db.get_latest_snapshots()
        if len(df) > 0:
            print("\nTop 10 vaults by TVL:")
            top_10 = df.head(10)[["name", "vault_address", "tvl", "apr", "total_pnl", "follower_count"]]
            table_fmt = tabulate(
                top_10.to_dict("records"),
                headers="keys",
                tablefmt="fancy_grid",
            )
            print(table_fmt)
    finally:
        db.close()

    print("\nAll ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
