"""Heal all Hyperliquid vault share prices in a single run.

Combines offline recomputation and API re-fetch into one script
that fixes share price data with minimal destruction of historical rows.

Steps
-----

1. **Detect** — scan for broken vaults (epoch resets, stuck prices, etc.)
2. **Offline recomputation** — recompute share prices from stored
   ``tvl`` / ``daily_pnl`` / ``cumulative_pnl`` without any API calls
   or data deletion. Fixes epoch reset artefacts and populates the
   ``epoch_reset`` column.
3. **API re-fetch** — for vaults still stuck at share price 1.0 after
   the offline fix, delete and re-fetch from the Hyperliquid API with
   multi-period merge. Only the stuck vaults are re-fetched; all other
   vaults keep their existing data.
4. **Verify** — re-run detection and report remaining issues.
5. **Pipeline** (optional) — push healed data through the downstream
   cleaning pipeline to regenerate Parquet and VaultDatabase files.

Usage
-----

.. code-block:: shell

    # Dry run: detect issues without modifying data
    DRY_RUN=true poetry run python scripts/hyperliquid/heal-all-share-prices.py

    # Heal all vaults (offline + API re-fetch for stuck ones)
    poetry run python scripts/hyperliquid/heal-all-share-prices.py

    # Heal specific vaults only
    VAULT_ADDRESSES=0x4dec0a851849056e259128464ef28ce78afa27f6 \\
      poetry run python scripts/hyperliquid/heal-all-share-prices.py

    # Heal and run downstream cleaning pipeline
    RUN_PIPELINE=true poetry run python scripts/hyperliquid/heal-all-share-prices.py

    # Skip API re-fetch (offline recomputation only)
    SKIP_REFETCH=true poetry run python scripts/hyperliquid/heal-all-share-prices.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: info
- ``DB_PATH``: Path to DuckDB database file.
  Default: ~/.tradingstrategy/vaults/hyperliquid-vaults.duckdb
- ``VAULT_ADDRESSES``: Comma-separated vault addresses to heal.
  Default: all vaults in the database.
- ``DRY_RUN``: If "true", only detect without modifying data. Default: false
- ``RUN_PIPELINE``: If "true", run the cleaning pipeline after healing. Default: false
- ``SKIP_REFETCH``: If "true", skip the API re-fetch step. Default: false
- ``MAX_WORKERS``: Maximum parallel workers for API re-fetch. Default: 16
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
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase, run_daily_scan
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault_data_export import (
    merge_into_uncleaned_parquet,
    merge_into_vault_database,
)
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE

logger = logging.getLogger(__name__)


def _print_issues(issues_df):
    """Print a grouped summary of detected issues."""
    if issues_df.empty:
        print("  No issues detected.")
        return

    for issue_type, group in issues_df.groupby("issue_type"):
        print(f"\n  {issue_type} ({len(group)} vaults):")
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
        print(tabulate(table_data, headers=["Name", "Address", "Rows", "Example"], tablefmt="simple", stralign="left"))


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "info")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/heal-all-share-prices.log"),
    )

    db_path_str = os.environ.get("DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else HYPERLIQUID_DAILY_METRICS_DATABASE

    vault_addresses_str = os.environ.get("VAULT_ADDRESSES", "").strip()
    vault_addresses_filter = [a.strip().lower() for a in vault_addresses_str.split(",") if a.strip()] or None

    dry_run = os.environ.get("DRY_RUN", "").lower() == "true"
    run_pipeline = os.environ.get("RUN_PIPELINE", "").lower() == "true"
    skip_refetch = os.environ.get("SKIP_REFETCH", "").lower() == "true"
    max_workers = int(os.environ.get("MAX_WORKERS", "16"))

    vault_db_path_str = os.environ.get("VAULT_DB_PATH")
    vault_db_path = Path(vault_db_path_str).expanduser() if vault_db_path_str else DEFAULT_VAULT_DATABASE

    uncleaned_path_str = os.environ.get("PARQUET_PATH")
    uncleaned_path = Path(uncleaned_path_str).expanduser() if uncleaned_path_str else DEFAULT_UNCLEANED_PRICE_DATABASE

    print("Hyperliquid share price healer (all steps)")
    print(f"DuckDB path: {db_path}")
    print(f"Dry run: {dry_run}")
    print(f"Skip API re-fetch: {skip_refetch}")
    print()

    if not db_path.exists():
        print(f"Database not found at {db_path}. Nothing to heal.")
        return

    db = HyperliquidDailyMetricsDatabase(db_path)

    try:
        # ── Step 1: Detect ──────────────────────────────────────────────
        print("Step 1: Detecting broken vaults...")
        pre_issues = db.detect_broken_vaults()
        if vault_addresses_filter:
            pre_issues = pre_issues[pre_issues["vault_address"].isin(vault_addresses_filter)]

        _print_issues(pre_issues)
        pre_count = len(pre_issues)
        print(f"\n  Total: {pre_count} issues across {pre_issues['vault_address'].nunique()} vaults")

        if dry_run:
            print("\nDRY_RUN=true — no changes made.")
            return

        # ── Step 2: Offline recomputation ───────────────────────────────
        print("\n" + "=" * 60)
        print("Step 2: Offline recomputation (no API calls, no data deletion)")
        print("=" * 60)

        if vault_addresses_filter:
            metadata = db.get_all_vault_metadata()
            name_map = dict(zip(metadata["vault_address"], metadata["name"]))
            per_vault = {}
            for addr in vault_addresses_filter:
                result = db.recompute_vault_share_prices(addr)
                per_vault[addr] = {"name": name_map.get(addr, "<unknown>"), **result}
            db.save()
            summary = {
                "total_vaults": len(vault_addresses_filter),
                "vaults_with_changes": sum(1 for r in per_vault.values() if r["changed_rows"] > 0),
                "vaults_with_epoch_resets": sum(1 for r in per_vault.values() if r["epoch_resets"] > 0),
                "total_changed_rows": sum(r["changed_rows"] for r in per_vault.values()),
                "per_vault": per_vault,
            }
        else:
            summary = db.recompute_all_share_prices()

        print(f"  Vaults processed: {summary['total_vaults']}")
        print(f"  Vaults with changes: {summary['vaults_with_changes']}")
        print(f"  Vaults with epoch resets: {summary['vaults_with_epoch_resets']}")
        print(f"  Total changed rows: {summary['total_changed_rows']}")

        # Show changed vaults
        changed = {addr: info for addr, info in summary["per_vault"].items() if info["changed_rows"] > 0}
        if changed:
            print(f"\n  Changed vaults ({len(changed)}):")
            table_data = []
            for addr, info in sorted(changed.items(), key=lambda x: x[1]["changed_rows"], reverse=True)[:20]:
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
            if len(changed) > 20:
                print(f"  ... and {len(changed) - 20} more")

        # Check what remains after offline fix
        mid_issues = db.detect_broken_vaults()
        if vault_addresses_filter:
            mid_issues = mid_issues[mid_issues["vault_address"].isin(vault_addresses_filter)]

        mid_count = len(mid_issues)
        print(f"\n  Issues remaining after offline fix: {mid_count} (was {pre_count})")

        # ── Step 3: API re-fetch for stuck vaults ───────────────────────
        stuck_vaults = mid_issues[mid_issues["issue_type"] == "share_price_stuck_at_1"]
        stuck_addresses = stuck_vaults["vault_address"].unique().tolist()

        if skip_refetch:
            print("\n" + "=" * 60)
            print("Step 3: API re-fetch — SKIPPED (SKIP_REFETCH=true)")
            print("=" * 60)
        elif not stuck_addresses:
            print("\n" + "=" * 60)
            print("Step 3: API re-fetch — not needed (no stuck vaults)")
            print("=" * 60)
        else:
            print("\n" + "=" * 60)
            print(f"Step 3: API re-fetch for {len(stuck_addresses)} stuck vaults")
            print("=" * 60)
            print("  This deletes and re-fetches data for these vaults only.")
            print()

            stuck_names = dict(zip(stuck_vaults["vault_address"], stuck_vaults["name"]))
            for addr in stuck_addresses:
                deleted = db.delete_vault_daily_prices(addr)
                print(f"  Deleted {deleted} rows for {stuck_names.get(addr, addr[:10])}")
            db.save()
            db.close()
            db = None

            session = create_hyperliquid_session(requests_per_second=2.75)
            db = run_daily_scan(
                session=session,
                db_path=db_path,
                vault_addresses=stuck_addresses,
                max_workers=max_workers,
            )

            # Report re-fetch results
            print(f"\n  Re-fetched {len(stuck_addresses)} vaults:")
            for addr in stuck_addresses:
                prices = db.get_vault_daily_prices(addr)
                if prices.empty:
                    print(f"    {stuck_names.get(addr, addr[:10]):30s}  no data returned")
                else:
                    sp_min = prices["share_price"].min()
                    sp_max = prices["share_price"].max()
                    print(f"    {stuck_names.get(addr, addr[:10]):30s}  {len(prices)} rows, SP {sp_min:.4f}-{sp_max:.4f}")

        # ── Step 4: Verify ──────────────────────────────────────────────
        print("\n" + "=" * 60)
        print("Step 4: Verification")
        print("=" * 60)

        post_issues = db.detect_broken_vaults()
        if vault_addresses_filter:
            post_issues = post_issues[post_issues["vault_address"].isin(vault_addresses_filter)]

        post_count = len(post_issues)
        _print_issues(post_issues)

        print(f"\n  Before: {pre_count} issues")
        print(f"  After:  {post_count} issues")
        if post_count < pre_count:
            print(f"  Fixed:  {pre_count - post_count} issues")

        # ── Step 5: Pipeline ────────────────────────────────────────────
        if run_pipeline:
            print("\n" + "=" * 60)
            print("Step 5: Downstream cleaning pipeline")
            print("=" * 60)

            vault_db = merge_into_vault_database(db, vault_db_path)
            print(f"  VaultDatabase: {len(vault_db)} total vaults")

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
