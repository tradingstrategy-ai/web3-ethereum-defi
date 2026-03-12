"""Stage 2: Apply backfill data from staging DuckDB to main metrics database.

Reads vault data from the staging DuckDB (populated by ``extract-s3-vault-data.py``),
inserts missing dates into the main ``daily-metrics.duckdb``, and recomputes share
prices for affected vaults.

Only fills gaps — never overwrites existing API-sourced data.

Usage:

.. code-block:: shell

    # Apply backfill to all vaults
    poetry run python scripts/hyperliquid/backfill-vault-data.py

    # Apply to specific vaults only
    VAULT_ADDRESSES=0x4cb5f4d145cd16460932bbb9b871bb6fd5db97e3 \\
        poetry run python scripts/hyperliquid/backfill-vault-data.py

    # Apply and run downstream cleaning pipeline
    RUN_PIPELINE=true poetry run python scripts/hyperliquid/backfill-vault-data.py

Environment variables:

- ``STAGING_DB_PATH``: Staging DuckDB path.
  Default: ``~/.tradingstrategy/hyperliquid/s3-vault-backfill.duckdb``
- ``DB_PATH``: Main metrics DuckDB path.
  Default: ``~/.tradingstrategy/hyperliquid/daily-metrics.duckdb``
- ``VAULT_ADDRESSES``: Comma-separated list of vault addresses to backfill.
  Default: all vaults in staging DB.
- ``RUN_PIPELINE``: If ``true``, run downstream cleaning pipeline after backfill.
- ``LOG_LEVEL``: Logging level. Default: ``warning``

"""

import logging
import os
from pathlib import Path

from eth_defi.hyperliquid.backfill import (
    HYPERLIQUID_S3_STAGING_DATABASE,
    HyperliquidS3StagingDatabase,
    apply_backfill,
)
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID, HYPERLIQUID_DAILY_METRICS_DATABASE
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.utils import setup_console_logging

logger = logging.getLogger(__name__)


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(default_log_level=default_log_level)

    staging_db_path_str = os.environ.get("STAGING_DB_PATH")
    staging_db_path = Path(staging_db_path_str).expanduser() if staging_db_path_str else HYPERLIQUID_S3_STAGING_DATABASE

    db_path_str = os.environ.get("DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else HYPERLIQUID_DAILY_METRICS_DATABASE

    vault_addresses_str = os.environ.get("VAULT_ADDRESSES", "").strip()
    vault_addresses = [a.strip() for a in vault_addresses_str.split(",") if a.strip()] or None

    run_pipeline = os.environ.get("RUN_PIPELINE", "").lower() in ("true", "1", "yes")

    if not staging_db_path.exists():
        raise FileNotFoundError(f"Staging DB not found: {staging_db_path}\nRun extract-s3-vault-data.py first to populate it.")

    print(f"Hyperliquid vault data backfill (Stage 2)")
    print(f"Staging DB: {staging_db_path}")
    print(f"Metrics DB: {db_path}")
    if vault_addresses:
        print(f"Vault filter: {', '.join(vault_addresses)}")
    else:
        print(f"Vault filter: all vaults in staging DB")
    print(f"Run pipeline: {run_pipeline}")

    staging_db = HyperliquidS3StagingDatabase(staging_db_path)
    metrics_db = HyperliquidDailyMetricsDatabase(db_path)
    try:
        staging_vault_count = staging_db.get_vault_count()
        staging_row_count = staging_db.get_total_rows()
        print(f"Staging DB: {staging_vault_count:,} vaults, {staging_row_count:,} data points")

        metrics_vault_count = metrics_db.get_vault_count()
        print(f"Metrics DB: {metrics_vault_count:,} vaults")
        print()

        result = apply_backfill(
            staging_db=staging_db,
            metrics_db=metrics_db,
            vault_addresses=vault_addresses,
        )

        print(f"\nBackfill complete:")
        print(f"  Vaults processed: {result['vaults_processed']:,}")
        print(f"  Vaults with new data: {result['vaults_with_new_data']:,}")
        print(f"  Dates inserted: {result['total_inserted']:,}")
        print(f"  Dates skipped (already existed): {result['total_skipped']:,}")

    finally:
        staging_db.close()
        metrics_db.close()

    if run_pipeline:
        from eth_defi.hyperliquid.vault_data_export import (
            merge_into_uncleaned_parquet,
            merge_into_vault_database,
        )
        from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
        from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE

        vault_db_path = Path(os.environ.get("VAULT_DB_PATH", "")).expanduser() if os.environ.get("VAULT_DB_PATH") else DEFAULT_VAULT_DATABASE
        uncleaned_path = Path(os.environ.get("PARQUET_PATH", "")).expanduser() if os.environ.get("PARQUET_PATH") else DEFAULT_UNCLEANED_PRICE_DATABASE

        # Re-open the metrics DB for pipeline export
        metrics_db = HyperliquidDailyMetricsDatabase(db_path)
        try:
            print(f"\nRunning downstream pipeline...")

            print(f"  Merging into VaultDatabase at {vault_db_path}...")
            vault_db = merge_into_vault_database(metrics_db, vault_db_path)
            print(f"  VaultDatabase: {len(vault_db)} total vaults")

            print(f"  Merging into uncleaned Parquet at {uncleaned_path}...")
            combined_df = merge_into_uncleaned_parquet(metrics_db, uncleaned_path)
            hl_rows = combined_df[combined_df["chain"] == HYPERCORE_CHAIN_ID] if len(combined_df) > 0 else combined_df
            print(f"  Uncleaned parquet: {len(combined_df):,} total rows ({len(hl_rows):,} Hyperliquid)")
        finally:
            metrics_db.close()

        print(f"  Running cleaning pipeline...")
        generate_cleaned_vault_datasets(
            vault_db_path=vault_db_path,
            price_df_path=uncleaned_path,
        )

        print(f"\nPipeline complete")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
