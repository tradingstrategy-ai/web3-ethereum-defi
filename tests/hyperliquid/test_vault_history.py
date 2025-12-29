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

from eth_defi.hyperliquid.position import (
    Fill,
    PositionDirection,
    PositionEvent,
    PositionEventType,
    fetch_vault_fills,
    get_position_summary,
    reconstruct_position_history,
    validate_position_reconstruction,
)
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
    fills = fetch_vault_fills(
        session,
        TEST_VAULT_ADDRESS,
        start_time=TEST_START_TIME,
        end_time=TEST_END_TIME,
    )
    return fills


@pytest.fixture(scope="module")
def position_events(vault_fills) -> list[PositionEvent]:
    """Reconstruct position events from fills."""
    return reconstruct_position_history(vault_fills)


def test_fetch_returns_fills(vault_fills: list[Fill]):
    """Test that we get fills from the API."""
    assert len(vault_fills) > 0, "Expected vault to have trading history"


def test_fills_are_chronologically_sorted(vault_fills: list[Fill]):
    """Test that fills are sorted oldest to newest."""
    if len(vault_fills) < 2:
        pytest.skip("Not enough fills to test ordering")

    assert all(
        vault_fills[i].timestamp_ms >= vault_fills[i - 1].timestamp_ms
        for i in range(1, len(vault_fills))
    ), "Fills are not sorted chronologically"


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


def test_fill_timestamp_property(vault_fills: list[Fill]):
    """Test that timestamp property converts correctly."""
    if not vault_fills:
        pytest.skip("No fills to test")

    fill = vault_fills[0]
    ts = fill.timestamp

    assert isinstance(ts, datetime)
    assert ts.year == 2025, "Timestamp should be in 2025"
    assert ts.month == 12, "Timestamp should be in December"


def test_fetch_with_time_range(session):
    """Test fetching with specific time range."""
    start_time = datetime(2025, 12, 15)
    end_time = datetime(2025, 12, 20)

    fills = fetch_vault_fills(
        session,
        TEST_VAULT_ADDRESS,
        start_time=start_time,
        end_time=end_time,
    )

    # All fills should be within the time range
    start_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)

    assert all(
        start_ms <= fill.timestamp_ms <= end_ms
        for fill in fills
    ), "All fills should be within the specified time range"


def test_reconstruct_returns_events(position_events: list[PositionEvent]):
    """Test that reconstruction produces events."""
    assert isinstance(position_events, list)


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


def test_events_are_chronological(position_events: list[PositionEvent]):
    """Test that events are in chronological order."""
    if len(position_events) < 2:
        pytest.skip("Not enough events to test ordering")

    assert all(
        position_events[i].timestamp >= position_events[i - 1].timestamp
        for i in range(1, len(position_events))
    ), "Events are not sorted chronologically"


def test_open_events_have_position(position_events: list[PositionEvent]):
    """Test that open events result in non-zero position."""
    open_events = [e for e in position_events if e.event_type == PositionEventType.open]

    assert all(
        e.position_after != 0 for e in open_events
    ), "All open events should result in non-zero position"


def test_close_events_are_flat(position_events: list[PositionEvent]):
    """Test that close events result in zero position."""
    close_events = [e for e in position_events if e.event_type == PositionEventType.close]

    assert all(
        e.position_after == 0 for e in close_events
    ), "All close events should result in zero position"


def test_direction_consistency(position_events: list[PositionEvent]):
    """Test that direction matches position sign."""
    # Positive positions should be long
    assert all(
        e.direction == PositionDirection.long
        for e in position_events
        if e.position_after > 0
    ), "All positive positions should be long"

    # Negative positions should be short
    assert all(
        e.direction == PositionDirection.short
        for e in position_events
        if e.position_after < 0
    ), "All negative positions should be short"
    # position_after == 0 is valid for close events


def test_validation_passes(vault_fills: list[Fill]):
    """Test that our reconstruction matches API's startPosition.

    Note: This validation only works if we have complete fill history from
    the start of the vault. When using a partial time range, the first fills
    may have non-zero startPosition from prior trades we don't have.

    For partial time ranges, we check that position tracking is internally
    consistent after the first fill for each coin.
    """
    if not vault_fills:
        pytest.skip("No fills to validate")

    # For partial time ranges, first fills may have non-zero startPosition
    # from prior positions. Check internal consistency instead.
    first_fill = vault_fills[0]
    if first_fill.start_position != 0:
        # We have a partial history - just verify the fills are internally consistent
        # by checking that position changes are calculated correctly
        positions: dict[str, Decimal] = {}
        for fill in vault_fills:
            if fill.coin not in positions:
                # Initialize with the startPosition from the API for this coin's first fill
                positions[fill.coin] = fill.start_position

            expected = positions[fill.coin]
            assert fill.start_position == expected, (
                f"Position mismatch for {fill.coin}: expected {expected}, got {fill.start_position}"
            )

            # Update position
            if fill.side == "B":
                positions[fill.coin] = expected + fill.size
            else:
                positions[fill.coin] = expected - fill.size
    else:
        # Full history available - use standard validation
        is_valid = validate_position_reconstruction(vault_fills)
        assert is_valid, "Position reconstruction should match API startPosition values"


def test_summary_structure(position_events: list[PositionEvent]):
    """Test position summary has correct structure."""
    summary = get_position_summary(position_events)

    assert isinstance(summary, dict)

    required_keys = {
        "total_trades", "opens", "closes", "increases",
        "decreases", "total_realized_pnl", "total_fees", "current_position"
    }

    assert all(
        isinstance(coin, str) and required_keys.issubset(stats.keys())
        for coin, stats in summary.items()
    ), "All summary entries should have required keys"

    assert all(
        isinstance(stats["total_trades"], int)
        and isinstance(stats["total_realized_pnl"], Decimal)
        and isinstance(stats["total_fees"], Decimal)
        for stats in summary.values()
    ), "Summary stats should have correct types"


def test_summary_trade_count_matches(position_events: list[PositionEvent]):
    """Test that summary trade counts match event counts."""
    summary = get_position_summary(position_events)

    def count_events(coin: str, event_type: PositionEventType) -> int:
        return sum(1 for e in position_events if e.coin == coin and e.event_type == event_type)

    assert all(
        stats["total_trades"] == sum(1 for e in position_events if e.coin == coin)
        and stats["opens"] == count_events(coin, PositionEventType.open)
        and stats["closes"] == count_events(coin, PositionEventType.close)
        and stats["increases"] == count_events(coin, PositionEventType.increase)
        and stats["decreases"] == count_events(coin, PositionEventType.decrease)
        for coin, stats in summary.items()
    ), "Summary trade counts should match event counts"


def test_pagination_handles_empty_result(session):
    """Test pagination with a time range that has no fills."""
    start_time = datetime(2019, 1, 1)
    end_time = datetime(2020, 1, 1)

    fills = fetch_vault_fills(
        session,
        TEST_VAULT_ADDRESS,
        start_time=start_time,
        end_time=end_time,
    )

    assert fills == [], "Should return empty list for time range with no fills"


def test_pagination_respects_start_time(session):
    """Test that pagination stops at start_time boundary."""
    start_time = datetime(2025, 12, 20)
    end_time = datetime(2025, 12, 25)

    fills = fetch_vault_fills(
        session,
        TEST_VAULT_ADDRESS,
        start_time=start_time,
        end_time=end_time,
    )

    start_ms = int(start_time.timestamp() * 1000)

    assert all(
        fill.timestamp_ms >= start_ms for fill in fills
    ), "All fills should be at or after start_time"
