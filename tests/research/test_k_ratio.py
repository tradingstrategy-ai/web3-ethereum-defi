"""Independent regression references and input contracts for K-ratio."""

import numpy as np
import pandas as pd
import pytest
from scipy.stats import linregress

from eth_defi.research.k_ratio import calculate_k_ratio


@pytest.mark.parametrize("n", [3, 32, 365, 10_000])
@pytest.mark.parametrize("drift", [-0.003, 0.0, 0.003])
@pytest.mark.parametrize("periods_per_year", [12.0, 252.0, 365.0])
def test_k_ratio_matches_independent_regression(n: int, drift: float, periods_per_year: float) -> None:
    """Match the slope standard error from SciPy, including negative trends."""
    rng = np.random.default_rng(71)
    equity = 10_000 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    reference = linregress(np.arange(n), np.log(equity))
    expected = reference.slope / reference.stderr * np.sqrt(periods_per_year) / n

    result = calculate_k_ratio(equity, periods_per_year=periods_per_year)

    assert isinstance(result, float)
    assert result == pytest.approx(expected, rel=1e-10, abs=1e-12)


def test_k_ratio_wealth_scale_and_input_containers() -> None:
    """Preserve scale invariance and accept containers without changing them."""
    equity = np.array([100, 102, 101, 105, 104, 108])
    expected = calculate_k_ratio(equity)
    for scale in [1e-200, 1e-6, 1.0, 1e6, 1e200]:
        assert calculate_k_ratio(equity * scale) == pytest.approx(expected, rel=1e-10)
    assert calculate_k_ratio(equity.tolist()) == expected
    assert calculate_k_ratio(pd.Series(equity)) == expected
    strided = np.repeat(equity.astype(float), 2)[::2]
    strided.flags.writeable = False
    assert calculate_k_ratio(strided) == expected
    np.testing.assert_array_equal(strided, equity)


def test_k_ratio_depends_on_return_order() -> None:
    """Changing the wealth path changes K-ratio despite identical final wealth."""
    returns = np.array([0.05, 0.04, -0.03, -0.02, 0.06, 0.01])
    first = np.cumprod(1 + returns)
    second = np.cumprod(1 + returns[[0, 2, 1, 3, 4, 5]])
    assert first[-1] == pytest.approx(second[-1])
    assert calculate_k_ratio(first) != pytest.approx(calculate_k_ratio(second))


@pytest.mark.parametrize("equity", [[], [100], [100, 101], [100, 100, 100], np.exp(0.01 * np.arange(365)), np.exp(-0.01 * np.arange(365))])
def test_k_ratio_undefined_samples(equity: np.ndarray | list[float]) -> None:
    """Short, flat and perfectly log-linear curves have no defined ratio."""
    assert np.isnan(calculate_k_ratio(equity))


def test_k_ratio_resolvable_nearly_straight_curve() -> None:
    """Avoid cancelling tiny residual variance or capping a large valid score."""
    t = np.arange(365)
    equity = np.exp(0.001 * t + 1e-8 * np.sin(t))
    design = np.column_stack([np.ones(len(t)), t])
    intercept, slope = np.linalg.lstsq(design, np.log(equity), rcond=None)[0]
    residuals = np.log(equity) - intercept - slope * t
    slope_se = np.sqrt(np.dot(residuals, residuals) / (len(t) - 2) / np.sum((t - t.mean()) ** 2))
    expected = slope / slope_se * np.sqrt(365) / len(t)

    result = calculate_k_ratio(equity)

    assert np.isfinite(result)
    minimum_score = 1e6
    assert result > minimum_score
    assert result == pytest.approx(expected, rel=1e-8)


@pytest.mark.parametrize("equity", [100, [[100, 101, 102]], [[100], [101, 102]], [100, np.nan, 102], [100, np.inf, 102], [100, 0, 102], [100, -1, 102], [100 + 1j, 101, 102], ["bad", "values"], [np.nan]])
def test_k_ratio_rejects_invalid_equity(equity: object) -> None:
    """Reject invalid values without silently dropping observations."""
    with pytest.raises(ValueError, match="equity"):
        calculate_k_ratio(equity)


@pytest.mark.parametrize("frequency", [0, -1, np.nan, np.inf, None, "invalid", 1j])
def test_k_ratio_rejects_invalid_frequency(frequency: object) -> None:
    """Require a finite, positive annual observation frequency."""
    with pytest.raises(ValueError, match="periods_per_year"):
        calculate_k_ratio([100, 102, 101], periods_per_year=frequency)
