"""Heal corrupted Hyperliquid vault share prices in the DuckDB database.

Problem
-------

The vault **pmalt** (``0x4dec0a851849056e259128464ef28ce78afa27f6``) on Hyperliquid
reported 0% returns across all time periods on TradingStrategy, despite the
Hyperliquid API showing an APR of 185.7% and followers with real profits.

Root cause: the ``_calculate_share_price()`` function in
``eth_defi.hyperliquid.combined_analysis`` lacked epoch-reset logic for
**total_supply wipeout cycles**. When all followers withdrew from a vault,
``total_supply`` dropped to zero, but ``total_assets`` remained nonzero
(the vault leader's equity). The computed share price overflowed and hit the
hard cap of 10,000. When new deposits arrived, shares were minted at this
inflated price, keeping ``total_supply`` permanently tiny relative to
``total_assets``. The share price stayed pinned at 10,000, making all
downstream return calculations show 0%.

Fix
---

The ``_calculate_share_price()`` function now detects when ``total_supply``
is zero or negligibly small (share price would exceed
``SHARE_PRICE_RESET_THRESHOLD = 10,000``). In those cases it performs an
**epoch reset**: ``total_supply`` is set to ``total_assets`` and
``share_price`` resets to 1.0, absorbing the leader's residual equity into
a fresh share base.

This script
-----------

Re-processes Hyperliquid vaults in the DuckDB by re-fetching their portfolio
history from the Hyperliquid API and recomputing share prices with the fixed
``_calculate_share_price()`` logic.

1. Opens the existing Hyperliquid DuckDB
2. Lists all vaults (or a specific subset)
3. Detects affected vaults: share price stuck at >= 9,999 for recent entries
4. Re-fetches portfolio history and recomputes share prices via ``run_daily_scan()``
5. Reports before/after share price ranges per vault
6. Outputs a summary: ``Healed N of M vaults (K already healthy)``
7. Optionally re-runs the downstream cleaning pipeline

Manual verification
-------------------

After healing, verify the pmalt vault's share prices are no longer stuck:

.. code-block:: shell

    poetry run python -c "
    from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
    db = HyperliquidDailyMetricsDatabase()
    try:
        prices = db.get_vault_daily_prices('0x4dec0a851849056e259128464ef28ce78afa27f6')
        print(prices[['date', 'share_price', 'tvl']].tail(20).to_string())
        recent = prices.tail(10)
        stuck = (recent['share_price'] >= 9999.0).all()
        print(f'Share price stuck at cap: {stuck}')
        print(f'Recent share price range: {recent[\"share_price\"].min():.2f} - {recent[\"share_price\"].max():.2f}')
    finally:
        db.close()
    "

Expected: recent share prices vary between ~1.0 and ~5.0 (reflecting actual
PnL), not stuck at 10,000.

Usage
-----

.. code-block:: shell

    # Dry run: diagnose without modifying data
    DRY_RUN=true poetry run python scripts/hyperliquid/heal-share-prices.py

    # Heal all affected vaults
    poetry run python scripts/hyperliquid/heal-share-prices.py

    # Heal a specific vault
    VAULT_ADDRESSES=0x4dec0a851849056e259128464ef28ce78afa27f6 \\
      poetry run python scripts/hyperliquid/heal-share-prices.py

    # Heal and re-run downstream cleaning pipeline
    RUN_PIPELINE=true poetry run python scripts/hyperliquid/heal-share-prices.py

Environment variables:

- ``LOG_LEVEL``: Logging level (debug, info, warning, error). Default: info
- ``DB_PATH``: Path to DuckDB database file.
  Default: ~/.tradingstrategy/vaults/hyperliquid-vaults.duckdb
- ``VAULT_ADDRESSES``: Comma-separated vault addresses to heal.
  Default: all vaults in the database.
- ``DRY_RUN``: If "true", only diagnose without modifying data. Default: false
- ``RUN_PIPELINE``: If "true", run the cleaning pipeline after healing. Default: false
- ``MAX_WORKERS``: Maximum parallel workers. Default: 16
- ``VAULT_DB_PATH``: Path to VaultDatabase pickle.
  Default: ~/.tradingstrategy/vaults/vault-metadata-db.pickle
- ``PARQUET_PATH``: Path to uncleaned Parquet.
  Default: ~/.tradingstrategy/vaults/vault-prices-1h.parquet

"""

