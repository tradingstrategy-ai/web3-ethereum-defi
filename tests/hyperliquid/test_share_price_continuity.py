"""Regression tests for Hyperliquid synthetic share-price continuity."""

import datetime
from decimal import Decimal

import pandas as pd
import pytest

from eth_defi.hyperliquid.combined_analysis import align_share_price_curve_to_anchor
from eth_defi.hyperliquid.daily_metrics import _merge_portfolio_periods
from eth_defi.hyperliquid.vault import PortfolioHistory


def _portfolio_history(
    period: str,
    rows: list[tuple[datetime.datetime, int, int]],
) -> PortfolioHistory:
    """Build a compact portfolio fixture from timestamp, NAV, and PnL rows."""
    return PortfolioHistory(
        period=period,
        account_value_history=[(timestamp, Decimal(nav)) for timestamp, nav, _pnl in rows],
        pnl_history=[(timestamp, Decimal(pnl)) for timestamp, _nav, pnl in rows],
        volume=Decimal(0),
    )


def test_merge_portfolio_periods_rebases_child_pnl_at_shared_timestamp() -> None:
    """A rolling PnL window must retain its trading movement after the merge."""
    start = datetime.datetime(2026, 1, 1)
    first_child_timestamp = start + datetime.timedelta(days=1)
    shared_timestamp = start + datetime.timedelta(days=2)
    portfolio = {
        "allTime": _portfolio_history("allTime", [(start, 1_000, 0), (shared_timestamp, 1_100, 100)]),
        "week": _portfolio_history("week", [(first_child_timestamp, 1_050, 5), (shared_timestamp, 1_100, 25)]),
    }

    result = _merge_portfolio_periods(portfolio, dedup_window_hours=1)

    assert dict(result.pnl_history)[first_child_timestamp] == Decimal(80)
    assert dict(result.pnl_history)[shared_timestamp] == Decimal(100)
    assert [timestamp for timestamp, _pnl in result.pnl_history] == [timestamp for timestamp, _nav in result.account_value_history]


def test_align_share_price_curve_interpolates_shifted_high_frequency_anchor() -> None:
    """A shifted HF re-read anchors in log-price space and preserves NAV."""
    index = pd.to_datetime(["2026-01-01 00:00", "2026-01-01 02:00"])
    curve = pd.DataFrame(
        {
            "share_price": [1.0, 1.2],
            "total_supply": [100.0, 100.0],
            "total_assets": [100.0, 120.0],
        },
        index=index,
    )

    aligned = align_share_price_curve_to_anchor(curve, pd.Timestamp("2026-01-01 01:00"), 1.1)

    assert aligned is not None
    midpoint = (aligned["share_price"].iloc[0] * aligned["share_price"].iloc[1]) ** 0.5
    assert midpoint == pytest.approx(1.1)
    assert (aligned["share_price"] * aligned["total_supply"]).to_numpy() == pytest.approx(aligned["total_assets"].to_numpy())


def test_align_share_price_curve_treats_zero_anchor_as_lifecycle_boundary() -> None:
    """A wiped-out epoch must not be used to scale a recapitalised curve."""
    index = pd.to_datetime(["2026-01-01", "2026-01-02"])
    curve = pd.DataFrame(
        {
            "share_price": [0.0, 1.0],
            "total_supply": [100.0, 100.0],
            "total_assets": [0.0, 100.0],
        },
        index=index,
    )

    resumed = align_share_price_curve_to_anchor(curve, index[0], 0.0)

    assert resumed is not curve
    pd.testing.assert_frame_equal(resumed, curve)
