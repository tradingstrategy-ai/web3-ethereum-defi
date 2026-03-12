"""Hyperliquid account trade history reconstruction.

Reconstructs historical closed positions, open positions, and round-trip trades
for any Hyperliquid account (vaults or normal addresses) by combining:

- Fill history from ``userFillsByTime``
- Funding payments from ``userFunding``
- Current positions from ``clearinghouseState``

The module groups individual fills into round-trip trades (open→close) with
VWAP entry/exit prices, funding costs, and realised PnL.

API endpoints used
------------------

- ``userFillsByTime`` — paginated fill history (max 10K fills accessible)
- ``userFunding`` — paginated funding payment history
- ``clearinghouseState`` — current open positions with unrealised PnL

Example::

    from eth_defi.hyperliquid.session import create_hyperliquid_session
    from eth_defi.hyperliquid.trade_history import fetch_account_trade_history

    session = create_hyperliquid_session()
    address = "0x1e37a337ed460039d1b15bd3bc489de789768d5e"

    history = fetch_account_trade_history(session, address)

    print(f"Open trades: {len(history.open_trades)}")
    print(f"Closed trades: {len(history.closed_trades)}")
    for trade in history.closed_trades[:5]:
        print(f"  {trade.coin} {trade.direction.value}: PnL={trade.net_pnl:.2f}")
"""

import datetime
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Iterator

import pandas as pd
from eth_typing import HexAddress
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.api import (
    AssetPosition,
    MarginSummary,
    PerpClearinghouseState,
    fetch_perp_clearinghouse_state,
)
from eth_defi.hyperliquid.position import (
    Fill,
    PositionDirection,
    PositionEvent,
    PositionEventType,
    fetch_vault_fills,
    reconstruct_position_history,
    validate_position_reconstruction,
)
from eth_defi.hyperliquid.session import HyperliquidSession

logger = logging.getLogger(__name__)

#: Maximum funding payments returned per API request
MAX_FUNDING_PER_REQUEST = 500


@dataclass(slots=True)
class FundingPayment:
    """A single funding payment from the Hyperliquid ``userFunding`` endpoint.

    Funding is paid/received periodically (typically hourly) for open
    perpetual positions. Negative USDC means funding was paid by the holder;
    positive means funding was received.
    """

    #: Asset symbol (e.g., "BTC", "ETH")
    coin: str
    #: Funding rate applied
    funding_rate: Decimal
    #: USD amount (negative = paid, positive = received)
    usdc: Decimal
    #: Position size at the time of the funding event
    position_size: Decimal
    #: Funding event timestamp
    timestamp: datetime.datetime
    #: Timestamp in milliseconds (for storage)
    timestamp_ms: int
    #: Transaction hash
    hash: str | None = None
    #: Number of samples aggregated
    n_samples: int = 1

    @classmethod
    def from_api_response(cls, data: dict) -> "FundingPayment":
        """Parse a funding payment from API response data.

        The ``userFunding`` API returns entries with a ``delta`` sub-object
        containing the actual funding fields (coin, usdc, szi, fundingRate).

        :param data:
            Raw funding dict from ``userFunding`` API response.
        :return:
            Parsed FundingPayment object.
        """
        delta = data.get("delta", data)
        return cls(
            coin=delta["coin"],
            funding_rate=Decimal(str(delta.get("fundingRate", "0"))),
            usdc=Decimal(str(delta.get("usdc", "0"))),
            position_size=Decimal(str(delta.get("szi", "0"))),
            timestamp=datetime.datetime.fromtimestamp(data["time"] / 1000),
            timestamp_ms=data["time"],
            hash=data.get("hash"),
            n_samples=delta.get("nSamples", 1),
        )


