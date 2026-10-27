"""Kestner's 2013 K-ratio for regularly sampled positive wealth.

The `Trading Strategy glossary <https://tradingstrategy.ai/glossary/k-ratio>`__
defines the log-wealth convention used here. The calculation follows the
Strategycore implementation, using vectorised NumPy instead of a JIT kernel.
"""

import math

import numpy as np
from numpy.typing import ArrayLike

#: OLS with an intercept and a slope needs at least one residual degree of freedom.
MINIMUM_K_RATIO_OBSERVATIONS = 3


def calculate_k_ratio(equity: ArrayLike, *, periods_per_year: float = 365.0) -> float:
    """Measure the consistency of compounded growth with the 2013 K-ratio.

    Fit ``log(equity[t]) = intercept + slope * t + residual[t]`` by ordinary
    least squares for ``t = 0, ..., n - 1``. The result is
    ``(slope / SE_OLS(slope)) * sqrt(periods_per_year) / n``, where
    ``SE_OLS(slope) = sqrt(sum(residual**2) / (n - 2) / sum((t - mean(t))**2))``.
    This is the scaling described by Lars N. Kestner in
    `(Re)Introducing the K-Ratio <https://ssrn.com/abstract=2230949>`__.

    Positive scores describe upward fitted log-wealth trends, and negative
    scores describe downward trends. Return order affects the result. No
    risk-free return is subtracted, and the score is a path diagnostic rather
    than a test of statistical significance. Compare the same sampling,
    formula and evaluation window alongside return and drawdown metrics.

    The centred regression uses the closed-form time sum of squares and
    calculates residuals directly to avoid subtracting nearly equal variances.
    Execution and working memory are O(n), without a design matrix or JIT
    compilation. The input is never modified.

    :param equity:
        One-dimensional real array-like of finite, strictly positive wealth
        or share prices, in chronological, equally spaced observation order.
        Lists, NumPy arrays and numeric pandas Series are accepted. A Series
        index is ignored: the caller must establish the calendar. Every point
        counts towards ``n``; no initial capital point is inserted and missing
        observations are never dropped. Fees and external cashflows must be
        reflected in the supplied wealth according to the caller's price basis.
    :param periods_per_year:
        Expected observations per year, finite and positive. Defaults to 365
        for daily crypto observations; use 252 for daily equities or 12 for
        monthly observations. This is not inferred from the input.
    :return:
        Python float. Returns ``nan`` for fewer than three observations or
        numerically zero residual variance, including constant wealth and
        perfectly exponential curves. The degeneracy tolerance matches
        Strategycore: residual RMS at most
        ``16 * float64_eps * max(1, max(abs(log(equity))))``. Nearly straight
        curves above this tolerance can have very large finite scores.
    :raises ValueError:
        If equity is not one-dimensional, contains complex, nonfinite or
        nonpositive values, or the annual frequency is invalid.
    """
    frequency_error = "periods_per_year must be finite and positive"
    try:
        frequency = float(periods_per_year)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(frequency_error) from exc
    if not math.isfinite(frequency) or frequency <= 0:
        raise ValueError(frequency_error)

    equity_error = "equity must be a one-dimensional real array of finite, strictly positive values"
    try:
        values = np.asarray(equity)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(equity_error) from exc
    if values.ndim != 1 or np.iscomplexobj(values):
        raise ValueError(equity_error)
    try:
        values = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(equity_error) from exc
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise ValueError(equity_error)
    n = values.size
    if n < MINIMUM_K_RATIO_OBSERVATIONS:
        return math.nan

    log_equity = np.log(values)
    magnitude = max(1.0, float(np.max(np.abs(log_equity))))
    # Subtract an anchor before centring to reduce cancellation from a large
    # wealth intercept, matching the Strategycore regression.
    centred_equity = log_equity - log_equity[0]
    centred_equity -= centred_equity.mean()
    centred_time = np.arange(n, dtype=np.float64) - (n - 1) / 2.0
    time_ss = n * (float(n) * n - 1.0) / 12.0
    slope = float(np.dot(centred_time, centred_equity) / time_ss)

    residuals = centred_equity - slope * centred_time
    residual_ss = float(np.dot(residuals, residuals))
    tolerance = 16.0 * np.finfo(np.float64).eps * magnitude
    if residual_ss <= n * tolerance * tolerance:
        return math.nan
    slope_se = math.sqrt(residual_ss / (n - 2) / time_ss)
    return (slope / slope_se / n) * math.sqrt(frequency)
