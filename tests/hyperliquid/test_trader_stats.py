"""Tests for Hyperliquid trader performance statistics."""

from unittest.mock import Mock

import pandas as pd
import pytest

from eth_defi.hyperliquid.trader_stats import TraderStatsDatabase
from eth_defi.research.perf_metrics import compute_sharpe


def test_trader_metrics_regularise_sparse_daily_pnl() -> None:
    """Sharpe uses consecutive calendar days rather than active-day samples."""

    database = object.__new__(TraderStatsDatabase)
    query_result = Mock()
    query_result.fetchone.return_value = (2.0, 0.0, 0.5)
    database.cache_con = Mock()
    database.cache_con.execute.return_value = query_result

    address = "0x1234"
    daily_pnl = pd.DataFrame(
        {
            "address": [address, address, address],
            "trade_date": pd.to_datetime(["2026-01-01", "2026-01-05", "2026-01-10"]),
            "daily_net_pnl": [1.0, -1.0, 2.0],
        }
    )
    fill_aggregates = pd.DataFrame(
        {
            "address": [address],
            "label": ["test trader"],
            "fill_count": [20],
            "active_days": [9.0],
            "max_notional_exposure": [100.0],
        }
    )
    deposits = pd.DataFrame({"address": [address], "total_deposits": [100.0]})

    metrics = database._compute_trader_metrics(address, daily_pnl, fill_aggregates, deposits)

    calendar_pnl = pd.Series([1.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 2.0])
    equity = 100.0 + calendar_pnl.cumsum()
    expected_sharpe = compute_sharpe(equity.pct_change().dropna())
    assert metrics is not None
    assert metrics["active_days"] == 3
    assert metrics["net_pnl"] == pytest.approx(2.0)
    assert metrics["sharpe"] == pytest.approx(expected_sharpe)


def test_trader_metrics_do_not_treat_dollar_pnl_as_returns() -> None:
    """Sharpe and Sortino stay unavailable when starting capital is unknown."""

    database = object.__new__(TraderStatsDatabase)
    query_result = Mock()
    query_result.fetchone.return_value = (2.0, 0.0, 0.5)
    database.cache_con = Mock()
    database.cache_con.execute.return_value = query_result

    address = "0x1234"
    daily_pnl = pd.DataFrame(
        {
            "address": [address, address],
            "trade_date": pd.to_datetime(["2026-01-01", "2026-01-10"]),
            "daily_net_pnl": [1.0, 2.0],
        }
    )
    fill_aggregates = pd.DataFrame(
        {
            "address": [address],
            "label": ["test trader"],
            "fill_count": [20],
            "active_days": [9.0],
            "max_notional_exposure": [100.0],
        }
    )
    deposits = pd.DataFrame(columns=["address", "total_deposits"])

    metrics = database._compute_trader_metrics(address, daily_pnl, fill_aggregates, deposits)

    assert metrics is not None
    assert metrics["sharpe"] is None
    assert metrics["sortino"] is None
