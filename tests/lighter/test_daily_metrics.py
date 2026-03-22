"""Test Lighter daily metrics pipeline.

Verifies that we can scan Lighter pools and store metrics in DuckDB.
"""

import pytest

from eth_defi.lighter.daily_metrics import (
    LighterDailyMetricsDatabase,
    fetch_and_store_pool,
    run_daily_scan,
)
from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.vault import fetch_all_pools


@pytest.mark.timeout(120)
def test_fetch_and_store_single_pool(tmp_path):
    """Fetch LLP pool and store in DuckDB."""
    duckdb_path = tmp_path / "lighter-metrics.duckdb"
    session = create_lighter_session()

    pools = fetch_all_pools(session)
    llp = [p for p in pools if p.is_llp][0]

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
