"""Test Lighter API client endpoints.

Verifies that we can fetch pool data from Lighter public endpoints.
No authentication required.
"""

import pytest

from eth_defi.lighter.vault import (
    LighterPoolDetail,
    LighterPoolSummary,
    fetch_all_pools,
    fetch_pool_detail,
    fetch_system_config,
    pool_detail_to_daily_dataframe,
)


@pytest.mark.timeout(30)
def test_system_config(lighter_session):
    """Test that we can fetch system configuration."""
    config = fetch_system_config(lighter_session)
    assert "liquidity_pool_index" in config
    assert isinstance(config["liquidity_pool_index"], int)
    assert config["liquidity_pool_index"] > 0


@pytest.mark.timeout(30)
def test_fetch_all_pools(lighter_pool_listing):
    """Test that we can list all public pools."""
    pools = lighter_pool_listing
    assert len(pools) > 0

    pool = pools[0]
    assert isinstance(pool, LighterPoolSummary)
    assert pool.account_index > 0
    assert pool.name

    # Exactly one pool should be the LLP
    llp_pools = [p for p in pools if p.is_llp]
    assert len(llp_pools) == 1
    assert llp_pools[0].total_asset_value > 0


@pytest.mark.timeout(60)
def test_fetch_pool_detail(lighter_session, lighter_llp_pool):
    """Test fetching detailed data for the LLP pool."""
    detail = fetch_pool_detail(lighter_session, lighter_llp_pool.account_index)

    assert isinstance(detail, LighterPoolDetail)
    assert detail.account_index == lighter_llp_pool.account_index
    assert detail.total_asset_value > 0
    assert len(detail.share_prices) > 0
    assert len(detail.daily_returns) > 0
    # LLP should have substantial history
    assert len(detail.share_prices) > 100


@pytest.mark.timeout(60)
def test_pool_detail_to_daily_dataframe(lighter_session, lighter_llp_pool):
    """Test converting pool detail to a daily DataFrame."""
    detail = fetch_pool_detail(lighter_session, lighter_llp_pool.account_index)
    daily_df = pool_detail_to_daily_dataframe(detail)

    assert not daily_df.empty
    assert "share_price" in daily_df.columns
    assert "daily_return" in daily_df.columns
    assert len(daily_df) >= 2
    assert (daily_df["share_price"] > 0).all()
