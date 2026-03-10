"""Test Hyperliquid vault deposit and withdrawal analysis.

This test module verifies the deposit.py module functions for fetching
and analysing vault deposit/withdrawal history.

Uses the same test vault as other Hyperliquid tests for consistency.
"""

from datetime import datetime

import pandas as pd
import pytest

from eth_defi.hyperliquid.deposit import VaultDepositEvent, VaultEventType, aggregate_daily_flows, create_deposit_dataframe, fetch_vault_deposits, get_deposit_summary


@pytest.fixture(scope="module")
def vault_events(session, hyperliquid_sample_vault, hyperliquid_test_period_start, hyperliquid_test_period_end) -> list[VaultDepositEvent]:
    """Fetch deposit/withdrawal events for the test vault."""
    events = list(
        fetch_vault_deposits(
            session,
            hyperliquid_sample_vault,
            start_time=hyperliquid_test_period_start,
            end_time=hyperliquid_test_period_end,
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


def test_fetch_with_time_range(session, hyperliquid_sample_vault):
    """Test fetching with specific time range."""
    start_time = datetime(2025, 12, 15)
    end_time = datetime(2025, 12, 20)

    events = list(
        fetch_vault_deposits(
            session,
            hyperliquid_sample_vault,
            start_time=start_time,
            end_time=end_time,
        )
    )

    # All events should be within the time range
    for event in events:
        assert start_time <= event.timestamp <= end_time, "All events should be within the specified time range"


def test_fetch_empty_result(session, hyperliquid_sample_vault):
    """Test fetching with a time range that has no events."""
    start_time = datetime(2019, 1, 1)
    end_time = datetime(2020, 1, 1)

    events = list(
        fetch_vault_deposits(
            session,
            hyperliquid_sample_vault,
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


def test_aggregate_daily_flows(vault_events: list[VaultDepositEvent]):
    """Test daily flow aggregation from vault events."""
    flows = aggregate_daily_flows(vault_events)

    assert isinstance(flows, dict)

    # The test vault has 8 deposits in the 2025-12-01 to 2025-12-28 period
    total_dep_count = sum(f[0] for f in flows.values())
    total_wd_count = sum(f[1] for f in flows.values())
    total_dep_usd = sum(f[2] for f in flows.values())
    total_wd_usd = sum(f[3] for f in flows.values())

    assert total_dep_count == 8
    assert total_wd_count == 0
    assert total_dep_usd == pytest.approx(3650.0)
    assert total_wd_usd == pytest.approx(0.0)

    # Each date key should be a datetime.date
    for date_key in flows.keys():
        assert hasattr(date_key, "year")

    # Each value is a 4-tuple: (dep_count, wd_count, dep_usd, wd_usd)
    for val in flows.values():
        assert len(val) == 4
        assert val[0] >= 0  # deposit count
        assert val[1] >= 0  # withdrawal count
        assert val[2] >= 0.0  # deposit usd
        assert val[3] >= 0.0  # withdrawal usd


def test_aggregate_daily_flows_synthetic():
    """Test daily flow aggregation with synthetic events including withdrawals."""
    from decimal import Decimal

    events = [
        VaultDepositEvent(
            event_type=VaultEventType.vault_deposit,
            vault_address="0xabc",
            user_address="0x123",
            usdc=Decimal("1000"),
            timestamp=datetime(2025, 12, 15, 10, 0, 0),
        ),
        VaultDepositEvent(
            event_type=VaultEventType.vault_deposit,
            vault_address="0xabc",
            user_address="0x456",
            usdc=Decimal("500"),
            timestamp=datetime(2025, 12, 15, 14, 0, 0),
        ),
        VaultDepositEvent(
            event_type=VaultEventType.vault_withdraw,
            vault_address="0xabc",
            user_address="0x789",
            usdc=Decimal("-200"),
            timestamp=datetime(2025, 12, 16, 9, 0, 0),
        ),
        # Distribution event — should be ignored by aggregate_daily_flows
        VaultDepositEvent(
            event_type=VaultEventType.vault_distribution,
            vault_address="0xabc",
            user_address=None,
            usdc=Decimal("50"),
            timestamp=datetime(2025, 12, 16, 12, 0, 0),
        ),
    ]

    flows = aggregate_daily_flows(events)

    from datetime import date

    assert date(2025, 12, 15) in flows
    assert flows[date(2025, 12, 15)] == (2, 0, 1500.0, 0.0)

    assert date(2025, 12, 16) in flows
    assert flows[date(2025, 12, 16)] == (0, 1, 0.0, 200.0)

    # Distribution event should not create an entry on its own
    assert len(flows) == 2
