"""Daily GRVT vault metrics pipeline.

Discovers GRVT vaults via the public GraphQL API (includes per-vault
fee data), fetches share price history from the market data API,
stores metrics in DuckDB, and merges the data into the existing
ERC-4626 vault pipeline files (VaultDatabase pickle and cleaned Parquet).

No authentication is required — all data comes from public endpoints.

After this script runs, the existing ``vault-analysis-json.py`` will
produce a combined JSON with ERC-4626, Hyperliquid, and GRVT vaults.

Usage:

.. code-block:: shell

    # Basic usage with defaults
    poetry run python scripts/grvt/daily-vault-metrics.py

    # With debug logging
    LOG_LEVEL=info poetry run python scripts/grvt/daily-vault-metrics.py

    # Scan specific vaults by string ID
    VAULT_IDS=VLT:34dTZyg6LhkGM49Je5AABi9tEbW \\
      poetry run python scripts/grvt/daily-vault-metrics.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``DB_PATH``: Path to DuckDB database file. Default: ~/.tradingstrategy/vaults/grvt-vaults.duckdb
- ``VAULT_IDS``: Comma-separated list of vault string IDs to scan. Default: all discoverable vaults
- ``VAULT_DB_PATH``: Path to existing ERC-4626 VaultDatabase pickle to merge into.
  Default: ~/.tradingstrategy/vaults/vault-metadata-db.pickle
- ``PARQUET_PATH``: Path to uncleaned Parquet to merge into.
  Default: ~/.tradingstrategy/vaults/vault-prices-1h.parquet

"""

import logging
import os
from pathlib import Path

from eth_defi.grvt.constants import GRVT_CHAIN_ID, GRVT_DAILY_METRICS_DATABASE
from eth_defi.grvt.daily_metrics import run_daily_scan
from eth_defi.grvt.vault_data_export import (
    merge_into_uncleaned_parquet,
    merge_into_vault_database,
)
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE

logger = logging.getLogger(__name__)


def main():
    # Configuration from environment
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/grvt-daily-vault-metrics.log"),
    )

    db_path_str = os.environ.get("DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else GRVT_DAILY_METRICS_DATABASE

    vault_ids_str = os.environ.get("VAULT_IDS", "").strip()
    vault_ids = [v.strip() for v in vault_ids_str.split(",") if v.strip()] or None

    vault_db_path_str = os.environ.get("VAULT_DB_PATH")
    vault_db_path = Path(vault_db_path_str).expanduser() if vault_db_path_str else DEFAULT_VAULT_DATABASE

    uncleaned_path_str = os.environ.get("PARQUET_PATH")
    uncleaned_path = Path(uncleaned_path_str).expanduser() if uncleaned_path_str else DEFAULT_UNCLEANED_PRICE_DATABASE

    print(f"GRVT daily vault metrics pipeline")
    print(f"DuckDB path: {db_path}")
    if vault_ids:
        print(f"Vault IDs: {', '.join(vault_ids)}")
    else:
        print(f"Scanning all discoverable vaults")
    print(f"VaultDB path: {vault_db_path}")
    print(f"Uncleaned parquet path: {uncleaned_path}")

    # Step 1: Scan and store in DuckDB
    print(f"\nStep 1: Scanning GRVT vaults...")
    db = run_daily_scan(
        db_path=db_path,
        vault_ids=vault_ids,
    )

    try:
        vault_count = db.get_vault_count()
        print(f"Stored metrics for {vault_count} vaults in DuckDB")

        # Step 2: Merge into VaultDatabase pickle
        print(f"\nStep 2: Merging into VaultDatabase at {vault_db_path}...")
        vault_db = merge_into_vault_database(db, vault_db_path)
        print(f"VaultDatabase now has {len(vault_db)} total vaults")

        # Step 3: Merge into uncleaned Parquet
        print(f"\nStep 3: Merging into uncleaned Parquet at {uncleaned_path}...")
        combined_df = merge_into_uncleaned_parquet(db, uncleaned_path)
        grvt_rows = combined_df[combined_df["chain"] == GRVT_CHAIN_ID] if len(combined_df) > 0 else combined_df
        print(f"Uncleaned parquet now has {len(combined_df):,} total rows ({len(grvt_rows):,} GRVT)")

    finally:
        db.close()

    # Step 4: Run the cleaning pipeline
    print(f"\nStep 4: Running cleaning pipeline...")
    generate_cleaned_vault_datasets(
        vault_db_path=vault_db_path,
        price_df_path=uncleaned_path,
    )

    print(f"\nAll ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
