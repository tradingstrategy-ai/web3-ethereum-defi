"""Heal Hyperliquid vault share prices offline from stored DuckDB data.

Problem
-------

The production DuckDB was built with old share price logic that:

1. Reset share_price to 1.0 on epoch boundaries (all followers withdrew),
   creating discontinuities in the price series
2. Lacked the ``epoch_reset`` boolean column for tracking resets
3. Produced extreme daily returns (> 100%) at epoch reset points

The new chain-linked epoch reset logic carries forward the last epoch's
share price, keeping the series continuous. However, the Hyperliquid API
may not return the same historical depth when re-fetched, so we cannot
simply delete old data and re-scan.

Solution
--------

This script recomputes share prices **entirely offline** from the data
already stored in DuckDB. The stored ``tvl`` (total_assets), ``daily_pnl``
(pnl_update), and ``cumulative_pnl`` columns contain enough information
to reconstruct ``netflow_update`` and rerun ``_calculate_share_price()``
with the fixed chain-linked logic.

Usage
-----

.. code-block:: shell

    # Dry run: detect broken vaults without modifying data
    DRY_RUN=true poetry run python scripts/hyperliquid/heal-share-prices-offline.py

    # Heal all vaults
    poetry run python scripts/hyperliquid/heal-share-prices-offline.py

    # Heal specific vaults
    VAULT_ADDRESSES=0x4dec0a851849056e259128464ef28ce78afa27f6 \\
      poetry run python scripts/hyperliquid/heal-share-prices-offline.py

    # Heal and re-run downstream cleaning pipeline
    RUN_PIPELINE=true poetry run python scripts/hyperliquid/heal-share-prices-offline.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: info
- ``DB_PATH``: Path to DuckDB database file.
  Default: ~/.tradingstrategy/vaults/hyperliquid-vaults.duckdb
- ``VAULT_ADDRESSES``: Comma-separated vault addresses to heal.
  Default: all vaults in the database.
- ``DRY_RUN``: If "true", only detect without modifying data. Default: false
- ``RUN_PIPELINE``: If "true", run the cleaning pipeline after healing. Default: false
- ``VAULT_DB_PATH``: Path to VaultDatabase pickle.
  Default: ~/.tradingstrategy/vaults/vault-metadata-db.pickle
- ``PARQUET_PATH``: Path to uncleaned Parquet.
  Default: ~/.tradingstrategy/vaults/vault-prices-1h.parquet

"""

import logging
import os
from pathlib import Path

from tabulate import tabulate

from eth_defi.hyperliquid.constants import HYPERLIQUID_DAILY_METRICS_DATABASE
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.hyperliquid.vault_data_export import (
    merge_into_uncleaned_parquet,
    merge_into_vault_database,
)
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE

