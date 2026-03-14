"""Standalone performance metric functions.

Lightweight, reusable functions for computing risk-adjusted return
metrics from daily returns or equity/price series. These are
independent of any specific data pipeline (vaults, traders, etc.)
and work with plain Pandas Series or scalar values.

For vault-specific metrics with fee adjustments, hourly data,
and multi-period tearsheets, see :py:mod:`eth_defi.research.vault_metrics`.

Example::

    import pandas as pd
    from eth_defi.research.perf_metrics import (
        compute_cagr,
        compute_sharpe,
        compute_sortino,
        compute_max_drawdown,
        compute_calmar,
    )

    # From an equity curve
    equity = pd.Series([100_000, 102_000, 101_500, 105_000, 110_000])
    max_dd = compute_max_drawdown(equity)

    # From daily returns
    daily_returns = equity.pct_change().dropna()
    sharpe = compute_sharpe(daily_returns)
    sortino = compute_sortino(daily_returns)

    # CAGR from start/end values and duration
    cagr = compute_cagr(start_value=100_000, end_value=110_000, days=90)
    calmar = compute_calmar(cagr, max_dd)
"""

import numpy as np
import pandas as pd


#: Maximum CAGR cap to prevent astronomical extrapolations
#: from very short time windows.
MAX_CAGR = 100.0

#: Minimum number of daily return observations required
#: for Sharpe and Sortino calculations.
MIN_RETURN_SAMPLES = 7


def compute_cagr(
    start_value: float,
    end_value: float,
    days: int,
) -> float | None:
    """Compute annualised compound annual growth rate (CAGR).

    :param start_value:
        Starting equity or price. Must be positive.
    :param end_value:
        Ending equity or price. Must be positive.
    :param days:
        Number of calendar days between start and end.
    :return:
        Annualised CAGR as a decimal (e.g. 0.25 = 25%),
        capped at :py:data:`MAX_CAGR`. Returns ``None`` if
        inputs are invalid (non-positive values or zero duration).
    """
    if days <= 0 or start_value <= 0 or end_value <= 0:
        return None
    years = days / 365.0
    if years < 0.001:
        return None
    base = end_value / start_value
    cagr = min(base ** (1.0 / years) - 1.0, MAX_CAGR)
    return cagr


def compute_sharpe(
    daily_returns: pd.Series,
    annualisation_factor: float = 365,
) -> float | None:
    """Compute annualised Sharpe ratio from daily returns.

    Uses zero risk-free rate (appropriate for crypto markets
    where opportunity cost is typically stablecoin yield).

    :param daily_returns:
        Series of daily percentage returns (e.g. from
        ``equity.pct_change()``). NaN values are dropped.
    :param annualisation_factor:
        Number of periods per year. Use 365 for daily crypto
        data (markets trade every day), 252 for traditional
        equities.
    :return:
        Annualised Sharpe ratio, or ``None`` if fewer than
        :py:data:`MIN_RETURN_SAMPLES` observations or zero
        standard deviation.
    """
    clean = daily_returns.dropna()
    if len(clean) < MIN_RETURN_SAMPLES:
        return None
    mean_r = clean.mean()
    std_r = clean.std()
    if std_r < 1e-12:
        return None
    return float((mean_r / std_r) * np.sqrt(annualisation_factor))


def compute_sortino(
    daily_returns: pd.Series,
    annualisation_factor: float = 365,
) -> float | None:
    """Compute annualised Sortino ratio from daily returns.

    Like Sharpe but uses only downside deviation (negative returns)
    in the denominator, rewarding upside volatility rather than
    penalising it.

    :param daily_returns:
        Series of daily percentage returns. NaN values are dropped.
    :param annualisation_factor:
        Number of periods per year (365 for crypto, 252 for equities).
    :return:
        Annualised Sortino ratio, or ``None`` if fewer than
        :py:data:`MIN_RETURN_SAMPLES` observations or zero
        downside deviation.
    """
    clean = daily_returns.dropna()
    if len(clean) < MIN_RETURN_SAMPLES:
        return None
    mean_r = clean.mean()
    downside = clean[clean < 0]
    if len(downside) < 2:
        # No negative returns — infinite Sortino
        return None
    downside_std = downside.std()
    if downside_std < 1e-12:
        return None
    return float((mean_r / downside_std) * np.sqrt(annualisation_factor))


def compute_max_drawdown(prices: pd.Series) -> float | None:
    """Compute maximum drawdown from a price or equity curve.

    :param prices:
        Series of absolute price or equity values (not returns).
        Must have at least 2 data points.
    :return:
        Maximum drawdown as a negative fraction (e.g. -0.15 = -15%
        drawdown). Returns ``None`` if fewer than 2 data points.
    """
    if len(prices) < 2:
        return None
    running_max = prices.cummax()
    drawdown = (prices - running_max) / running_max
    return float(drawdown.min())


def compute_calmar(
    cagr: float | None,
    max_drawdown: float | None,
) -> float | None:
    """Compute Calmar ratio from CAGR and maximum drawdown.

    :param cagr:
        Annualised CAGR as a decimal.
    :param max_drawdown:
        Maximum drawdown as a negative fraction
        (from :py:func:`compute_max_drawdown`).
    :return:
        Calmar ratio (CAGR / |max_drawdown|), or ``None`` if
        either input is ``None`` or max drawdown is near zero.
    """
    if cagr is None or max_drawdown is None or abs(max_drawdown) < 1e-12:
        return None
    return cagr / abs(max_drawdown)
