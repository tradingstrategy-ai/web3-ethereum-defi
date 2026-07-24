"""Test Lighter daily metrics pipeline.

Verifies that we can scan Lighter pools and store metrics in DuckDB.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.lighter.constants import LIGHTER_DEPLOYMENTS, LighterAPIConfig
from eth_defi.lighter.daily_metrics import (
    LighterDailyMetricsDatabase,
    fetch_and_store_pool,
    run_daily_scan,
)
from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.vault import LighterPoolSummary, fetch_all_pools
from eth_defi.lighter.vault_data_export import merge_into_vault_database
from eth_defi.perp_dex.storage import read_perp_vault_observations
from eth_defi.research.vault_metrics import (
    calculate_hourly_returns_for_all_vaults,
    calculate_lifetime_metrics,
    export_lifetime_row,
)
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.vault.post_processing import merge_native_protocols
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.mark.timeout(120)
def test_fetch_and_store_single_pool(tmp_path):
    """Fetch LLP pool and store in DuckDB."""
    duckdb_path = tmp_path / "lighter-metrics.duckdb"
    session = create_lighter_session()

    pools = fetch_all_pools(session)
    llp = next(pool for pool in pools if pool.is_llp)

    db = LighterDailyMetricsDatabase(duckdb_path)
    try:
        result = fetch_and_store_pool(session, db, llp)
        assert result
        db.save()

        assert db.get_pool_count() == 1
        assert db.get_pool_daily_price_count(llp.account_index) > 0

        daily_df = db.get_pool_daily_prices(llp.account_index)
        assert not daily_df.empty
        assert (daily_df["share_price"] > 0).all()

        # Verify written_at is filled for all rows
        assert "written_at" in daily_df.columns, "written_at column missing from daily prices"
        assert daily_df["written_at"].notna().all(), "written_at should be filled for all rows"

        # Verify metadata was stored
        metadata_df = db.get_all_pool_metadata()
        assert len(metadata_df) == 1
        assert metadata_df.iloc[0]["is_llp"]
    finally:
        db.close()


@pytest.mark.timeout(180)
def test_run_daily_scan_small(tmp_path):
    """Run a small daily scan with TVL filter."""
    duckdb_path = tmp_path / "lighter-scan.duckdb"
    session = create_lighter_session()

    db = run_daily_scan(
        session,
        db_path=duckdb_path,
        min_tvl=100_000,
        max_pools=5,
        max_workers=4,
    )
    try:
        assert db.get_pool_count() > 0
        metadata_df = db.get_all_pool_metadata()
        assert len(metadata_df) > 0
    finally:
        db.close()


@pytest.mark.slow
@pytest.mark.timeout(180)
@pytest.mark.parametrize("deployment", LIGHTER_DEPLOYMENTS, ids=lambda deployment: deployment.slug)
def test_live_lighter_perp_metrics_reach_cleaned_parquet_and_json(tmp_path: Path, deployment: LighterAPIConfig) -> None:  # noqa: PLR0914
    """Collect a live Lighter pool and export its account metrics per deployment.

    The test selects one currently listed public pool, runs the production
    daily scanner, verifies the persisted common observation, then executes
    the native merge, cleaning and final JSON-record stages without mocks.

    :param tmp_path:
        Isolated pytest directory for the live DuckDB and Parquet artefacts.
    :param deployment:
        Public Lighter deployment selected by parametrisation.
    """
    rate_limit_db_path = tmp_path / f"lighter-{deployment.slug}-rate-limit.sqlite"
    session = create_lighter_session(deployment=deployment, rate_limit_db_path=rate_limit_db_path)
    pools = fetch_all_pools(session)
    target_pool: LighterPoolSummary = max(pools, key=lambda pool: pool.total_asset_value)
    expected_address = deployment.format_pool_address(target_pool.account_index)
    database_path = tmp_path / f"lighter-{deployment.slug}.duckdb"
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    uncleaned_path = tmp_path / "vault-prices-1h.parquet"
    cleaned_path = tmp_path / "cleaned-vault-prices-1h.parquet"

    database = run_daily_scan(
        session,
        db_path=database_path,
        pool_indices=[target_pool.account_index],
        max_workers=1,
        timeout=30.0,
    )
    try:
        accounts, positions = read_perp_vault_observations(database.con)
        account_rows = accounts[accounts["dataset_address"] == expected_address]
        assert len(account_rows) == 1
        assert account_rows.iloc[0]["position_data_status"] == "available"
        assert bool(account_rows.iloc[0]["position_set_complete"])
        assert pd.notna(account_rows.iloc[0]["observed_at"])
        assert set(positions["snapshot_id"]).issubset(set(account_rows["snapshot_id"]))
        merge_into_vault_database(database, vault_db_path)
    finally:
        database.close()

    merge_steps = merge_native_protocols(
        merge_lighter=True,
        uncleaned_parquet_path=uncleaned_path,
        lighter_db_path=database_path,
    )
    assert merge_steps["lighter-price-merge"]

    raw_prices = pd.read_parquet(uncleaned_path)
    raw_vault_rows = raw_prices[raw_prices["address"] == expected_address]
    assert not raw_vault_rows.empty
    assert raw_vault_rows["perp_position_data_status"].eq("available").any()
    assert raw_vault_rows["perp_metrics_observed_at"].notna().any()

    generate_cleaned_vault_datasets(
        vault_db_path=vault_db_path,
        price_df_path=uncleaned_path,
        cleaned_price_df_path=cleaned_path,
    )
    cleaned_prices = pd.read_parquet(cleaned_path)
    cleaned_vault_rows = cleaned_prices[cleaned_prices["address"] == expected_address]
    assert not cleaned_vault_rows.empty
    latest_cleaned_row = cleaned_vault_rows.sort_values("timestamp").iloc[-1]
    assert latest_cleaned_row["perp_position_data_status"] == "available"
    assert latest_cleaned_row["perp_quote_asset"] == deployment.denomination
    assert pd.notna(latest_cleaned_row["perp_metrics_observed_at"])

    vault_db = VaultDatabase.read(vault_db_path)
    returns = calculate_hourly_returns_for_all_vaults(cleaned_prices)
    lifetime_metrics = calculate_lifetime_metrics(returns, vault_db)
    assert len(lifetime_metrics) == 1
    exported = export_lifetime_row(lifetime_metrics.iloc[0])
    json.dumps(exported, allow_nan=False)
    perp_dex = exported["other_data"]["perp_dex"]
    assert perp_dex["position_data_status"] == "available"
    assert perp_dex["quote_asset"] == deployment.denomination
    assert perp_dex["observed_at"] is not None