import logging
import os
from pathlib import Path

from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID, HYPERLIQUID_DAILY_METRICS_DATABASE
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

#: A vault is considered "stuck" if its most recent share prices
#: are all at or above this threshold.
STUCK_SHARE_PRICE_THRESHOLD = 9_999.0

#: Minimum number of recent rows to check for stuck detection
STUCK_CHECK_ROWS = 5


def detect_stuck_vaults(db: HyperliquidDailyMetricsDatabase) -> dict[str, dict]:
    """Detect vaults with share prices stuck at the cap.

    :return:
        Dict mapping vault_address to diagnosis info:
        ``{"name": str, "last_date": date, "recent_min": float, "recent_max": float, "stuck": bool}``
    """
    metadata = db.get_all_vault_metadata()
    results = {}

    for _, row in metadata.iterrows():
        vault_address = row["vault_address"]
        name = row["name"]

        prices = db.get_vault_daily_prices(vault_address)
        if prices.empty or len(prices) < STUCK_CHECK_ROWS:
            results[vault_address] = {
                "name": name,
                "last_date": None,
                "recent_min": 0.0,
                "recent_max": 0.0,
                "stuck": False,
                "rows": len(prices),
            }
            continue

        recent = prices.tail(STUCK_CHECK_ROWS)
        recent_min = recent["share_price"].min()
        recent_max = recent["share_price"].max()
        stuck = (recent["share_price"] >= STUCK_SHARE_PRICE_THRESHOLD).all()

        results[vault_address] = {
            "name": name,
            "last_date": prices["date"].max(),
            "recent_min": recent_min,
            "recent_max": recent_max,
            "stuck": stuck,
            "rows": len(prices),
        }

    return results