@dataclass(slots=True)
class RoundTripTrade:
    """A round-trip trade from position open through close.

    Groups individual fills from position-open to position-close with
    volume-weighted average entry/exit prices, funding costs, and PnL.

    For positions that were already open before the data window,
    ``is_complete`` is ``False`` and ``entry_price`` may be unknown.
    """

    #: Asset symbol (e.g., "BTC", "ETH")
    coin: str
    #: Trade direction
    direction: PositionDirection
    #: Whether the position is still open
    is_open: bool
    #: Whether we have the full open→close lifecycle
    #: (False when position was already open before data window)
    is_complete: bool
    #: Timestamp when the position was first opened (or earliest known fill)
    opened_at: datetime.datetime
    #: Timestamp when the position was closed (None if still open)
    closed_at: datetime.datetime | None
    #: Volume-weighted average entry price
    entry_price: Decimal
    #: Volume-weighted average exit price (None if still open or no exits)
    exit_price: Decimal | None
    #: Maximum position size reached during the trade
    max_size: Decimal
    #: Current position size (0 if closed)
    current_size: Decimal
    #: Realised PnL from fills (sum of closed_pnl)
    realised_pnl: Decimal
    #: Total funding payments during this trade
    funding_pnl: Decimal
    #: Total fees paid across all fills
    total_fees: Decimal
    #: Net PnL (realised_pnl + funding_pnl - total_fees)
    net_pnl: Decimal
    #: Unrealised PnL from clearinghouse state (open trades only)
    unrealised_pnl: Decimal | None
    #: All fills belonging to this trade
    fills: list[Fill] = field(default_factory=list)
    #: All funding payments during this trade's lifetime
    funding_payments: list[FundingPayment] = field(default_factory=list)
    #: Duration of the trade
    duration: datetime.timedelta | None = None
    #: Number of fills
    fill_count: int = 0

    # Internal tracking for VWAP calculation (not part of public API)
    #: Cumulative entry cost (size * price) for VWAP
    _entry_cost: Decimal = field(default=Decimal("0"), repr=False)
    #: Cumulative entry size for VWAP
    _entry_size: Decimal = field(default=Decimal("0"), repr=False)
    #: Cumulative exit cost for VWAP
    _exit_cost: Decimal = field(default=Decimal("0"), repr=False)
    #: Cumulative exit size for VWAP
    _exit_size: Decimal = field(default=Decimal("0"), repr=False)


@dataclass(slots=True)
class AccountTradeHistory:
    """Complete trade history snapshot for a Hyperliquid account.

    Combines current open positions, historical closed trades, open trades,
    all fills, and funding payments into a single result.
    """

    #: Account address (vault or user)
    address: HexAddress
    #: Timestamp when this snapshot was taken
    snapshot_time: datetime.datetime
    #: Current open positions from clearinghouse state
    open_positions: list[AssetPosition]
    #: Historical closed round-trip trades
    closed_trades: list[RoundTripTrade]
    #: Currently open round-trip trades
    open_trades: list[RoundTripTrade]
    #: All individual fills in the time range
    fills: list[Fill]
    #: All funding payments in the time range
    funding_payments: list[FundingPayment]
    #: Perp account margin summary
    margin_summary: MarginSummary
    #: Time range start
    start_time: datetime.datetime
    #: Time range end
    end_time: datetime.datetime
    #: Whether the fill history was truncated by the 10K API limit
    fills_truncated: bool