logger = logging.getLogger(__name__)


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "info")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/heal-share-prices-offline.log"),
    )

    db_path_str = os.environ.get("DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else HYPERLIQUID_DAILY_METRICS_DATABASE

    vault_addresses_str = os.environ.get("VAULT_ADDRESSES", "").strip()
    vault_addresses_filter = [a.strip().lower() for a in vault_addresses_str.split(",") if a.strip()] or None

    dry_run = os.environ.get("DRY_RUN", "").lower() == "true"
    run_pipeline = os.environ.get("RUN_PIPELINE", "").lower() == "true"

    vault_db_path_str = os.environ.get("VAULT_DB_PATH")
    vault_db_path = Path(vault_db_path_str).expanduser() if vault_db_path_str else DEFAULT_VAULT_DATABASE

    uncleaned_path_str = os.environ.get("PARQUET_PATH")
    uncleaned_path = Path(uncleaned_path_str).expanduser() if uncleaned_path_str else DEFAULT_UNCLEANED_PRICE_DATABASE

    print(f"Hyperliquid offline share price healer")
    print(f"DuckDB path: {db_path}")
    print(f"Dry run: {dry_run}")
    print()

    if not db_path.exists():
        print(f"Database not found at {db_path}. Nothing to heal.")
        return

    db = HyperliquidDailyMetricsDatabase(db_path)

    try:
        # Step 1: Detect broken vaults
        print("Step 1: Detecting broken vaults...")
        issues_df = db.detect_broken_vaults()

        if vault_addresses_filter:
            issues_df = issues_df[issues_df["vault_address"].isin(vault_addresses_filter)]

        if issues_df.empty:
            print("No broken vaults detected.")
        else:
            # Group by issue type for summary
            for issue_type, group in issues_df.groupby("issue_type"):
                print(f"\n{issue_type} ({len(group)} vaults):")
                table_data = []
                for _, row in group.iterrows():
                    table_data.append(
                        [
                            row["name"][:30] if row["name"] else "<unknown>",
                            row["vault_address"][:10] + "...",
                            row["affected_rows"],
                            f"{row['example_value']:.4f}" if row["example_value"] is not None else "N/A",
                        ]
                    )
                print(tabulate(table_data, headers=["Name", "Address", "Rows", "Example"], tablefmt="simple"))

        print(f"\nTotal issues found: {len(issues_df)} across {issues_df['vault_address'].nunique()} vaults")

        if dry_run:
            print("\nDRY_RUN=true — no changes made.")
            return

        # Step 2: Recompute share prices
        print("\nStep 2: Recomputing share prices offline...")

        if vault_addresses_filter:
            results = {}
            metadata = db.get_all_vault_metadata()
            name_map = dict(zip(metadata["vault_address"], metadata["name"]))
            for addr in vault_addresses_filter:
                result = db.recompute_vault_share_prices(addr)
                results[addr] = {"name": name_map.get(addr, "<unknown>"), **result}
            db.save()
            summary = {
                "total_vaults": len(vault_addresses_filter),
                "vaults_with_changes": sum(1 for r in results.values() if r["changed_rows"] > 0),
                "vaults_with_epoch_resets": sum(1 for r in results.values() if r["epoch_resets"] > 0),
                "total_changed_rows": sum(r["changed_rows"] for r in results.values()),
                "per_vault": results,
            }
        else:
            summary = db.recompute_all_share_prices()

        print(f"\nRecomputation summary:")
        print(f"  Total vaults: {summary['total_vaults']}")
        print(f"  Vaults with changes: {summary['vaults_with_changes']}")
        print(f"  Vaults with epoch resets: {summary['vaults_with_epoch_resets']}")
        print(f"  Total changed rows: {summary['total_changed_rows']}")

        # Show per-vault details for changed vaults
        changed = {addr: info for addr, info in summary["per_vault"].items() if info["changed_rows"] > 0}
        if changed:
            print(f"\nChanged vaults ({len(changed)}):")
            table_data = []
            for addr, info in sorted(changed.items(), key=lambda x: x[1]["changed_rows"], reverse=True):
                table_data.append(
                    [
                        info["name"][:30],
                        addr[:10] + "...",
                        info["rows"],
                        info["changed_rows"],
                        info["epoch_resets"],
                        f"{info['old_sp_min']:.2f}-{info['old_sp_max']:.2f}",
                        f"{info['new_sp_min']:.2f}-{info['new_sp_max']:.2f}",
                    ]
                )
            print(
                tabulate(
                    table_data,
                    headers=["Name", "Address", "Rows", "Changed", "Resets", "Old SP range", "New SP range"],
                    tablefmt="simple",
                )
            )

        # Step 3: Verify
        print("\nStep 3: Verifying...")
        post_issues = db.detect_broken_vaults()
        if vault_addresses_filter:
            post_issues = post_issues[post_issues["vault_address"].isin(vault_addresses_filter)]

        pre_count = len(issues_df)
        post_count = len(post_issues)
        print(f"  Issues before: {pre_count}")
        print(f"  Issues after: {post_count}")
        if post_count < pre_count:
            print(f"  Fixed {pre_count - post_count} issues")
        if post_count > 0:
            print(f"  Remaining issues:")
            for _, row in post_issues.iterrows():
                print(f"    {row['name'][:30]:30s}  {row['issue_type']:30s}  rows={row['affected_rows']}")

        # Step 4: Optionally run pipeline
        if run_pipeline:
            print(f"\nStep 4: Running downstream pipeline...")
            vault_db = merge_into_vault_database(db, vault_db_path)
            print(f"  VaultDatabase now has {len(vault_db)} total vaults")

            combined_df = merge_into_uncleaned_parquet(db, uncleaned_path)
            print(f"  Uncleaned parquet: {len(combined_df):,} total rows")

            db.close()
            db = None

            generate_cleaned_vault_datasets(
                vault_db_path=vault_db_path,
                price_df_path=uncleaned_path,
            )
            print("  Cleaning pipeline complete")
        else:
            print("\nSkipping pipeline (set RUN_PIPELINE=true to run)")

    finally:
        if db is not None:
            db.close()

    print("\nAll ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
