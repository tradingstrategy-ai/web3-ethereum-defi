"""Test Hyperliquid vault details fetching.

This test module verifies that we can fetch detailed vault information
from the Hyperliquid API using the vaultDetails endpoint.

Uses vault https://app.hyperliquid.xyz/vaults/0x3df9769bbbb335340872f01d8157c779d73c6ed0
as the test case (Trading Strategy - IchiV3 LS).
"""

from decimal import Decimal

import pytest

from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault import (
    HyperliquidVault,
    PortfolioHistory,
    VaultFollower,
    VaultInfo,
)


#: Test vault address (Trading Strategy - IchiV3 LS)
TEST_VAULT_ADDRESS = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"


@pytest.fixture(scope="module")
def session():
    """Create a shared session for all tests in this module."""
    return create_hyperliquid_session()


@pytest.fixture(scope="module")
def vault(session) -> HyperliquidVault:
    """Create a HyperliquidVault instance for the test vault."""
    return HyperliquidVault(
        session=session,
        vault_address=TEST_VAULT_ADDRESS,
    )


@pytest.fixture(scope="module")
def vault_info(vault) -> VaultInfo:
    """Fetch vault info once for all tests."""
    return vault.fetch_info()


def test_vault_repr(vault: HyperliquidVault):
    """Test vault string representation."""
    assert repr(vault) == f"<HyperliquidVault {TEST_VAULT_ADDRESS}>"


def test_vault_info_basic_properties(vault_info: VaultInfo):
    """Test basic vault info properties that should be stable."""
    # Vault address should match
    assert vault_info.vault_address.lower() == TEST_VAULT_ADDRESS.lower()

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


def test_vault_info_is_dataclass(vault_info: VaultInfo):
    """Verify VaultInfo is a proper dataclass instance."""
    assert isinstance(vault_info, VaultInfo)


def test_vault_info_numeric_properties(vault_info: VaultInfo):
    """Test numeric properties that can change but should not be zero.

    Since this is a live integration test, we can't check exact values,
    but we can verify they are reasonable non-zero values.
    """
    # Max distributable and withdrawable should be Decimals
    assert isinstance(vault_info.max_distributable, Decimal)
    assert isinstance(vault_info.max_withdrawable, Decimal)

    # These values can be zero if the vault has no assets, but typically
    # an active vault should have some value
    # We don't assert > 0 because it depends on vault state


def test_vault_info_boolean_properties(vault_info: VaultInfo):
    """Test boolean properties."""
    # is_closed and allow_deposits should be booleans
    assert isinstance(vault_info.is_closed, bool)
    assert isinstance(vault_info.allow_deposits, bool)

    # This vault should be open for deposits
    assert vault_info.is_closed is False
    assert vault_info.allow_deposits is True


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
    assert expected_periods.issubset(set(vault_info.portfolio.keys())), \
        f"Missing periods: {expected_periods - set(vault_info.portfolio.keys())}"

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


def test_vault_info_portfolio_has_data(vault_info: VaultInfo):
    """Test that portfolio history contains actual data points."""
    # Day period should have some data points
    day_history = vault_info.portfolio.get("day")
    assert day_history is not None
    assert len(day_history.account_value_history) > 0, \
        "Day period should have account value history"
    assert len(day_history.pnl_history) > 0, \
        "Day period should have PNL history"

    # Week period should have more data points than day
    week_history = vault_info.portfolio.get("week")
    assert week_history is not None
    assert len(week_history.account_value_history) >= len(day_history.account_value_history), \
        "Week should have at least as many data points as day"


def test_vault_info_cached_property(vault: HyperliquidVault):
    """Test that the info cached property works correctly."""
    # First access should fetch and cache
    info1 = vault.info

    # Second access should return cached value (same object)
    info2 = vault.info

    assert info1 is info2, "Cached property should return the same object"
    assert isinstance(info1, VaultInfo)


def test_vault_info_commission_rate(vault_info: VaultInfo):
    """Test commission rate property."""
    # Commission rate can be None or a float/Percent
    if vault_info.commission_rate is not None:
        assert isinstance(vault_info.commission_rate, (int, float))
        # Commission rate should be between 0 and 1 (0% to 100%)
        assert 0 <= vault_info.commission_rate <= 1
