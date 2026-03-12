"""Unit tests for Hyperliquid trade history reconstruction.

Tests trade grouping, funding attribution, and edge cases
without network access. Uses synthetic Fill and PositionEvent data.
"""

import datetime
from decimal import Decimal

import pytest

from eth_defi.hyperliquid.position import (
    Fill,
    PositionDirection,
    PositionEvent,
    PositionEventType,
)
from eth_defi.hyperliquid.trade_history import (
    FundingPayment,
    RoundTripTrade,
    attach_funding_to_trades,
    create_trade_summary_dataframe,
    group_fills_into_trades,
)


def _make_event(
    event_type: PositionEventType,
    coin: str,
    direction: PositionDirection,
    size: Decimal,
    price: Decimal,
    position_after: Decimal,
    timestamp: datetime.datetime,
    realized_pnl: Decimal | None = None,
    fee: Decimal = Decimal("0.5"),
) -> PositionEvent:
    """Helper to create a PositionEvent for testing."""
    return PositionEvent(
        event_type=event_type,
        coin=coin,
        direction=direction,
        size=size,
        price=price,
        timestamp=timestamp,
        position_after=position_after,
        realized_pnl=realized_pnl,
        fee=fee,
        fee_token="USDC",
    )


def _make_fill(
    coin: str,
    side: str,
    size: Decimal,
    price: Decimal,
    timestamp_ms: int,
    start_position: Decimal = Decimal("0"),
    closed_pnl: Decimal = Decimal("0"),
    trade_id: int = 1,
) -> Fill:
    """Helper to create a Fill for testing."""
    return Fill(
        coin=coin,
        side=side,
        size=size,
        price=price,
        timestamp_ms=timestamp_ms,
        start_position=start_position,
        closed_pnl=closed_pnl,
        direction_hint="",
        hash=None,
        order_id=None,
        trade_id=trade_id,
        fee=Decimal("0.5"),
        fee_token="USDC",
    )


def _make_funding(
    coin: str,
    usdc: Decimal,
    timestamp: datetime.datetime,
) -> FundingPayment:
    """Helper to create a FundingPayment for testing."""
    return FundingPayment(
        coin=coin,
        funding_rate=Decimal("0.0001"),
        usdc=usdc,
        position_size=Decimal("1.0"),
        timestamp=timestamp,
        timestamp_ms=int(timestamp.timestamp() * 1000),
    )


T0 = datetime.datetime(2026, 1, 1, 0, 0, 0)
T1 = datetime.datetime(2026, 1, 1, 1, 0, 0)
T2 = datetime.datetime(2026, 1, 1, 2, 0, 0)
T3 = datetime.datetime(2026, 1, 1, 3, 0, 0)
T4 = datetime.datetime(2026, 1, 1, 4, 0, 0)


def test_group_simple_round_trip():
    """Simple open→close produces one closed trade."""
    events = [
        _make_event(PositionEventType.open, "BTC", PositionDirection.long, Decimal("1.0"), Decimal("50000"), Decimal("1.0"), T0),
        _make_event(PositionEventType.close, "BTC", PositionDirection.long, Decimal("1.0"), Decimal("51000"), Decimal("0"), T1, realized_pnl=Decimal("1000")),
    ]

    closed, opened = group_fills_into_trades(iter(events))

    assert len(closed) == 1
    assert len(opened) == 0

    trade = closed[0]
    assert trade.coin == "BTC"
    assert trade.direction == PositionDirection.long
    assert not trade.is_open
    assert trade.is_complete
    assert trade.entry_price == Decimal("50000")
    assert trade.exit_price == Decimal("51000")
    assert trade.realised_pnl == Decimal("1000")
    assert trade.current_size == Decimal("0")
    assert trade.fill_count == 2
    assert trade.duration == T1 - T0


