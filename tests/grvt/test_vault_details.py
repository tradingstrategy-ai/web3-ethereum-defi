"""Test GRVT vault details fetching.

This test module verifies that we can fetch detailed vault information
from the GRVT public endpoints:

- Vault listing via the GraphQL API at ``edge.grvt.io/query``
  (includes per-vault fees)
- Vault details, performance, risk metrics, and share price history
  from the market data API at ``market-data.grvt.io``

No authentication required — all endpoints are public.
"""

import os

import pytest

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(CI, reason="GRVT endpoints are behind Cloudflare which blocks CI runners")

from eth_defi.grvt.vault import (
    GRVTVaultPerformance,
    GRVTVaultRiskMetric,
    GRVTVaultSummary,
    fetch_vault_details,
    fetch_vault_listing_graphql,
    fetch_vault_performance,
    fetch_vault_risk_metrics,
    fetch_vault_summary_history,
)


def test_vault_listing_graphql(grvt_session):
    """Test that we can discover vaults via the GraphQL API with fee data."""
    vaults = fetch_vault_listing_graphql(grvt_session, only_discoverable=True)

    assert len(vaults) > 0, "Expected at least one discoverable vault"

    vault = vaults[0]
    assert isinstance(vault, GRVTVaultSummary)
    assert vault.vault_id.startswith("VLT:")
    assert vault.chain_vault_id > 0
    assert vault.name
    assert vault.discoverable is True

    # GraphQL listing should include per-vault fee data
    assert vault.management_fee is not None, "Expected management_fee from GraphQL"
    assert vault.performance_fee is not None, "Expected performance_fee from GraphQL"
    assert 0.0 <= vault.management_fee <= 0.10  # 0-10%
    assert 0.0 <= vault.performance_fee <= 0.50  # 0-50%


def test_vault_details_and_performance(grvt_session, grvt_vault_listing):
    """Test fetching vault details and performance from market data API."""
    chain_ids = [v.chain_vault_id for v in grvt_vault_listing[:3]]

    details = fetch_vault_details(grvt_session, chain_ids)
    assert len(details) > 0, "Expected at least one vault detail"

    for cid, d in details.items():
        assert isinstance(cid, int)
        assert "total_equity" in d
        assert "share_price" in d

    perf = fetch_vault_performance(grvt_session, chain_ids)
    assert len(perf) > 0, "Expected at least one vault performance"

    for cid, p in perf.items():
        assert isinstance(p, GRVTVaultPerformance)
        assert isinstance(p.apr, float)

    risk = fetch_vault_risk_metrics(grvt_session, chain_ids)
    assert len(risk) > 0, "Expected at least one vault risk metric"

    for cid, r in risk.items():
        assert isinstance(r, GRVTVaultRiskMetric)
        assert isinstance(r.max_drawdown, float)


def test_vault_summary_history(grvt_session, grvt_sample_vault):
    """Test fetching share price history for a vault."""
    daily_df = fetch_vault_summary_history(
        grvt_session,
        chain_vault_id=grvt_sample_vault.chain_vault_id,
    )

    assert not daily_df.empty, f"Expected share price history for {grvt_sample_vault.name}"
    assert "share_price" in daily_df.columns
    assert "daily_return" in daily_df.columns
    assert len(daily_df) >= 2
    assert (daily_df["share_price"] > 0).all()
