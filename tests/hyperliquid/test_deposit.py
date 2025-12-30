"""Test Hyperliquid vault deposit and withdrawal analysis.

This test module verifies the deposit.py module functions for fetching
and analysing vault deposit/withdrawal history.

Uses the same test vault as other Hyperliquid tests for consistency.
"""

from datetime import datetime
from decimal import Decimal

import pandas as pd
import pytest

from eth_defi.hyperliquid.deposit import VaultDepositEvent, VaultEventType, create_deposit_dataframe, fetch_vault_deposits, get_deposit_summary
from eth_defi.hyperliquid.session import create_hyperliquid_session

#: Test vault address (Trading Strategy - IchiV3 LS)
TEST_VAULT_ADDRESS = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

#: Fixed test time range start
TEST_START_TIME = datetime(2025, 12, 1)

#: Fixed test time range end
TEST_END_TIME = datetime(2025, 12, 28)


@pytest.fixture(scope="module")
def session():
    """Create a shared session for all tests in this module."""
    return create_hyperliquid_session()


@pytest.fixture(scope="module")
def vault_events(session) -> list[VaultDepositEvent]:
    """Fetch deposit/withdrawal events for the test vault."""
    events = list(
        fetch_vault_deposits(
            session,
            TEST_VAULT_ADDRESS,
            start_time=TEST_START_TIME,
            end_time=TEST_END_TIME,
        )
    )
    return events


def test_create_dataframe(vault_events: list[VaultDepositEvent]):
    """Test DataFrame creation from events."""
    df = create_deposit_dataframe(vault_events)

    if not vault_events:
        assert df.empty
        return

    assert isinstance(df, pd.DataFrame)
    assert len(df) == len(vault_events)
    assert "event_type" in df.columns
    assert "vault_address" in df.columns
    assert "usdc" in df.columns


def test_get_summary(vault_events: list[VaultDepositEvent]):
    """Test summary generation."""
    summary = get_deposit_summary(vault_events)

    assert isinstance(summary, dict)
    assert "total_events" in summary
    assert "deposits" in summary
    assert "withdrawals" in summary
    assert "total_deposited" in summary
    assert "total_withdrawn" in summary
    assert "net_flow" in summary

    assert summary["total_events"] == len(vault_events)


def test_fetch_with_time_range(session):
    """Test fetching with specific time range."""
    start_time = datetime(2025, 12, 15)
    end_time = datetime(2025, 12, 20)

    events = list(
        fetch_vault_deposits(
            session,
            TEST_VAULT_ADDRESS,
            start_time=start_time,
            end_time=end_time,
        )
    )

    # All events should be within the time range
    for event in events:
        assert start_time <= event.timestamp <= end_time, "All events should be within the specified time range"


def test_fetch_empty_result(session):
    """Test fetching with a time range that has no events."""
    start_time = datetime(2019, 1, 1)
    end_time = datetime(2020, 1, 1)

    events = list(
        fetch_vault_deposits(
            session,
            TEST_VAULT_ADDRESS,
            start_time=start_time,
            end_time=end_time,
        )
    )

    assert events == [], "Should return empty list for time range with no events"


def test_summary_values(vault_events: list[VaultDepositEvent]):
    """Test that summary contains expected values for the test period."""
    summary = get_deposit_summary(vault_events)

    # Test known values for this vault in this time period
    assert summary["deposits"] == 8, f"Expected 8 deposits, got {summary['deposits']}"
    assert summary["total_deposited"] == pytest.approx(3650.0), f"Expected 3650 USDC deposited"
    assert summary["net_flow"] == pytest.approx(3650.0), "Net flow should equal total deposited when no withdrawals"
