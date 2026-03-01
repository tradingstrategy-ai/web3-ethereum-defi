"""Test historical TVL tracking for Lighter pools.

Verifies that the pipeline computes historical TVL from the PnL
endpoint's ``pool_total_shares`` combined with share prices, rather
than stamping the current TVL onto every historical row.
"""

import datetime

import pytest

from eth_defi.lighter.daily_metrics import (
    LighterDailyMetricsDatabase,
    fetch_and_store_pool,
)
from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.vault import (
    fetch_all_pools,
    fetch_pool_detail,
    fetch_pool_total_shares_history,
    pool_detail_to_daily_dataframe,
)


@pytest.mark.timeout(60)
def test_fetch_pool_total_shares_history(lighter_session, lighter_llp_pool):
    """Fetch historical total shares from the PnL endpoint for the LLP."""
    shares_by_date = fetch_pool_total_shares_history(
        lighter_session,
        lighter_llp_pool.account_index,
    )

    assert len(shares_by_date) > 100, f"Expected substantial history, got {len(shares_by_date)} entries"

    # All keys should be dates, all values positive ints
    for date_key, total_shares in shares_by_date.items():
        assert isinstance(date_key, datetime.date)
        assert isinstance(total_shares, int)
        assert total_shares >= 0

    # Shares should vary over time (LLP grew from small to large)
    values = list(shares_by_date.values())
    assert min(values) != max(values), "Total shares should vary over time"


@pytest.mark.timeout(60)
def test_historical_tvl_varies(lighter_session, lighter_llp_pool):
    """TVL computed from shares * share_price should vary across dates."""
    detail = fetch_pool_detail(lighter_session, lighter_llp_pool.account_index)
    shares_by_date = fetch_pool_total_shares_history(
        lighter_session,
        lighter_llp_pool.account_index,
    )
    daily_df = pool_detail_to_daily_dataframe(detail, total_shares_by_date=shares_by_date)

    assert not daily_df.empty
    assert "tvl" in daily_df.columns

    # TVL should not be constant — the whole point of the fix
    nonzero_tvl = daily_df[daily_df["tvl"] > 0]["tvl"]
    assert len(nonzero_tvl) > 50, f"Expected many rows with positive TVL, got {len(nonzero_tvl)}"
    assert nonzero_tvl.std() > 0, "TVL should vary over time, not be constant"

    # TVL should be roughly consistent with current total_asset_value
    # on the most recent date
    latest_tvl = daily_df["tvl"].iloc[-1]
    assert latest_tvl == pytest.approx(detail.total_asset_value, rel=0.05)


@pytest.mark.timeout(120)
def test_stored_tvl_varies(tmp_path):
    """Full pipeline: TVL stored in DuckDB should vary across dates."""
    duckdb_path = tmp_path / "tvl-test.duckdb"
    session = create_lighter_session()

    pools = fetch_all_pools(session)
    llp = [p for p in pools if p.is_llp][0]

    db = LighterDailyMetricsDatabase(duckdb_path)
    try:
        result = fetch_and_store_pool(session, db, llp)
        assert result
        db.save()

        daily_df = db.get_pool_daily_prices(llp.account_index)
        assert not daily_df.empty

        # TVL should vary in the stored data
        nonzero_tvl = daily_df[daily_df["tvl"] > 0]["tvl"]
        assert len(nonzero_tvl) > 50
        assert nonzero_tvl.std() > 0, "Stored TVL should vary over time"
        assert nonzero_tvl.min() < nonzero_tvl.max()
    finally:
        db.close()
