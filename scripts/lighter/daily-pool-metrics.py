"""Daily Lighter pool metrics pipeline.

Discovers Lighter pools via the public API, fetches share price history,
stores metrics in DuckDB, and merges the data into the existing
ERC-4626 vault pipeline files (VaultDatabase pickle and cleaned Parquet).

No authentication is required — all data comes from public endpoints.

After this script runs, the existing ``vault-analysis-json.py`` will
produce a combined JSON with ERC-4626, Hyperliquid, GRVT, and Lighter pools.

Usage:

.. code-block:: shell

    # Basic usage with defaults
    poetry run python scripts/lighter/daily-pool-metrics.py

    # With debug logging
    LOG_LEVEL=info poetry run python scripts/lighter/daily-pool-metrics.py

    # Scan specific pools by account index
    POOL_INDICES=281474976710654,281474976710653 \\
      poetry run python scripts/lighter/daily-pool-metrics.py

    # Limit to top pools by TVL
    MIN_TVL=10000 MAX_POOLS=20 \\
      poetry run python scripts/lighter/daily-pool-metrics.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``DB_PATH``: Path to DuckDB database file. Default: ~/.tradingstrategy/vaults/lighter-pools.duckdb
- ``POOL_INDICES``: Comma-separated list of pool account indices to scan. Default: all pools
- ``MIN_TVL``: Minimum TVL in USDC to include a pool. Default: 1000
- ``MAX_POOLS``: Maximum number of pools to scan. Default: 200
- ``MAX_WORKERS``: Number of parallel workers. Default: 16
- ``VAULT_DB_PATH``: Path to existing ERC-4626 VaultDatabase pickle to merge into.
  Default: ~/.tradingstrategy/vaults/vault-metadata-db.pickle
- ``PARQUET_PATH``: Path to uncleaned Parquet to merge into.
  Default: ~/.tradingstrategy/vaults/vault-prices-1h.parquet

"""

import logging
import os
from pathlib import Path

import pandas as pd

from eth_defi.lighter.constants import LIGHTER_CHAIN_ID, LIGHTER_DAILY_METRICS_DATABASE
from eth_defi.lighter.daily_metrics import run_daily_scan
from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.vault_data_export import (
    merge_into_uncleaned_parquet,
    merge_into_vault_database,
)
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE

logger = logging.getLogger(__name__)


def main():
    # Configuration from environment
    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/lighter-daily-pool-metrics.log"),
    )

    db_path_str = os.environ.get("DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else LIGHTER_DAILY_METRICS_DATABASE

    pool_indices_str = os.environ.get("POOL_INDICES", "").strip()
    pool_indices = [int(v.strip()) for v in pool_indices_str.split(",") if v.strip()] or None

    min_tvl = float(os.environ.get("MIN_TVL", "1000"))
    max_pools = int(os.environ.get("MAX_POOLS", "200"))
    max_workers = int(os.environ.get("MAX_WORKERS", "16"))

    vault_db_path_str = os.environ.get("VAULT_DB_PATH")
    vault_db_path = Path(vault_db_path_str).expanduser() if vault_db_path_str else DEFAULT_VAULT_DATABASE

    uncleaned_path_str = os.environ.get("PARQUET_PATH")
    uncleaned_path = Path(uncleaned_path_str).expanduser() if uncleaned_path_str else DEFAULT_UNCLEANED_PRICE_DATABASE

    print("Lighter daily pool metrics pipeline")
    print(f"DuckDB path: {db_path}")
    if pool_indices:
        print(f"Pool indices: {pool_indices}")
    else:
        print(f"Scanning all pools (min_tvl=${min_tvl:,.0f}, max_pools={max_pools})")
    print(f"VaultDB path: {vault_db_path}")
    print(f"Uncleaned parquet path: {uncleaned_path}")

    # Step 1: Scan and store in DuckDB
    print("\nStep 1: Scanning Lighter pools...")
    session = create_lighter_session()
    db = run_daily_scan(
        session,
        db_path=db_path,
        min_tvl=min_tvl,
        max_pools=max_pools,
        max_workers=max_workers,
        pool_indices=pool_indices,
    )

    try:
        pool_count = db.get_pool_count()
        print(f"Stored metrics for {pool_count} pools in DuckDB")

        # Step 2: Merge into VaultDatabase pickle
        print(f"\nStep 2: Merging into VaultDatabase at {vault_db_path}...")
        vault_db = merge_into_vault_database(db, vault_db_path)
        print(f"VaultDatabase now has {len(vault_db)} total vaults")

        # Step 3: Merge into uncleaned Parquet
        print(f"\nStep 3: Merging into uncleaned Parquet at {uncleaned_path}...")
        combined_df = merge_into_uncleaned_parquet(db, uncleaned_path)
        lighter_rows = combined_df[combined_df["chain"] == LIGHTER_CHAIN_ID] if len(combined_df) > 0 else combined_df
        print(f"Uncleaned parquet now has {len(combined_df):,} total rows ({len(lighter_rows):,} Lighter)")

    finally:
        db.close()

    # Step 4: Run the cleaning pipeline
    print(f"\nStep 4: Running cleaning pipeline...")
    generate_cleaned_vault_datasets(
        vault_db_path=vault_db_path,
        price_df_path=uncleaned_path,
    )

    # Step 5: Verify results in cleaned output
    print(f"\nStep 5: Verifying results in {DEFAULT_RAW_PRICE_DATABASE}...")
    if DEFAULT_RAW_PRICE_DATABASE.exists():
        cleaned_df = pd.read_parquet(DEFAULT_RAW_PRICE_DATABASE)
        lighter_cleaned = cleaned_df[cleaned_df["chain"] == LIGHTER_CHAIN_ID] if "chain" in cleaned_df.columns else pd.DataFrame()
        if not lighter_cleaned.empty:
            lighter_pools_in_cleaned = lighter_cleaned["address"].nunique() if "address" in lighter_cleaned.columns else 0
            print(f"Cleaned parquet has {len(lighter_cleaned):,} Lighter rows across {lighter_pools_in_cleaned} pools")
            print(f"Total cleaned rows: {len(cleaned_df):,}")
        else:
            print("WARNING: No Lighter data found in cleaned parquet")
            # This can happen if the cleaning pipeline filters out all Lighter pools
            # (e.g. due to non-stablecoin denomination or insufficient data)
            print("This may be expected if pools were filtered during cleaning")
    else:
        print(f"WARNING: Cleaned parquet not found at {DEFAULT_RAW_PRICE_DATABASE}")

    print("\nAll ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
