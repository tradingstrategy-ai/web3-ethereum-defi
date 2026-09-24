"""Test Hyperliquid vault details fetching.

This test module verifies that we can fetch detailed vault information
from the Hyperliquid API using the vaultDetails endpoint.

Uses vault https://app.hyperliquid.xyz/vaults/0x3df9769bbbb335340872f01d8157c779d73c6ed0
as the test case (Trading Strategy - IchiV3 LS).
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from eth_defi.hyperliquid.vault import HyperliquidVault, PortfolioHistory, VaultFollower, VaultInfo, classify_hyperliquid_vault_deposit


def test_missing_vault_details_flags_remain_unknown():
    """Do not silently turn an incomplete API response into an open vault.

    1. Parse a minimal valid ``vaultDetails`` payload with no deposit flags.
    2. Verify both flags and the resulting public permission stay unknown.
    """
    # 1. Parsing needs no HTTP call; the mock only supplies the session argument.
    vault = HyperliquidVault(session=MagicMock(), vault_address="0x0000000000000000000000000000000000000001")
    info = vault._parse_vault_details(
        {
            "name": "Missing flags",
            "vaultAddress": vault.vault_address,
            "leader": "0x0000000000000000000000000000000000000002",
        }
    )

    # 2. Missing flags remain unknown for the caller to handle.
    assert info.is_closed is None
    assert info.allow_deposits is None
    status = classify_hyperliquid_vault_deposit(info.is_closed, info.allow_deposits)
    assert status.deposits_open is None
    assert status.closed_reason is None


@pytest.fixture(scope="module")
def vault(session, hyperliquid_sample_vault) -> HyperliquidVault:
    """Create a HyperliquidVault instance for the test vault."""
    return HyperliquidVault(
        session=session,
        vault_address=hyperliquid_sample_vault,
    )


@pytest.fixture(scope="module")
def vault_info(vault) -> VaultInfo:
    """Fetch vault info once for all tests."""
    return vault.fetch_metadata()


def test_vault_info_basic_properties(vault_info: VaultInfo, hyperliquid_sample_vault):
    """Test basic vault info properties that should be stable."""
    # Vault address should match
    assert vault_info.vault_address.lower() == hyperliquid_sample_vault.lower()

    # Name should be set
    assert vault_info.name == "Trading Strategy - IchiV3 LS"

    # Leader address should be valid
    assert vault_info.leader.startswith("0x")
    assert len(vault_info.leader) == 42
    assert vault_info.leader.lower() == "0x6389c448e4adebf770590142b685402449b9eab2"

    # Description should be set
    assert vault_info.description, "Vault should have a description"

    # Relationship type should be normal for this vault
    assert vault_info.relationship_type == "normal"

    # No parent vault for this normal vault
    assert vault_info.parent is None


def test_vault_info_followers(vault_info: VaultInfo):
    """Test followers list structure."""
    # Followers should be a list
    assert isinstance(vault_info.followers, list)

    # If there are followers, verify their structure
    if vault_info.followers:
        follower = vault_info.followers[0]
        assert isinstance(follower, VaultFollower)
        assert follower.user.startswith("0x")
        assert len(follower.user) == 42
        assert isinstance(follower.vault_equity, Decimal)
        assert isinstance(follower.pnl, Decimal)
        assert isinstance(follower.all_time_pnl, Decimal)
        assert isinstance(follower.days_following, int)
        assert isinstance(follower.vault_entry_time, int)
        assert follower.vault_entry_time > 0


def test_vault_info_portfolio_history(vault_info: VaultInfo):
    """Test portfolio history structure."""
    # Portfolio should be a dict
    assert isinstance(vault_info.portfolio, dict)

    # Should have standard time periods
    expected_periods = {"day", "week", "month", "allTime"}
    assert expected_periods.issubset(set(vault_info.portfolio.keys())), f"Missing periods: {expected_periods - set(vault_info.portfolio.keys())}"

    # Check each period's structure
    for period_name, history in vault_info.portfolio.items():
        assert isinstance(history, PortfolioHistory)
        assert history.period == period_name

        # Account value history should be a list of tuples
        assert isinstance(history.account_value_history, list)
        if history.account_value_history:
            ts, value = history.account_value_history[0]
            from datetime import datetime

            assert isinstance(ts, datetime)
            assert isinstance(value, Decimal)

        # PNL history should be a list of tuples
        assert isinstance(history.pnl_history, list)
        if history.pnl_history:
            ts, pnl = history.pnl_history[0]
            assert isinstance(ts, datetime)
            assert isinstance(pnl, Decimal)

        # Volume should be a Decimal
        assert isinstance(history.volume, Decimal)
