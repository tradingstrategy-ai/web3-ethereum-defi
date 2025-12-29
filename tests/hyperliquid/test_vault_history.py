"""Test Hyperliquid vault position history reconstruction.

This test module verifies that we can fetch fills and reconstruct
position history for a Hyperliquid vault.

Uses vault https://app.hyperliquid.xyz/vaults/0x3df9769bbbb335340872f01d8157c779d73c6ed0
as the test case (Trading Strategy - IchiV3 LS).

All tests use a fixed time range of 2025-12-01 to 2025-12-28 for reproducibility.
"""

from datetime import datetime
from decimal import Decimal

import pytest

from eth_defi.hyperliquid.position import Fill, PositionDirection, PositionEvent, PositionEventType, fetch_vault_fills, get_position_summary, reconstruct_position_history, validate_position_reconstruction
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
def vault_fills(session) -> list[Fill]:
    """Fetch fills for the test vault.

    Uses fixed time range 2025-12-01 to 2025-12-28 for reproducibility.
    """
    fills = list(
        fetch_vault_fills(
            session,
            TEST_VAULT_ADDRESS,
            start_time=TEST_START_TIME,
            end_time=TEST_END_TIME,
        )
    )
    return fills


@pytest.fixture(scope="module")
def position_events(vault_fills) -> list[PositionEvent]:
    """Reconstruct position events from fills."""
    return list(reconstruct_position_history(vault_fills))


def test_fetch_returns_fills(vault_fills: list[Fill]):
    """Test that we get fills from the API."""
    assert len(vault_fills) > 0, "Expected vault to have trading history"


def test_fill_data_structure(vault_fills: list[Fill]):
    """Test that fill data is properly parsed."""
    if not vault_fills:
        pytest.skip("No fills to test")

    fill = vault_fills[0]

    # Check required fields
    assert isinstance(fill.coin, str)
    assert fill.coin, "Coin should not be empty"

    assert fill.side in ("B", "A"), f"Invalid side: {fill.side}"

    assert isinstance(fill.size, Decimal)
    assert fill.size > 0, "Size should be positive"

    assert isinstance(fill.price, Decimal)
    assert fill.price > 0, "Price should be positive"

    assert isinstance(fill.timestamp_ms, int)
    assert fill.timestamp_ms > 0, "Timestamp should be positive"

    assert isinstance(fill.start_position, Decimal)
    assert isinstance(fill.closed_pnl, Decimal)
    assert isinstance(fill.fee, Decimal)


def test_fetch_with_time_range(session):
    """Test fetching with specific time range."""
    start_time = datetime(2025, 12, 15)
    end_time = datetime(2025, 12, 20)

    fills = list(
        fetch_vault_fills(
            session,
            TEST_VAULT_ADDRESS,
            start_time=start_time,
            end_time=end_time,
        )
    )

    # All fills should be within the time range
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)

    assert all(start_ms <= fill.timestamp_ms <= end_ms for fill in fills), "All fills should be within the specified time range"


def test_event_data_structure(position_events: list[PositionEvent]):
    """Test that events have correct structure."""
    if not position_events:
        pytest.skip("No position events to test")

    event = position_events[0]

    assert isinstance(event.event_type, PositionEventType)
    assert isinstance(event.coin, str)
    assert event.coin, "Coin should not be empty"

    assert isinstance(event.direction, PositionDirection)
    assert event.direction in (PositionDirection.long, PositionDirection.short)

    assert isinstance(event.size, Decimal)
    assert event.size > 0, "Event size should be positive"

    assert isinstance(event.price, Decimal)
    assert event.price > 0, "Price should be positive"

    assert isinstance(event.timestamp, datetime)
    assert isinstance(event.position_after, Decimal)


def test_summary(position_events: list[PositionEvent]):
    """Test position summary with expected values from fixed historical data.

    Since we use fixed time range 2025-12-01 to 2025-12-28, the summary
    values are deterministic and can be tested against known values.
    """
    summary = get_position_summary(position_events)

    # Verify total number of coins traded
    assert len(summary) == 30, f"Expected 30 coins traded, got {len(summary)}"

    # Verify specific coin summaries
    # ACE had the most activity
    assert summary["ACE"]["total_trades"] == 13
    assert summary["ACE"]["opens"] == 5
    assert summary["ACE"]["closes"] == 5
    assert summary["ACE"]["increases"] == 1
    assert summary["ACE"]["decreases"] == 2
    assert summary["ACE"]["current_position"] == Decimal("0")

    # ZEC also had significant activity
    assert summary["ZEC"]["total_trades"] == 10
    assert summary["ZEC"]["opens"] == 4
    assert summary["ZEC"]["closes"] == 3
    assert summary["ZEC"]["current_position"] == Decimal("2.41")

    # BTC position
    assert summary["BTC"]["total_trades"] == 3
    assert summary["BTC"]["opens"] == 2
    assert summary["BTC"]["closes"] == 1
    assert summary["BTC"]["current_position"] == Decimal("-0.00883")

    # ENA - single open, still held
    assert summary["ENA"]["total_trades"] == 1
    assert summary["ENA"]["opens"] == 1
    assert summary["ENA"]["closes"] == 0
    assert summary["ENA"]["current_position"] == Decimal("107.0")

    # Verify realized PnL for some coins
    assert summary["AAVE"]["total_realized_pnl"] == Decimal("96.6087")
    assert summary["ZEC"]["total_realized_pnl"] == Decimal("66.9261")
    assert summary["VVV"]["total_realized_pnl"] == Decimal("32.009223")

    # Verify fees are tracked
    assert all(stats["total_fees"] > 0 for stats in summary.values()), "All coins should have fees"


def test_pagination_handles_empty_result(session):
    """Test pagination with a time range that has no fills."""
    start_time = datetime(2019, 1, 1)
    end_time = datetime(2020, 1, 1)

    fills = list(
        fetch_vault_fills(
            session,
            TEST_VAULT_ADDRESS,
            start_time=start_time,
            end_time=end_time,
        )
    )

    assert fills == [], "Should return empty list for time range with no fills"


def test_pagination_respects_start_time(session):
    """Test that pagination stops at start_time boundary."""
    start_time = datetime(2025, 12, 20)
    end_time = datetime(2025, 12, 25)

    fills = list(
        fetch_vault_fills(
            session,
            TEST_VAULT_ADDRESS,
            start_time=start_time,
            end_time=end_time,
        )
    )

    start_ms = int(start_time.timestamp() * 1000)

    assert all(fill.timestamp_ms >= start_ms for fill in fills), "All fills should be at or after start_time"