def test_group_with_scaling():
    """Open, increase, decrease, close produces one closed trade with VWAP prices."""
    events = [
        _make_event(PositionEventType.open, "ETH", PositionDirection.long, Decimal("5.0"), Decimal("2000"), Decimal("5.0"), T0),
        _make_event(PositionEventType.increase, "ETH", PositionDirection.long, Decimal("5.0"), Decimal("2100"), Decimal("10.0"), T1),
        _make_event(PositionEventType.decrease, "ETH", PositionDirection.long, Decimal("3.0"), Decimal("2200"), Decimal("7.0"), T2, realized_pnl=Decimal("450")),
        _make_event(PositionEventType.close, "ETH", PositionDirection.long, Decimal("7.0"), Decimal("2300"), Decimal("0"), T3, realized_pnl=Decimal("1750")),
    ]

    closed, opened = group_fills_into_trades(iter(events))

    assert len(closed) == 1
    trade = closed[0]
    assert trade.coin == "ETH"
    assert trade.fill_count == 4
    assert trade.max_size == Decimal("10.0")

    # VWAP entry: (5*2000 + 5*2100) / (5+5) = 20500/10 = 2050
    assert trade.entry_price == Decimal("2050")

    # VWAP exit: (3*2200 + 7*2300) / (3+7) = 22700/10 = 2270
    assert trade.exit_price == Decimal("2270")

    # Realised PnL
    assert trade.realised_pnl == Decimal("2200")


def test_group_position_flip():
    """Long→short flip produces a close and a new open."""
    events = [
        _make_event(PositionEventType.open, "BTC", PositionDirection.long, Decimal("1.0"), Decimal("50000"), Decimal("1.0"), T0),
        # Position flip: close long + open short
        _make_event(PositionEventType.close, "BTC", PositionDirection.long, Decimal("1.0"), Decimal("51000"), Decimal("0"), T1, realized_pnl=Decimal("1000")),
        _make_event(PositionEventType.open, "BTC", PositionDirection.short, Decimal("0.5"), Decimal("51000"), Decimal("-0.5"), T1),
    ]

    closed, opened = group_fills_into_trades(iter(events))

    assert len(closed) == 1
    assert len(opened) == 1

    assert closed[0].direction == PositionDirection.long
    assert closed[0].realised_pnl == Decimal("1000")

    assert opened[0].direction == PositionDirection.short
    assert opened[0].is_open
    assert opened[0].current_size == Decimal("0.5")


def test_group_never_closed():
    """Open + increase without close produces one open trade."""
    events = [
        _make_event(PositionEventType.open, "SOL", PositionDirection.short, Decimal("100"), Decimal("150"), Decimal("-100"), T0),
        _make_event(PositionEventType.increase, "SOL", PositionDirection.short, Decimal("50"), Decimal("155"), Decimal("-150"), T1),
    ]

    closed, opened = group_fills_into_trades(iter(events))

    assert len(closed) == 0
    assert len(opened) == 1

    trade = opened[0]
    assert trade.is_open
    assert trade.coin == "SOL"
    assert trade.current_size == Decimal("150")
    assert trade.max_size == Decimal("150")


def test_group_incomplete_trade():
    """Position existed before data window — flagged as incomplete."""
    fills = [
        _make_fill("BTC", "A", Decimal("0.5"), Decimal("52000"), 1000, start_position=Decimal("1.0"), trade_id=1),
        _make_fill("BTC", "A", Decimal("0.5"), Decimal("53000"), 2000, start_position=Decimal("0.5"), closed_pnl=Decimal("500"), trade_id=2),
    ]

    events = [
        _make_event(PositionEventType.decrease, "BTC", PositionDirection.long, Decimal("0.5"), Decimal("52000"), Decimal("0.5"), T0, realized_pnl=Decimal("250")),
        _make_event(PositionEventType.close, "BTC", PositionDirection.long, Decimal("0.5"), Decimal("53000"), Decimal("0"), T1, realized_pnl=Decimal("500")),
    ]

    closed, opened = group_fills_into_trades(iter(events), fills=fills)

    assert len(closed) == 1
    trade = closed[0]
    assert not trade.is_complete
    assert trade.entry_price == Decimal("0")  # Unknown


def test_group_empty_input():
    """Empty events produce empty results."""
    closed, opened = group_fills_into_trades(iter([]))
    assert closed == []
    assert opened == []


def test_group_multiple_coins():
    """Trades in different coins are tracked independently."""
    events = [
        _make_event(PositionEventType.open, "BTC", PositionDirection.long, Decimal("1"), Decimal("50000"), Decimal("1"), T0),
        _make_event(PositionEventType.open, "ETH", PositionDirection.short, Decimal("10"), Decimal("2000"), Decimal("-10"), T1),
        _make_event(PositionEventType.close, "BTC", PositionDirection.long, Decimal("1"), Decimal("51000"), Decimal("0"), T2, realized_pnl=Decimal("1000")),
        _make_event(PositionEventType.close, "ETH", PositionDirection.short, Decimal("10"), Decimal("1900"), Decimal("0"), T3, realized_pnl=Decimal("1000")),
    ]

    closed, opened = group_fills_into_trades(iter(events))

    assert len(closed) == 2
    assert len(opened) == 0
    coins = {t.coin for t in closed}
    assert coins == {"BTC", "ETH"}


