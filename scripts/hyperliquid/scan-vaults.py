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
- ``MAX_WORKERS``: Maximum number of parallel workers for fetching vault details. Default: 16

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

    # Get max workers for parallel processing
    max_workers = int(os.environ.get("MAX_WORKERS", "6"))

    print(f"Scanning Hyperliquid vaults...")
    print(f"Database path: {db_path}")
    if limit:
        print(f"Limit: {limit} vaults")
    print(f"Max workers: {max_workers}")

    # Create session and scan.
    # Rate limiting uses SQLite backend for thread-safe coordination across workers.
    session = create_hyperliquid_session(
        requests_per_second=2.75,
    )

    db = scan_vaults(
        session=session,
        db_path=db_path,
        limit=limit,
        max_workers=max_workers,
    )

    try:
        # Print summary
        vault_count = db.get_vault_count()
        snapshot_count = db.get_count()
        timestamps = db.get_snapshot_timestamps()
        disabled_count = len(db.get_disabled_vault_addresses())

        print(f"\nScan complete!")
        print(f"Total vaults: {vault_count:,}")
        print(f"Active vaults: {vault_count - disabled_count:,}")
        print(f"Disabled vaults: {disabled_count:,}")
        print(f"Total snapshots: {snapshot_count:,}")
        print(f"Scan timestamps: {len(timestamps)}")

        # Show top 10 vaults by TVL
        df = db.get_latest_snapshots()
        if len(df) > 0:
            print("\nTop 10 vaults by TVL:")
            top_10 = df.head(10)[["name", "vault_address", "tvl", "apr", "total_pnl", "follower_count"]].copy()
            # Format TVL as currency
            top_10["tvl"] = top_10["tvl"].apply(lambda x: f"${x:,.0f}" if x is not None else "")
            # Format APR and total_pnl as percent
            top_10["apr"] = top_10["apr"].apply(lambda x: f"{x * 100:.2f}%" if x is not None else "")
            top_10["total_pnl"] = top_10["total_pnl"].apply(lambda x: f"${x:,.0f}" if x is not None else "")
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
