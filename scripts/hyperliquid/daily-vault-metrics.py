"""Daily Hyperliquid vault metrics pipeline.

Scans native Hyperliquid vaults, computes share prices,
stores metrics in a DuckDB database, and merges the data into
the existing ERC-4626 vault pipeline files (VaultDatabase pickle
and cleaned Parquet).

After this script runs, the existing ``vault-analysis-json.py`` will
produce a combined JSON with both ERC-4626 and Hyperliquid vaults.

Usage:

.. code-block:: shell

    # Basic usage with defaults
    poetry run python scripts/hyperliquid/daily-vault-metrics.py

    # Quick test with one vault
    MAX_VAULTS=1 MIN_TVL=1000000 poetry run python scripts/hyperliquid/daily-vault-metrics.py

    # Scan specific vaults by address
    VAULT_ADDRESSES=0x3df9769bbbb335340872f01d8157c779d73c6ed0,0xdfc24b077bc1425ad1dea75bcb6f8158e3df2f0f \
      poetry run python scripts/hyperliquid/daily-vault-metrics.py

    # With debug logging
    LOG_LEVEL=info poetry run python scripts/hyperliquid/daily-vault-metrics.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: warning
- ``DB_PATH``: Path to DuckDB database file. Default: ~/.tradingstrategy/hyperliquid/daily-metrics.duckdb
- ``VAULT_ADDRESSES``: Comma-separated list of vault addresses to scan.
  When set, overrides ``MIN_TVL`` and ``MAX_VAULTS`` filters.
- ``MIN_TVL``: Minimum TVL in USD to include a vault. Default: 5000
- ``MAX_VAULTS``: Maximum number of vaults to process. Default: 20000
- ``MAX_WORKERS``: Maximum number of parallel workers. Default: 16
- ``VAULT_DB_PATH``: Path to existing ERC-4626 VaultDatabase pickle to merge into.
  Default: ~/.tradingstrategy/vaults/vault-metadata-db.pickle
- ``PARQUET_PATH``: Path to uncleaned Parquet to merge into (raw format).
  Hypercore data is written here and then goes through the standard
  cleaning pipeline.
  Default: ~/.tradingstrategy/vaults/vault-prices-1h.parquet

"""

import logging
import os
from pathlib import Path

from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID, HYPERLIQUID_DAILY_METRICS_DATABASE
from eth_defi.hyperliquid.daily_metrics import run_daily_scan
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault_data_export import (
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
        log_file=Path("logs/hyperliquid-daily-vault-metrics.log"),
    )

    db_path_str = os.environ.get("DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else HYPERLIQUID_DAILY_METRICS_DATABASE

    vault_addresses_str = os.environ.get("VAULT_ADDRESSES", "").strip()
    vault_addresses = [a.strip() for a in vault_addresses_str.split(",") if a.strip()] or None

    min_tvl = float(os.environ.get("MIN_TVL", "5000"))
    max_vaults = int(os.environ.get("MAX_VAULTS", "20000"))
    max_workers = int(os.environ.get("MAX_WORKERS", "16"))

    vault_db_path_str = os.environ.get("VAULT_DB_PATH")
    vault_db_path = Path(vault_db_path_str).expanduser() if vault_db_path_str else DEFAULT_VAULT_DATABASE

    uncleaned_path_str = os.environ.get("PARQUET_PATH")
    uncleaned_path = Path(uncleaned_path_str).expanduser() if uncleaned_path_str else DEFAULT_UNCLEANED_PRICE_DATABASE

    print(f"Hyperliquid daily vault metrics pipeline")
    print(f"DuckDB path: {db_path}")
    if vault_addresses:
        print(f"Vault addresses: {', '.join(vault_addresses)}")
    else:
        print(f"Min TVL: ${min_tvl:,.0f}")
        print(f"Max vaults: {max_vaults}")
    print(f"Max workers: {max_workers}")
    print(f"VaultDB path: {vault_db_path}")
    print(f"Uncleaned parquet path: {uncleaned_path}")

    # Create rate-limited session
    session = create_hyperliquid_session(requests_per_second=2.75)

    # Step 1: Scan and store in DuckDB
    print(f"\nStep 1: Scanning Hyperliquid vaults...")
    db = run_daily_scan(
        session=session,
        db_path=db_path,
        min_tvl=min_tvl,
        max_vaults=max_vaults,
        max_workers=max_workers,
        vault_addresses=vault_addresses,
    )

    try:
        vault_count = db.get_vault_count()
        print(f"Stored metrics for {vault_count} vaults in DuckDB")

        # Step 2: Merge into VaultDatabase pickle
        print(f"\nStep 2: Merging into VaultDatabase at {vault_db_path}...")
        vault_db = merge_into_vault_database(db, vault_db_path)
        print(f"VaultDatabase now has {len(vault_db)} total vaults")

        # Step 3: Merge into uncleaned Parquet (raw format for the cleaning pipeline)
        print(f"\nStep 3: Merging into uncleaned Parquet at {uncleaned_path}...")
        combined_df = merge_into_uncleaned_parquet(db, uncleaned_path)
        hl_rows = combined_df[combined_df["chain"] == HYPERCORE_CHAIN_ID] if len(combined_df) > 0 else combined_df
        print(f"Uncleaned parquet now has {len(combined_df):,} total rows ({len(hl_rows):,} Hyperliquid)")

    finally:
        db.close()

    # Step 4: Run the cleaning pipeline so Hypercore data goes through
    # the same cleaning steps as EVM vaults (outlier share price smoothing,
    # return cleaning, TVL-based filtering, etc.)
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
