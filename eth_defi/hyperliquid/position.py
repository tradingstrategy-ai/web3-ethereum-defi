"""Hyperliquid vault position history reconstruction.

This module provides functionality for reconstructing historical position events
(opens, closes, increases, decreases) from Hyperliquid vault fill data.

The Hyperliquid API does not provide a direct position history endpoint, so we must
reconstruct position events by processing trade fills chronologically and tracking
position state changes.

API Endpoints Used
------------------

- ``userFillsByTime`` - Paginated fill history with time range support
- ``clearinghouseState`` - Current open positions (for validation)

Pagination Strategy
-------------------

The ``userFillsByTime`` endpoint has these constraints:

- Max 2,000 fills per response
- Only 10,000 most recent fills accessible
- Time-based pagination using ``startTime`` and ``endTime`` (milliseconds)

To paginate backwards through history:

1. Query with desired ``startTime`` and ``endTime``
2. Use the oldest fill's timestamp - 1ms as the next ``endTime``
3. Repeat until no more fills or ``startTime`` reached

Example::

    from datetime import datetime, timedelta
    from eth_defi.hyperliquid.session import create_hyperliquid_session
    from eth_defi.hyperliquid.position import (
        fetch_vault_fills,
        reconstruct_position_history,
    )

    session = create_hyperliquid_session()
    vault_address = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

    # Fetch fills for the last 30 days (returns an iterator)
    start_time = datetime.now() - timedelta(days=30)
    fills = fetch_vault_fills(session, vault_address, start_time=start_time)

    # Reconstruct position events (also returns an iterator)
    for event in reconstruct_position_history(fills):
        print(f"{event.timestamp}: {event.event_type} {event.direction} {event.coin} size={event.size} @ {event.price}")
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable, Iterator

from eth_typing import HexAddress
from eth_defi.hyperliquid.session import HyperliquidSession

logger = logging.getLogger(__name__)

#: Maximum fills returned per API request
MAX_FILLS_PER_REQUEST = 2000

#: Maximum total fills accessible via API
MAX_TOTAL_FILLS = 10000


class PositionEventType(Enum):
    """Type of position event."""

    #: Opening a new position from flat
    open = "open"
    #: Closing entire position to flat
    close = "close"
    #: Increasing position size (same direction)
    increase = "increase"
    #: Decreasing position size (same direction)
    decrease = "decrease"


class PositionDirection(Enum):
    """Direction of a position."""

    long = "long"
    short = "short"


@dataclass(slots=True)
class PositionEvent:
    """Represents a position change event reconstructed from fill data.

    Position events are derived by processing fills chronologically and
    detecting when positions are opened, closed, increased, or decreased.
    """

    #: Type of position event
    event_type: PositionEventType
    #: Asset symbol (e.g., "BTC", "ETH")
    coin: str
    #: Position direction
    direction: PositionDirection
    #: Size of this fill (always positive)
    size: Decimal
    #: Execution price
    price: Decimal
    #: Event timestamp
    timestamp: datetime.datetime
    #: Position size after this event (positive = long, negative = short)
    position_after: Decimal
    #: Realized PnL for closes/decreases (None for opens/increases)
    realized_pnl: Decimal | None = None
    #: Transaction hash
    fill_hash: str | None = None
    #: Order ID
    order_id: int | None = None
    #: Trade ID
    trade_id: int | None = None
    #: Fee paid
    fee: Decimal | None = None
    #: Fee token (e.g., "USDC")
    fee_token: str | None = None


@dataclass(slots=True)
class Fill:
    """Parsed fill data from Hyperliquid API.

    This is an intermediate representation of raw API fill data
    with proper typing.
    """

    #: Asset symbol
    coin: str
    #: Trade side: "B" (buy) or "A" (sell/ask)
    side: str
    #: Fill size
    size: Decimal
    #: Fill price
    price: Decimal
    #: Timestamp in milliseconds
    timestamp_ms: int
    #: Position size before this fill
    start_position: Decimal
    #: Closed PnL (for position reductions)
    closed_pnl: Decimal
    #: Direction hint from API (e.g., "Open Long", "Close Short")
    direction_hint: str
    #: Transaction hash
    hash: str | None
    #: Order ID
    order_id: int | None
    #: Trade ID
    trade_id: int | None
    #: Fee paid
    fee: Decimal
    #: Fee token
    fee_token: str

    @classmethod
    def from_api_response(cls, data: dict) -> "Fill":
        """Parse a fill from API response data.

        :param data: Raw fill dict from API
        :return: Parsed Fill object
        """
        return cls(
            coin=data["coin"],
            side=data["side"],
            size=Decimal(str(data["sz"])),
            price=Decimal(str(data["px"])),
            timestamp_ms=data["time"],
            start_position=Decimal(str(data.get("startPosition", "0"))),
            closed_pnl=Decimal(str(data.get("closedPnl", "0"))),
            direction_hint=data.get("dir", ""),
            hash=data.get("hash"),
            order_id=data.get("oid"),
            trade_id=data.get("tid"),
            fee=Decimal(str(data.get("fee", "0"))),
            fee_token=data.get("feeToken", "USDC"),
        )

    @property
    def timestamp(self) -> datetime.datetime:
        """Convert millisecond timestamp to datetime."""
        return datetime.datetime.fromtimestamp(self.timestamp_ms / 1000)


def fetch_vault_fills(
    session: HyperliquidSession,
    vault_address: HexAddress,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
    timeout: float = 30.0,
    aggregate_by_time: bool = False,
) -> Iterator[Fill]:
    """Fetch all fills for a vault with automatic pagination.

    Fetches trade fills from the Hyperliquid API using the ``userFillsByTime``
    endpoint with automatic pagination to handle API limits.

    The fills are yielded in chronological order (oldest first) for
    position reconstruction.

    Note: This function collects all fills before yielding to ensure
    chronological ordering. For memory-constrained scenarios with very
    large fill histories, consider using :py:func:`fetch_vault_fills_iterator`
    which yields fills in API order (not chronological).

    Example::

        from datetime import datetime, timedelta
        from eth_defi.hyperliquid.session import create_hyperliquid_session
        from eth_defi.hyperliquid.position import fetch_vault_fills

        session = create_hyperliquid_session()
        vault = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

        # Fetch last 7 days of fills
        fills = list(
            fetch_vault_fills(
                session,
                vault,
                start_time=datetime.now() - timedelta(days=7),
            )
        )
        print(f"Fetched {len(fills)} fills")

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`
    :param vault_address:
        Vault address to fetch fills for
    :param start_time:
        Start of time range (inclusive). Defaults to 30 days ago.
    :param end_time:
        End of time range (inclusive). Defaults to current time.
    :param timeout:
        HTTP request timeout in seconds
    :param aggregate_by_time:
        When True, partial fills from the same crossing order are combined
    :return:
        Iterator of fills sorted by timestamp ascending (oldest first)
    :raises requests.HTTPError:
        If the HTTP request fails after retries
    """
    if end_time is None:
        end_time = datetime.datetime.now()

    if start_time is None:
        # Default to 30 days ago
        start_time = end_time - datetime.timedelta(days=30)

    all_fills: list[Fill] = []
    current_end_ms = int(end_time.timestamp() * 1000)
    start_ms = int(start_time.timestamp() * 1000)
    total_fetched = 0

    logger.info(
        "Fetching fills for vault %s from %s to %s",
        vault_address,
        start_time.isoformat(),
        end_time.isoformat(),
    )

    while current_end_ms > start_ms:
        payload = {
            "type": "userFillsByTime",
            "user": vault_address,
            "startTime": start_ms,
            "endTime": current_end_ms,
        }
        if aggregate_by_time:
            payload["aggregateByTime"] = True

        logger.debug(f"Fetching fills: startTime={start_ms}, endTime={current_end_ms}")

        response = session.post(
            f"{session.api_url}/info",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        raw_fills = response.json()

        if not raw_fills:
            logger.debug("No more fills returned, pagination complete")
            break

        # Parse fills
        batch_fills = [Fill.from_api_response(f) for f in raw_fills]
        all_fills.extend(batch_fills)
        total_fetched += len(batch_fills)

        logger.debug(f"Fetched {len(batch_fills)} fills, total: {total_fetched}")

        # Find oldest timestamp for next iteration
        # API returns newest first, so we need to go backwards
        oldest_timestamp_ms = min(f.timestamp_ms for f in batch_fills)

        # Move end time before oldest fill to avoid duplicates
        current_end_ms = oldest_timestamp_ms - 1

        # Check if we've hit the API limit
        if total_fetched >= MAX_TOTAL_FILLS:
            logger.warning(f"Hit API limit of {MAX_TOTAL_FILLS} fills. Older history may be unavailable.")
            break

        # If we got fewer than max per request, we've likely exhausted the range
        if len(batch_fills) < MAX_FILLS_PER_REQUEST:
            break

    # Sort by timestamp ascending for chronological processing
    all_fills.sort(key=lambda f: f.timestamp_ms)

    logger.info("Fetched %d total fills for vault %s", len(all_fills), vault_address)

    yield from all_fills


def fetch_vault_fills_iterator(
    session: HyperliquidSession,
    vault_address: HexAddress,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
    timeout: float = 30.0,
    aggregate_by_time: bool = False,
) -> Iterator[Fill]:
    """Iterate over fills for a vault with automatic pagination.

    Memory-efficient version of :py:func:`fetch_vault_fills` that yields
    fills one at a time instead of loading all into memory.

    Note that fills are yielded in API order (newest first per batch),
    not chronological order. Use :py:func:`fetch_vault_fills` if you
    need chronological ordering.

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`
    :param vault_address:
        Vault address to fetch fills for
    :param start_time:
        Start of time range (inclusive)
    :param end_time:
        End of time range (inclusive)
    :param timeout:
        HTTP request timeout in seconds
    :param aggregate_by_time:
        When True, partial fills are combined
    :return:
        Iterator yielding Fill objects
    """
    if end_time is None:
        end_time = datetime.datetime.now()

    if start_time is None:
        start_time = end_time - datetime.timedelta(days=30)

    current_end_ms = int(end_time.timestamp() * 1000)
    start_ms = int(start_time.timestamp() * 1000)
    total_fetched = 0

    while current_end_ms > start_ms:
        payload = {
            "type": "userFillsByTime",
            "user": vault_address,
            "startTime": start_ms,
            "endTime": current_end_ms,
        }
        if aggregate_by_time:
            payload["aggregateByTime"] = True

        response = session.post(
            f"{session.api_url}/info",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        raw_fills = response.json()

        if not raw_fills:
            break

        oldest_timestamp_ms = None
        for raw_fill in raw_fills:
            fill = Fill.from_api_response(raw_fill)
            yield fill
            total_fetched += 1

            if oldest_timestamp_ms is None or fill.timestamp_ms < oldest_timestamp_ms:
                oldest_timestamp_ms = fill.timestamp_ms

        if oldest_timestamp_ms is not None:
            current_end_ms = oldest_timestamp_ms - 1

        if total_fetched >= MAX_TOTAL_FILLS:
            break

        if len(raw_fills) < MAX_FILLS_PER_REQUEST:
            break


def reconstruct_position_history(
    fills: Iterable[Fill],
) -> Iterator[PositionEvent]:
    """Reconstruct position open/close events from fill history.

    Processes fills chronologically to detect position state changes:

    - **Open**: New position from flat (size was 0)
    - **Close**: Position closed to flat (size becomes 0)
    - **Increase**: Position size increased (same direction)
    - **Decrease**: Position size decreased (same direction, not to 0)

    When a trade flips position direction (e.g., long to short), it generates
    two events: a close of the old position and an open of the new position.

    Example::

        from eth_defi.hyperliquid.position import (
            fetch_vault_fills,
            reconstruct_position_history,
        )

        fills = fetch_vault_fills(session, vault_address)
        events = list(reconstruct_position_history(fills))

        # Filter for just opens and closes
        trades = [
            e
            for e in events
            if e.event_type
            in (
                PositionEventType.open,
                PositionEventType.close,
            )
        ]

        for trade in trades:
            print(f"{trade.timestamp}: {trade.event_type.value} {trade.direction.value} {trade.coin}")

    :param fills:
        Iterable of fills sorted by timestamp ascending (oldest first).
        Use :py:func:`fetch_vault_fills` or :py:func:`fetch_vault_fills_iterator` to obtain this.
    :return:
        Iterator of position events in chronological order
    """
    # Track current position per asset
    # Positive = long, negative = short
    positions: dict[str, Decimal] = {}

    for fill in fills:
        coin = fill.coin
        old_size = positions.get(coin, Decimal("0"))

        # Calculate position change
        # Buy increases position, Sell decreases
        if fill.side == "B":
            size_delta = fill.size
        else:  # "A" (sell/ask)
            size_delta = -fill.size

        new_size = old_size + size_delta

        # Determine event type based on position state change
        if old_size == Decimal("0"):
            # Was flat, now have position -> Open
            event_type = PositionEventType.open
            direction = PositionDirection.long if new_size > 0 else PositionDirection.short
            realized_pnl = None

            yield _create_event(fill, event_type, direction, fill.size, new_size, realized_pnl)

        elif new_size == Decimal("0"):
            # Had position, now flat -> Close
            event_type = PositionEventType.close
            direction = PositionDirection.long if old_size > 0 else PositionDirection.short
            realized_pnl = fill.closed_pnl if fill.closed_pnl != 0 else None

            yield _create_event(fill, event_type, direction, fill.size, new_size, realized_pnl)

        elif (old_size > 0 and new_size > 0) or (old_size < 0 and new_size < 0):
            # Same side, size changed
            direction = PositionDirection.long if new_size > 0 else PositionDirection.short

            if abs(new_size) > abs(old_size):
                # Position increased
                event_type = PositionEventType.increase
                realized_pnl = None
            else:
                # Position decreased (partial close)
                event_type = PositionEventType.decrease
                realized_pnl = fill.closed_pnl if fill.closed_pnl != 0 else None

            yield _create_event(fill, event_type, direction, fill.size, new_size, realized_pnl)

        else:
            # Position flipped sides (e.g., long -> short)
            # This is a close followed by an open

            # First: close old position
            old_direction = PositionDirection.long if old_size > 0 else PositionDirection.short
            yield _create_event(
                fill,
                PositionEventType.close,
                old_direction,
                abs(old_size),  # Close the entire old position
                Decimal("0"),
                fill.closed_pnl if fill.closed_pnl != 0 else None,
            )

            # Second: open new position with remaining size
            new_direction = PositionDirection.long if new_size > 0 else PositionDirection.short
            yield _create_event(
                fill,
                PositionEventType.open,
                new_direction,
                abs(new_size),  # Open with the flipped amount
                new_size,
                None,
            )

        # Update position tracker
        positions[coin] = new_size


def _create_event(
    fill: Fill,
    event_type: PositionEventType,
    direction: PositionDirection,
    size: Decimal,
    position_after: Decimal,
    realized_pnl: Decimal | None,
) -> PositionEvent:
    """Helper to create a PositionEvent from a fill."""
    return PositionEvent(
        event_type=event_type,
        coin=fill.coin,
        direction=direction,
        size=size,
        price=fill.price,
        timestamp=fill.timestamp,
        position_after=position_after,
        realized_pnl=realized_pnl,
        fill_hash=fill.hash,
        order_id=fill.order_id,
        trade_id=fill.trade_id,
        fee=fill.fee,
        fee_token=fill.fee_token,
    )


def validate_position_reconstruction(fills: list[Fill]) -> bool:
    """Validate position reconstruction against API's startPosition field.

    The Hyperliquid API includes a ``startPosition`` field in each fill
    showing the position size before that fill. This function validates
    that our position tracking matches the API's values.

    :param fills:
        List of fills sorted by timestamp ascending
    :return:
        True if reconstruction matches API data, False otherwise
    """
    positions: dict[str, Decimal] = {}

    for fill in fills:
        coin = fill.coin
        expected_start = fill.start_position
        actual_start = positions.get(coin, Decimal("0"))

        if actual_start != expected_start:
            logger.error(f"Position mismatch for {coin} at {fill.timestamp}: expected startPosition={expected_start}, calculated={actual_start}")
            return False

        # Update position
        if fill.side == "B":
            positions[coin] = actual_start + fill.size
        else:
            positions[coin] = actual_start - fill.size

    return True


def get_position_summary(events: list[PositionEvent]) -> dict[str, dict]:
    """Generate a summary of position activity from events.

    :param events:
        List of position events from :py:func:`reconstruct_position_history`
    :return:
        Dict mapping coin to summary stats
    """
    from collections import defaultdict

    summaries: dict[str, dict] = defaultdict(
        lambda: {
            "total_trades": 0,
            "opens": 0,
            "closes": 0,
            "increases": 0,
            "decreases": 0,
            "total_realized_pnl": Decimal("0"),
            "total_fees": Decimal("0"),
            "current_position": Decimal("0"),
        }
    )

    for event in events:
        summary = summaries[event.coin]
        summary["total_trades"] += 1
        summary["current_position"] = event.position_after

        if event.event_type == PositionEventType.open:
            summary["opens"] += 1
        elif event.event_type == PositionEventType.close:
            summary["closes"] += 1
        elif event.event_type == PositionEventType.increase:
            summary["increases"] += 1
        elif event.event_type == PositionEventType.decrease:
            summary["decreases"] += 1

        if event.realized_pnl is not None:
            summary["total_realized_pnl"] += event.realized_pnl

        if event.fee is not None:
            summary["total_fees"] += event.fee

    return dict(summaries)
