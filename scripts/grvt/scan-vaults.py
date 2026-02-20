"""Scan all GRVT vaults and store snapshots in DuckDB.

This script discovers vaults via the public GraphQL API
(includes per-vault fee data), enriches them with live data
from the market data API, and stores point-in-time snapshots
in a DuckDB database.

No authentication is required — all data comes from public endpoints.

Usage:

.. code-block:: shell

    # Basic usage (scans all discoverable vaults)
    poetry run python scripts/grvt/scan-vaults.py

    # With debug logging
    LOG_LEVEL=info poetry run python scripts/grvt/scan-vaults.py

    # Custom database path
    DB_PATH=/path/to/vaults.duckdb poetry run python scripts/grvt/scan-vaults.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``DB_PATH``: Path to DuckDB database file. Default: ~/.tradingstrategy/grvt/vaults.duckdb

"""

import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.grvt.vault_scanner import GRVT_VAULT_METADATA_DATABASE, scan_vaults
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    # Set up logging
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/grvt-scan-vaults.log"),
    )

    logger.info("Using log level: %s", default_log_level)

    # Get database path
    db_path_str = os.environ.get("DB_PATH")
    if db_path_str:
        db_path = Path(db_path_str).expanduser()
    else:
        db_path = GRVT_VAULT_METADATA_DATABASE

    print(f"Scanning GRVT vaults...")
    print(f"Database path: {db_path}")

    db = scan_vaults(
        db_path=db_path,
    )

    try:
        # Print summary
        vault_count = db.get_vault_count()
        snapshot_count = db.get_count()
        timestamps = db.get_snapshot_timestamps()

        print(f"\nScan complete!")
        print(f"Total vaults: {vault_count:,}")
        print(f"Total snapshots: {snapshot_count:,}")
        print(f"Scan timestamps: {len(timestamps)}")

        # Show all vaults
        df = db.get_latest_snapshots()
        if len(df) > 0:
            print("\nVaults:")
            display_df = df[["name", "vault_id", "tvl", "share_price", "apr"]].copy()
            display_df["tvl"] = display_df["tvl"].apply(lambda x: f"${x:,.0f}" if x is not None and x == x else "")
            display_df["apr"] = display_df["apr"].apply(lambda x: f"{x * 100:.1f}%" if x is not None and x == x else "")
            display_df["share_price"] = display_df["share_price"].apply(lambda x: f"{x:.6f}" if x is not None and x == x else "")
            table_fmt = tabulate(
                display_df.to_dict("records"),
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