def fetch_account_funding(
    session: HyperliquidSession,
    address: HexAddress,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
    timeout: float = 30.0,
) -> Iterator[FundingPayment]:
    """Fetch all funding payments for an account with automatic pagination.

    Fetches funding payment history from the Hyperliquid API using the
    ``userFunding`` endpoint with automatic forward pagination.

    Payments are yielded in chronological order (oldest first).

    Example::

        from datetime import datetime, timedelta
        from eth_defi.hyperliquid.session import create_hyperliquid_session
        from eth_defi.hyperliquid.trade_history import fetch_account_funding

        session = create_hyperliquid_session()
        address = "0x1e37a337ed460039d1b15bd3bc489de789768d5e"

        payments = list(
            fetch_account_funding(
                session,
                address,
                start_time=datetime.now() - timedelta(days=7),
            )
        )
        print(f"Fetched {len(payments)} funding payments")

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.
    :param address:
        Account address (vault or user).
    :param start_time:
        Start of time range (inclusive). Defaults to 30 days ago.
    :param end_time:
        End of time range (inclusive). Defaults to current time.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Iterator of funding payments sorted by timestamp ascending (oldest first).
    :raises requests.HTTPError:
        If the HTTP request fails after retries.
    """
    if end_time is None:
        end_time = native_datetime_utc_now()

    if start_time is None:
        start_time = end_time - datetime.timedelta(days=30)

    all_payments: list[FundingPayment] = []
    end_ms = int(end_time.timestamp() * 1000)
    current_start_ms = int(start_time.timestamp() * 1000)
    batch_num = 0

    logger.info(
        "Fetching funding payments for %s from %s to %s",
        address,
        start_time.isoformat(),
        end_time.isoformat(),
    )

    progress = tqdm(
        desc=f"Funding {address[:10]}",
        unit="payment",
        leave=False,
    )

    try:
        while current_start_ms < end_ms:
            payload = {
                "type": "userFunding",
                "user": address,
                "startTime": current_start_ms,
                "endTime": end_ms,
            }

            logger.debug("Fetching funding: startTime=%s, endTime=%s", current_start_ms, end_ms)

            response = session.post(
                f"{session.api_url}/info",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            response.raise_for_status()
            raw_payments = response.json()

            if not raw_payments:
                logger.debug("No more funding payments returned, pagination complete")
                break

            batch_num += 1
            batch = [FundingPayment.from_api_response(p) for p in raw_payments]
            all_payments.extend(batch)

            progress.update(len(batch))
            progress.set_postfix(
                batch=batch_num,
                total=len(all_payments),
            )

            # Paginate forward: API returns oldest first
            newest_timestamp_ms = max(p.timestamp_ms for p in batch)
            current_start_ms = newest_timestamp_ms + 1

            # If we got fewer than max per request, we've exhausted the range
            if len(raw_payments) < MAX_FUNDING_PER_REQUEST:
                break
    finally:
        progress.close()

    # Sort by timestamp ascending for chronological processing
    all_payments.sort(key=lambda p: p.timestamp_ms)

    logger.info("Fetched %d total funding payments for %s", len(all_payments), address)

    yield from all_payments


def group_fills_into_trades(
    events: Iterable[PositionEvent],
    fills: list[Fill] | None = None,
) -> tuple[list[RoundTripTrade], list[RoundTripTrade]]:
    """Group position events into round-trip trades.

    Processes position events chronologically and groups them into
    round-trip trades. Each trade tracks a position from open to close
    (or to the current state if still open).

    When a position was already open before the data window (i.e. the
    earliest fill has a non-zero ``start_position``), the trade is
    marked as ``is_complete=False``.

    :param events:
        Position events from :py:func:`~eth_defi.hyperliquid.position.reconstruct_position_history`.
        Must be in chronological order.
    :param fills:
        Optional original fills list for detecting incomplete trades
        via ``start_position``.
    :return:
        Tuple of ``(closed_trades, open_trades)``.
    """
    # Detect coins with pre-existing positions (for is_complete flag)
    pre_existing_coins: set[str] = set()
    if fills:
        seen_coins: set[str] = set()
        for fill in fills:
            if fill.coin not in seen_coins:
                seen_coins.add(fill.coin)
                if fill.start_position != Decimal("0"):
                    pre_existing_coins.add(fill.coin)

    active_trades: dict[str, RoundTripTrade] = {}
    closed_trades: list[RoundTripTrade] = []

    for event in events:
        coin = event.coin

        if event.event_type == PositionEventType.open:
            # Start a new round-trip trade
            is_complete = coin not in pre_existing_coins or coin in active_trades
            trade = RoundTripTrade(
                coin=coin,
                direction=event.direction,
                is_open=True,
                is_complete=is_complete,
                opened_at=event.timestamp,
                closed_at=None,
                entry_price=event.price,
                exit_price=None,
                max_size=event.size,
                current_size=abs(event.position_after),
                realised_pnl=Decimal("0"),
                funding_pnl=Decimal("0"),
                total_fees=event.fee if event.fee else Decimal("0"),
                net_pnl=Decimal("0"),
                unrealised_pnl=None,
                fills=[],
                funding_payments=[],
                fill_count=1,
                _entry_cost=event.size * event.price,
                _entry_size=event.size,
            )
            active_trades[coin] = trade

        elif event.event_type == PositionEventType.increase:
            trade = active_trades.get(coin)
            if trade is None:
                # Position existed before our data window — create incomplete trade
                trade = _create_incomplete_trade(event)
                active_trades[coin] = trade
            else:
                # Update VWAP entry price
                trade._entry_cost += event.size * event.price
                trade._entry_size += event.size
                trade.entry_price = trade._entry_cost / trade._entry_size
                trade.current_size = abs(event.position_after)
                if trade.current_size > trade.max_size:
                    trade.max_size = trade.current_size
                if event.fee:
                    trade.total_fees += event.fee
                trade.fill_count += 1

        elif event.event_type == PositionEventType.decrease:
            trade = active_trades.get(coin)
            if trade is None:
                # Position existed before our data window
                trade = _create_incomplete_trade(event)
                active_trades[coin] = trade
            else:
                # Update exit VWAP
                trade._exit_cost += event.size * event.price
                trade._exit_size += event.size
                trade.exit_price = trade._exit_cost / trade._exit_size
                trade.current_size = abs(event.position_after)
                if event.realized_pnl:
                    trade.realised_pnl += event.realized_pnl
                if event.fee:
                    trade.total_fees += event.fee
                trade.fill_count += 1
                trade.net_pnl = trade.realised_pnl + trade.funding_pnl - trade.total_fees

        elif event.event_type == PositionEventType.close:
            trade = active_trades.get(coin)
            if trade is None:
                # Position existed before our data window
                trade = _create_incomplete_trade(event)
            else:
                # Finalise exit VWAP
                trade._exit_cost += event.size * event.price
                trade._exit_size += event.size
                trade.exit_price = trade._exit_cost / trade._exit_size

            # Close the trade
            trade.is_open = False
            trade.closed_at = event.timestamp
            trade.current_size = Decimal("0")
            trade.duration = trade.closed_at - trade.opened_at
            if event.realized_pnl:
                trade.realised_pnl += event.realized_pnl
            if event.fee:
                trade.total_fees += event.fee
            trade.fill_count += 1
            trade.net_pnl = trade.realised_pnl + trade.funding_pnl - trade.total_fees

            closed_trades.append(trade)
            active_trades.pop(coin, None)

    # Remaining active trades are open
    open_trades = list(active_trades.values())

    return closed_trades, open_trades


def _create_incomplete_trade(event: PositionEvent) -> RoundTripTrade:
    """Create an incomplete trade for a position that existed before the data window."""
    return RoundTripTrade(
        coin=event.coin,
        direction=event.direction,
        is_open=True,
        is_complete=False,
        opened_at=event.timestamp,  # Best we know
        closed_at=None,
        entry_price=Decimal("0"),  # Unknown
        exit_price=event.price if event.event_type in (PositionEventType.decrease, PositionEventType.close) else None,
        max_size=event.size,
        current_size=abs(event.position_after),
        realised_pnl=event.realized_pnl if event.realized_pnl else Decimal("0"),
        funding_pnl=Decimal("0"),
        total_fees=event.fee if event.fee else Decimal("0"),
        net_pnl=Decimal("0"),
        unrealised_pnl=None,
        fills=[],
        funding_payments=[],
        fill_count=1,
        _exit_cost=event.size * event.price if event.event_type in (PositionEventType.decrease, PositionEventType.close) else Decimal("0"),
        _exit_size=event.size if event.event_type in (PositionEventType.decrease, PositionEventType.close) else Decimal("0"),
    )


def attach_funding_to_trades(
    closed_trades: list[RoundTripTrade],
    open_trades: list[RoundTripTrade],
    funding_payments: list[FundingPayment],
) -> None:
    """Attach funding payments to the appropriate round-trip trades.

    For each funding payment, finds the trade for that coin active at the
    payment timestamp and attributes the funding cost/income to it.

    Modifies trades in place.

    :param closed_trades:
        List of closed round-trip trades.
    :param open_trades:
        List of open round-trip trades.
    :param funding_payments:
        Funding payments sorted chronologically.
    """
    # Build a lookup: for each coin, list of (opened_at, closed_at, trade) sorted by opened_at
    all_trades = closed_trades + open_trades
    coin_trades: dict[str, list[RoundTripTrade]] = {}
    for trade in all_trades:
        coin_trades.setdefault(trade.coin, []).append(trade)

    # Sort each coin's trades by opened_at
    for trades_list in coin_trades.values():
        trades_list.sort(key=lambda t: t.opened_at)

    for payment in funding_payments:
        trades_for_coin = coin_trades.get(payment.coin)
        if not trades_for_coin:
            continue

        # Find the active trade at this timestamp
        for trade in trades_for_coin:
            if trade.opened_at <= payment.timestamp:
                if trade.is_open or (trade.closed_at is not None and trade.closed_at >= payment.timestamp):
                    trade.funding_payments.append(payment)
                    trade.funding_pnl += payment.usdc
                    trade.net_pnl = trade.realised_pnl + trade.funding_pnl - trade.total_fees
                    break


def fetch_account_trade_history(
    session: HyperliquidSession,
    address: HexAddress,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
    timeout: float = 30.0,
) -> AccountTradeHistory:
    """Fetch and reconstruct complete trade history for a Hyperliquid account.

    Orchestrates fetching fills, funding payments, and current positions,
    then reconstructs round-trip trades with full PnL accounting.

    Example::

        from eth_defi.hyperliquid.session import create_hyperliquid_session
        from eth_defi.hyperliquid.trade_history import fetch_account_trade_history

        session = create_hyperliquid_session()
        address = "0x1e37a337ed460039d1b15bd3bc489de789768d5e"

        history = fetch_account_trade_history(session, address)

        for trade in history.closed_trades:
            print(f"{trade.coin} {trade.direction.value}: entry={trade.entry_price} exit={trade.exit_price} PnL={trade.net_pnl}")

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.
    :param address:
        Account address (vault or user).
    :param start_time:
        Start of time range. Defaults to 30 days ago.
    :param end_time:
        End of time range. Defaults to now.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        Complete trade history snapshot.
    """
    now = native_datetime_utc_now()
    if end_time is None:
        end_time = now
    if start_time is None:
        start_time = end_time - datetime.timedelta(days=30)

    steps = tqdm(
        total=5,
        desc=f"Trade history {address[:10]}",
        unit="step",
        leave=False,
    )

    try:
        # 1. Fetch fills
        steps.set_postfix(step="fills")
        fills = list(fetch_vault_fills(session, address, start_time=start_time, end_time=end_time, timeout=timeout))
        fills_truncated = len(fills) >= 10000
        steps.update(1)

        # 2. Validate and reconstruct
        steps.set_postfix(step="reconstruct", fills=len(fills))
        if fills:
            valid = validate_position_reconstruction(fills)
            if not valid:
                logger.warning("Position reconstruction validation failed for %s", address)
        events = list(reconstruct_position_history(iter(fills)))
        closed_trades, open_trades = group_fills_into_trades(iter(events), fills=fills)
        steps.update(1)

        # 3. Fetch funding payments
        steps.set_postfix(step="funding", trades=len(closed_trades) + len(open_trades))
        funding_payments = list(fetch_account_funding(session, address, start_time=start_time, end_time=end_time, timeout=timeout))
        steps.update(1)

        # 4. Attach funding to trades
        steps.set_postfix(step="attach_funding", funding=len(funding_payments))
        attach_funding_to_trades(closed_trades, open_trades, funding_payments)
        steps.update(1)

        # 5. Fetch clearinghouse state
        steps.set_postfix(step="clearinghouse")
        clearinghouse = fetch_perp_clearinghouse_state(session, address, timeout=timeout)
        for trade in open_trades:
            for pos in clearinghouse.asset_positions:
                if pos.coin == trade.coin:
                    trade.unrealised_pnl = pos.unrealised_pnl
                    break
        steps.update(1)
    finally:
        steps.close()

    return AccountTradeHistory(
        address=address,
        snapshot_time=now,
        open_positions=clearinghouse.asset_positions,
        closed_trades=closed_trades,
        open_trades=open_trades,
        fills=fills,
        funding_payments=funding_payments,
        margin_summary=clearinghouse.margin_summary,
        start_time=start_time,
        end_time=end_time,
        fills_truncated=fills_truncated,
    )


def create_trade_summary_dataframe(
    trades: list[RoundTripTrade],
) -> pd.DataFrame:
    """Convert round-trip trades to a pandas DataFrame for tabular display.

    :param trades:
        List of round-trip trades from :py:func:`fetch_account_trade_history`.
    :return:
        DataFrame with one row per trade.
    """
    if not trades:
        return pd.DataFrame()

    rows = []
    for trade in trades:
        rows.append(
            {
                "coin": trade.coin,
                "direction": trade.direction.value,
                "is_open": trade.is_open,
                "is_complete": trade.is_complete,
                "opened_at": trade.opened_at,
                "closed_at": trade.closed_at,
                "duration": str(trade.duration) if trade.duration else None,
                "entry_price": float(trade.entry_price),
                "exit_price": float(trade.exit_price) if trade.exit_price else None,
                "max_size": float(trade.max_size),
                "current_size": float(trade.current_size),
                "realised_pnl": float(trade.realised_pnl),
                "funding_pnl": float(trade.funding_pnl),
                "total_fees": float(trade.total_fees),
                "net_pnl": float(trade.net_pnl),
                "unrealised_pnl": float(trade.unrealised_pnl) if trade.unrealised_pnl is not None else None,
                "fill_count": trade.fill_count,
            }
        )

    return pd.DataFrame(rows)


@dataclass(slots=True)
class SharePriceEvent:
    """A single event in the share price time series.

    Each event represents a state change that affects the vault's
    share price: a deposit, withdrawal, trading PnL, or funding payment.
    """

    #: Event timestamp
    timestamp: datetime.datetime
    #: Type of event: "deposit", "withdraw", "fill_pnl", "funding"
    event_type: str
    #: Total assets (NAV) after this event
    total_assets: float
    #: Total supply of shares after this event
    total_supply: float
    #: Share price after this event
    share_price: float
    #: Change in assets from this event
    delta: float
    #: Whether an epoch reset occurred
    epoch_reset: bool = False
    #: Coin involved (for fill_pnl and funding events)
    coin: str | None = None


def compute_event_share_prices(
    fills: list[Fill],
    funding_payments: list[FundingPayment],
    ledger_events: list,
    initial_total_assets: float = 0.0,
) -> list[SharePriceEvent]:
    """Compute event-accurate share prices from actual ledger events.

    Unlike the portfolio-history-derived share prices (which suffer from
    resolution artefacts — see ``README-hyperliquid-vault-limitations.md``),
    this function uses actual event data:

    - **Deposits**: Exact amounts from ``userNonFundingLedgerUpdates``
    - **Withdrawals**: Exact amounts from ``userNonFundingLedgerUpdates``
    - **Trading PnL**: Realised PnL from individual fills (``closed_pnl``)
    - **Funding payments**: USD amounts from ``userFunding``

    This eliminates the resolution-dependent netflow derivation that causes
    share price spikes in the current pipeline.

    The share price model follows ERC-4626 mechanics:

    - ``share_price = total_assets / total_supply``
    - Deposits mint shares: ``shares_minted = deposit_amount / share_price``
    - Withdrawals burn shares: ``shares_burned = withdrawal_amount / share_price``
    - PnL and funding change ``total_assets`` but not ``total_supply``
    - Share price starts at 1.00 at first deposit

    :param fills:
        List of fills sorted chronologically.
    :param funding_payments:
        List of funding payments sorted chronologically.
    :param ledger_events:
        List of VaultDepositEvent objects sorted chronologically.
        Import from :py:mod:`eth_defi.hyperliquid.deposit`.
    :param initial_total_assets:
        Starting total assets (usually 0 for a new vault).
    :return:
        List of SharePriceEvent objects in chronological order.
    """
    from eth_defi.hyperliquid.combined_analysis import (
        EPOCH_RESET_MIN_ASSETS,
        SHARE_PRICE_RESET_THRESHOLD,
    )
    from eth_defi.hyperliquid.deposit import VaultEventType

    # Build a unified event timeline
    # Each entry: (timestamp_ms, event_type, delta, coin)
    timeline: list[tuple[int, str, float, str | None]] = []

    # Add deposit/withdrawal events
    for event in ledger_events:
        if event.event_type == VaultEventType.vault_deposit:
            timeline.append(
                (
                    int(event.timestamp.timestamp() * 1000),
                    "deposit",
                    float(event.usdc),
                    None,
                )
            )
        elif event.event_type == VaultEventType.vault_withdraw:
            timeline.append(
                (
                    int(event.timestamp.timestamp() * 1000),
                    "withdraw",
                    float(event.usdc),  # Already negative from deposit.py
                    None,
                )
            )
        elif event.event_type == VaultEventType.vault_create:
            timeline.append(
                (
                    int(event.timestamp.timestamp() * 1000),
                    "deposit",
                    float(event.usdc),
                    None,
                )
            )

    # Add fill PnL events (only fills with non-zero closed_pnl)
    for fill in fills:
        if fill.closed_pnl != 0:
            timeline.append(
                (
                    fill.timestamp_ms,
                    "fill_pnl",
                    float(fill.closed_pnl),
                    fill.coin,
                )
            )

    # Add funding payment events
    for payment in funding_payments:
        if payment.usdc != 0:
            timeline.append(
                (
                    payment.timestamp_ms,
                    "funding",
                    float(payment.usdc),
                    payment.coin,
                )
            )

    # Sort by timestamp
    timeline.sort(key=lambda x: x[0])

    # Compute share prices
    total_assets = initial_total_assets
    total_supply = 0.0
    share_price = 1.0
    result: list[SharePriceEvent] = []

    for ts_ms, event_type, delta, coin in timeline:
        timestamp = datetime.datetime.fromtimestamp(ts_ms / 1000)
        epoch_reset = False

        if event_type in ("deposit", "withdraw"):
            # Capital flow: mint/burn shares
            total_assets += delta

            if total_supply == 0 and delta > 0:
                # First deposit: mint shares at current share price
                shares_change = delta / share_price
                total_supply += shares_change
            elif total_supply > 0 and share_price > 0:
                shares_change = delta / share_price
                total_supply += shares_change
                total_supply = max(0.0, total_supply)

        elif event_type in ("fill_pnl", "funding"):
            # PnL event: changes total_assets but not total_supply
            total_assets += delta

        # Compute share price
        if total_supply > 0:
            candidate_price = total_assets / total_supply
        else:
            candidate_price = 0.0

        # Epoch reset check (same logic as combined_analysis.py)
        if total_assets > EPOCH_RESET_MIN_ASSETS and (total_supply == 0 or candidate_price > SHARE_PRICE_RESET_THRESHOLD):
            epoch_anchor = share_price if share_price > 0 else 1.0
            total_supply = total_assets / epoch_anchor
            share_price = epoch_anchor
            epoch_reset = True
        elif total_supply > 0:
            share_price = candidate_price
        else:
            share_price = 1.0

        result.append(
            SharePriceEvent(
                timestamp=timestamp,
                event_type=event_type,
                total_assets=total_assets,
                total_supply=total_supply,
                share_price=share_price,
                delta=delta,
                epoch_reset=epoch_reset,
                coin=coin,
            )
        )

    return result


def create_share_price_dataframe(
    events: list[SharePriceEvent],
) -> pd.DataFrame:
    """Convert share price events to a pandas DataFrame.

    :param events:
        List of SharePriceEvent from :py:func:`compute_event_share_prices`.
    :return:
        DataFrame with timestamp index and columns for total_assets,
        total_supply, share_price, event_type, delta, epoch_reset.
    """
    if not events:
        return pd.DataFrame()

    rows = []
    timestamps = []
    for event in events:
        rows.append(
            {
                "event_type": event.event_type,
                "total_assets": event.total_assets,
                "total_supply": event.total_supply,
                "share_price": event.share_price,
                "delta": event.delta,
                "epoch_reset": event.epoch_reset,
                "coin": event.coin,
            }
        )
        timestamps.append(event.timestamp)

    return pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps, name="timestamp"))
