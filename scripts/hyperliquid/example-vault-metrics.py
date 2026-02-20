"""Example: scan specific Hyperliquid vaults, compute metrics, display JSON output.

Demonstrates the full pipeline end-to-end:

1. Scans specific vaults by address from the Hyperliquid API
2. Computes share prices and stores in DuckDB
3. Merges into the ERC-4626 pipeline files (pickle + Parquet)
4. Runs the metrics calculation (lifetime returns, CAGR, Sharpe, etc.)
5. Exports and displays the final JSON records

Usage:

.. code-block:: shell

    # Scan two known vaults and display their metrics
    poetry run python scripts/hyperliquid/example-vault-metrics.py

    # Pick your own vaults (comma-separated addresses)
    VAULT_ADDRESSES=0xabc,0xdef poetry run python scripts/hyperliquid/example-vault-metrics.py

"""

import datetime
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from eth_defi.hyperliquid.daily_metrics import run_daily_scan
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault_data_export import (
    merge_into_uncleaned_parquet,
    merge_into_vault_database,
)
from eth_defi.research.vault_metrics import (
    calculate_hourly_returns_for_all_vaults,
    calculate_lifetime_metrics,
    export_lifetime_row,
)
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import VaultDatabase

logger = logging.getLogger(__name__)

#: Default vaults to scan if VAULT_ADDRESSES is not set
DEFAULT_VAULTS = [
    "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303",  # Hyperliquidity Provider (HLP)
    "0x1e37a337ed460039d1b15bd3bc489de789768d5e",  # Growi HF
]


def main():
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    vault_addresses_str = os.environ.get("VAULT_ADDRESSES", "").strip()
    if vault_addresses_str:
        vault_addresses = [a.strip() for a in vault_addresses_str.split(",") if a.strip()]
    else:
        vault_addresses = DEFAULT_VAULTS

    print(f"Scanning {len(vault_addresses)} Hyperliquid vaults:")
    for addr in vault_addresses:
        print(f"  {addr}")
    print()

    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        duckdb_path = tmp_path / "daily-metrics.duckdb"
        vault_db_path = tmp_path / "vault-metadata-db.pickle"
        uncleaned_path = tmp_path / "vault-prices-1h.parquet"
        cleaned_path = tmp_path / "cleaned-vault-prices-1h.parquet"

        # Step 1: scan vaults and store in DuckDB
        print("Step 1: Fetching vault data from Hyperliquid API...")
        session = create_hyperliquid_session(requests_per_second=2.75)
        db = run_daily_scan(
            session=session,
            db_path=duckdb_path,
            vault_addresses=vault_addresses,
        )

        try:
            vault_count = db.get_vault_count()
            print(f"  Stored metrics for {vault_count} vaults in DuckDB\n")

            # Step 2: merge into pipeline files
            print("Step 2: Merging into pipeline files...")
            merge_into_vault_database(db, vault_db_path)
            merge_into_uncleaned_parquet(db, uncleaned_path)
            print()

        finally:
            db.close()

        # Step 3: run cleaning pipeline
        print("Step 3: Running cleaning pipeline...")
        generate_cleaned_vault_datasets(
            vault_db_path=vault_db_path,
            price_df_path=uncleaned_path,
            cleaned_price_df_path=cleaned_path,
        )
        print()

        # Step 4: run lifetime metrics calculation
        print("Step 4: Calculating lifetime metrics...")
        vault_db = VaultDatabase.read(vault_db_path)
        prices_df = pd.read_parquet(cleaned_path)

        if not isinstance(prices_df.index, pd.DatetimeIndex):
            if "timestamp" in prices_df.columns:
                prices_df = prices_df.set_index("timestamp")

        returns_df = calculate_hourly_returns_for_all_vaults(prices_df)
        lifetime_df = calculate_lifetime_metrics(returns_df, vault_db)
        print(f"  Calculated metrics for {len(lifetime_df)} vaults\n")

        # Step 5: export to JSON and display
        print("=" * 80)
        print("VAULT METRICS JSON OUTPUT")
        print("=" * 80)

        for _, row in lifetime_df.iterrows():
            vault_json = export_lifetime_row(row)

            # Display a summary header
            name = vault_json.get("name", "<unnamed>")
            chain_name = vault_json.get("chain_name", "Hypercore")
            protocol = vault_json.get("protocol", "?")
            print(f"\n--- {name} ({protocol}, {chain_name}) ---\n")

            # Display the full JSON record
            print(json.dumps(vault_json, indent=2, ensure_ascii=False, allow_nan=False))
            print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