def test_attach_funding_to_correct_trade():
    """Funding payments are attributed to the correct trade by coin and time."""
    trade_btc = RoundTripTrade(
        coin="BTC",
        direction=PositionDirection.long,
        is_open=False,
        is_complete=True,
        opened_at=T0,
        closed_at=T3,
        entry_price=Decimal("50000"),
        exit_price=Decimal("51000"),
        max_size=Decimal("1"),
        current_size=Decimal("0"),
        realised_pnl=Decimal("1000"),
        funding_pnl=Decimal("0"),
        total_fees=Decimal("1"),
        net_pnl=Decimal("999"),
        unrealised_pnl=None,
        fill_count=2,
        duration=T3 - T0,
    )
    trade_eth = RoundTripTrade(
        coin="ETH",
        direction=PositionDirection.short,
        is_open=True,
        is_complete=True,
        opened_at=T1,
        closed_at=None,
        entry_price=Decimal("2000"),
        exit_price=None,
        max_size=Decimal("10"),
        current_size=Decimal("10"),
        realised_pnl=Decimal("0"),
        funding_pnl=Decimal("0"),
        total_fees=Decimal("0.5"),
        net_pnl=Decimal("-0.5"),
        unrealised_pnl=None,
        fill_count=1,
    )

    funding = [
        _make_funding("BTC", Decimal("-5.0"), T1),  # BTC funding paid
        _make_funding("ETH", Decimal("3.0"), T2),  # ETH funding received
        _make_funding("BTC", Decimal("-4.0"), T2),  # More BTC funding paid
    ]

    attach_funding_to_trades([trade_btc], [trade_eth], funding)

    assert trade_btc.funding_pnl == Decimal("-9.0")
    assert len(trade_btc.funding_payments) == 2
    assert trade_btc.net_pnl == Decimal("1000") + Decimal("-9.0") - Decimal("1")

    assert trade_eth.funding_pnl == Decimal("3.0")
    assert len(trade_eth.funding_payments) == 1


def test_funding_outside_trade_window_ignored():
    """Funding payment after trade closes is not attributed."""
    trade = RoundTripTrade(
        coin="BTC",
        direction=PositionDirection.long,
        is_open=False,
        is_complete=True,
        opened_at=T0,
        closed_at=T1,
        entry_price=Decimal("50000"),
        exit_price=Decimal("51000"),
        max_size=Decimal("1"),
        current_size=Decimal("0"),
        realised_pnl=Decimal("1000"),
        funding_pnl=Decimal("0"),
        total_fees=Decimal("1"),
        net_pnl=Decimal("999"),
        unrealised_pnl=None,
        fill_count=2,
        duration=T1 - T0,
    )

    # Funding payment AFTER trade closed
    funding = [_make_funding("BTC", Decimal("-5.0"), T3)]

    attach_funding_to_trades([trade], [], funding)

    assert trade.funding_pnl == Decimal("0")
    assert len(trade.funding_payments) == 0


def test_create_trade_summary_dataframe():
    """DataFrame conversion produces correct columns and values."""
    trades = [
        RoundTripTrade(
            coin="BTC",
            direction=PositionDirection.long,
            is_open=False,
            is_complete=True,
            opened_at=T0,
            closed_at=T1,
            entry_price=Decimal("50000"),
            exit_price=Decimal("51000"),
            max_size=Decimal("1"),
            current_size=Decimal("0"),
            realised_pnl=Decimal("1000"),
            funding_pnl=Decimal("-5"),
            total_fees=Decimal("2"),
            net_pnl=Decimal("993"),
            unrealised_pnl=None,
            fill_count=2,
            duration=T1 - T0,
        ),
    ]

    df = create_trade_summary_dataframe(trades)

    assert len(df) == 1
    assert df.iloc[0]["coin"] == "BTC"
    assert df.iloc[0]["direction"] == "long"
    assert df.iloc[0]["entry_price"] == pytest.approx(50000)
    assert df.iloc[0]["exit_price"] == pytest.approx(51000)
    assert df.iloc[0]["net_pnl"] == pytest.approx(993)
    assert df.iloc[0]["is_complete"] == True  # noqa: E712


def test_create_trade_summary_dataframe_empty():
    """Empty trades list produces empty DataFrame."""
    df = create_trade_summary_dataframe([])
    assert len(df) == 0