def main():
    default_log_level = os.environ.get("LOG_LEVEL", "info")
    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path("logs/heal-share-prices.log"),
    )

    db_path_str = os.environ.get("DB_PATH")
    db_path = Path(db_path_str).expanduser() if db_path_str else HYPERLIQUID_DAILY_METRICS_DATABASE

    vault_addresses_str = os.environ.get("VAULT_ADDRESSES", "").strip()
    vault_addresses_filter = [a.strip().lower() for a in vault_addresses_str.split(",") if a.strip()] or None

    dry_run = os.environ.get("DRY_RUN", "").lower() == "true"
    run_pipeline = os.environ.get("RUN_PIPELINE", "").lower() == "true"
    max_workers = int(os.environ.get("MAX_WORKERS", "16"))

    vault_db_path_str = os.environ.get("VAULT_DB_PATH")
    vault_db_path = Path(vault_db_path_str).expanduser() if vault_db_path_str else DEFAULT_VAULT_DATABASE

    uncleaned_path_str = os.environ.get("PARQUET_PATH")
    uncleaned_path = Path(uncleaned_path_str).expanduser() if uncleaned_path_str else DEFAULT_UNCLEANED_PRICE_DATABASE

    print(f"Hyperliquid share price healer")
    print(f"DuckDB path: {db_path}")
    print(f"Dry run: {dry_run}")
    print()

    if not db_path.exists():
        print(f"Database not found at {db_path}. Nothing to heal.")
        return

    # Step 1: Diagnose
    print("Step 1: Diagnosing vaults...")
    db = HyperliquidDailyMetricsDatabase(db_path)
    try:
        diagnosis = detect_stuck_vaults(db)
    finally:
        db.close()

    # Filter to requested vaults if specified
    if vault_addresses_filter:
        diagnosis = {addr: info for addr, info in diagnosis.items() if addr in vault_addresses_filter}

    total_vaults = len(diagnosis)
    stuck_vaults = {addr: info for addr, info in diagnosis.items() if info["stuck"]}
    healthy_vaults = total_vaults - len(stuck_vaults)

    # When specific vault addresses are explicitly requested, heal all of them
    # regardless of stuck detection. This handles cases where a partial heal
    # made recent rows look healthy but older rows still have corrupted values.
    force_heal = vault_addresses_filter is not None
    if force_heal:
        vaults_to_heal = diagnosis
    else:
        vaults_to_heal = stuck_vaults

    print(f"Total vaults examined: {total_vaults}")
    print(f"Stuck at cap (auto-detected): {len(stuck_vaults)}")
    print(f"Already healthy: {healthy_vaults}")
    if force_heal and len(vaults_to_heal) > len(stuck_vaults):
        print(f"Force healing all {len(vaults_to_heal)} requested vaults (VAULT_ADDRESSES set)")
    print()

    if vaults_to_heal:
        print("Vaults to heal:")
        for addr, info in vaults_to_heal.items():
            status = "STUCK" if info["stuck"] else "force"
            print(f"  {info['name']:30s}  {addr}  SP range: {info['recent_min']:.0f}-{info['recent_max']:.0f}  rows: {info['rows']}  [{status}]")
        print()

    if dry_run:
        print("DRY_RUN=true — no changes made.")
        return

    if not vaults_to_heal:
        print("No vaults need healing.")
        return

    # Step 2: Delete old corrupted prices, then re-scan with fixed code.
    # The old daily prices may include rows from previous scans at different
    # granularities (day/week snapshots) that won't be overwritten by the
    # allTime re-scan. Deleting first ensures a clean slate.
    addresses_to_heal = list(vaults_to_heal.keys())
    print(f"Step 2: Deleting old prices and re-scanning {len(addresses_to_heal)} vaults...")

    db = HyperliquidDailyMetricsDatabase(db_path)
    for addr in addresses_to_heal:
        deleted = db.delete_vault_daily_prices(addr)
        print(f"  Deleted {deleted} old rows for {vaults_to_heal[addr]['name']}")
    db.save()
    db.close()

    session = create_hyperliquid_session(requests_per_second=2.75)
    db = run_daily_scan(
        session=session,
        db_path=db_path,
        vault_addresses=addresses_to_heal,
        max_workers=max_workers,
    )

    # Step 3: Verify healing
    print(f"\nStep 3: Verifying healing...")
    healed_count = 0
    still_stuck_count = 0
    try:
        for addr, before_info in vaults_to_heal.items():
            prices = db.get_vault_daily_prices(addr)
            if prices.empty or len(prices) < STUCK_CHECK_ROWS:
                still_stuck_count += 1
                continue

            recent = prices.tail(STUCK_CHECK_ROWS)
            after_min = recent["share_price"].min()
            after_max = recent["share_price"].max()
            still_stuck = (recent["share_price"] >= STUCK_SHARE_PRICE_THRESHOLD).all()

            status = "HEALED" if not still_stuck else "STILL STUCK"
            print(f"  {before_info['name']:30s}  before: {before_info['recent_min']:.0f}-{before_info['recent_max']:.0f}  after: {after_min:.2f}-{after_max:.2f}  [{status}]")

            if still_stuck:
                still_stuck_count += 1
            else:
                healed_count += 1
    finally:
        # Step 4: Optionally run pipeline
        if run_pipeline:
            print(f"\nStep 4: Running downstream pipeline...")
            vault_db = merge_into_vault_database(db, vault_db_path)
            print(f"VaultDatabase now has {len(vault_db)} total vaults")

            combined_df = merge_into_uncleaned_parquet(db, uncleaned_path)
            hl_rows = combined_df[combined_df["chain"] == HYPERCORE_CHAIN_ID] if len(combined_df) > 0 else combined_df
            print(f"Uncleaned parquet: {len(combined_df):,} total rows ({len(hl_rows):,} Hyperliquid)")

            db.close()

            generate_cleaned_vault_datasets(
                vault_db_path=vault_db_path,
                price_df_path=uncleaned_path,
            )
        else:
            db.close()

    print(f"\nHealed {healed_count} of {len(vaults_to_heal)} vaults ({healthy_vaults} already healthy)")
    if still_stuck_count > 0:
        print(f"WARNING: {still_stuck_count} vaults are still stuck — may need manual investigation")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
