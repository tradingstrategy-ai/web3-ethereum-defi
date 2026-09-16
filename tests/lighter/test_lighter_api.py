"""Test Lighter API client endpoints.

Verifies that we can fetch pool data from Lighter public endpoints.
No authentication required.
"""

import pytest

from eth_defi.erc_4626 import settlement_events as settlement_events_module
from eth_defi.lighter.api import fetch_lighter_withdrawal_delay
from eth_defi.lighter.session import LighterSession
from eth_defi.lighter.vault import (
    LighterPoolDetail,
    LighterPoolSummary,
    fetch_pool_daily_pnl_history,
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
def test_fetch_lighter_withdrawal_delay(lighter_session: LighterSession):
    """Read the live, dynamic secure-withdrawal delay.

    1. Query Lighter's public withdrawal-delay endpoint.
    2. Verify it returns a positive number of seconds for operator planning.
    """
    # 1. Query Lighter's public withdrawal-delay endpoint.
    delay = fetch_lighter_withdrawal_delay(lighter_session)

    # 2. Verify it returns a positive number of seconds for operator planning.
    assert delay > 0


def test_should_use_hypersync_requires_installed_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """Automatic event reads fall back to RPC when Hypersync is unavailable.

    1. Configure the environment to request Hypersync with an API key.
    2. Represent the optional Hypersync package as unavailable.
    3. Verify the reader chooses the safe JSON-RPC fallback.
    """
    # 1. Configure the environment to request Hypersync with an API key.
    monkeypatch.setenv("HYPERSYNC_API_KEY", "test-key")
    monkeypatch.setenv("USE_HYPERSYNC", "true")

    # 2. Represent the optional Hypersync package as unavailable.
    monkeypatch.setattr(settlement_events_module, "hypersync", None)

    # 3. Verify the reader chooses the safe JSON-RPC fallback.
    assert not settlement_events_module.should_use_hypersync()


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
    assert detail.total_shares > 0
    assert detail.operator_shares >= 0
    assert detail.snapshot.total_shares == detail.total_shares
    assert detail.snapshot.operator_shares == detail.operator_shares
    assert detail.snapshot.total_asset_value == pytest.approx(detail.total_asset_value)
    assert detail.snapshot.position_count is not None
    assert detail.snapshot.source_account["positions"] is not None
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


@pytest.mark.timeout(60)
def test_fetch_pool_daily_pnl_history(lighter_session, lighter_llp_pool):
    """Fetch source shares and cumulative USDC flow counters for the LLP."""
    history = fetch_pool_daily_pnl_history(lighter_session, lighter_llp_pool.account_index)

    assert len(history) > 100
    latest = history[max(history)]
    assert latest.total_shares is not None
    assert latest.total_shares > 0
    assert latest.cumulative_pool_inflow is not None
    assert latest.cumulative_pool_outflow is not None
    assert latest.cumulative_pool_inflow >= 0
    assert latest.cumulative_pool_outflow >= 0
    assert latest.trade_pnl is not None
    assert latest.pool_pnl is not None
    assert latest.volume is not None
