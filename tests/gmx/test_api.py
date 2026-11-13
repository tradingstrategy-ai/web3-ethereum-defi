"""
Tests for GMXAPI with parametrized chain testing.

This test suite makes real API calls to GMX API endpoints for Arbitrum and Avalanche networks.
"""

import functools

import pandas as pd
import pytest
from flaky import flaky

from eth_defi.gmx.api import GMXAPI
from tests.gmx.conftest import GMX_TEST_RETRY_CONFIG


def skip_on_gmx_v2_api_failure(func):
    """Skip a test when the GMX v2 REST API is unreachable.

    The GMX v2 API routes used here (``/pairs``, ``/tokens/info`` and
    ``/rates``) are an external service
    prone to transient outages and rate limiting from CI.  When
    :meth:`eth_defi.gmx.api.GMXAPI._make_v2_request` exhausts its internal
    retries it raises ``RuntimeError("GMX v2 API request failed after N
    attempts: <endpoint>")``.  Stacking this decorator under
    ``@flaky(max_runs=3, min_passes=1)`` is not enough — all three flaky
    retries run within seconds and fail back-to-back during a longer
    outage, masking the failure as a real regression.

    This decorator converts that specific ``RuntimeError`` into a
    :func:`pytest.skip`, mirroring the pattern already used by
    pre-fetch fixtures in ``tests/gmx/conftest.py``.  Other ``RuntimeError`` instances continue to fail the test.

    :param func: The test function to wrap.
    :return: Wrapped test that skips on GMX v2 API outage.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except RuntimeError as exc:
            if "GMX v2 API request failed" in str(exc):
                pytest.skip(f"GMX v2 API unreachable — transient outage: {exc}")
            raise

    return wrapper


@flaky(max_runs=3, min_passes=1)
def test_api_initialization(chain_name, gmx_config):
    """
    Test that the API initialises correctly with chain-specific config.
    """
    api = GMXAPI(gmx_config, retry_config=GMX_TEST_RETRY_CONFIG)
    assert api.chain.lower() == chain_name.lower()
    assert chain_name.lower() in api.base_url
    assert chain_name.lower() in api.backup_url


@flaky(max_runs=3, min_passes=1)
def test_get_candlesticks_dataframe(api):
    """
    Test retrieving historical price data as DataFrame.
    """
    # Test with ETH (common token on both chains)
    df = api.get_candlesticks_dataframe("ETH", period="1h")

    # Should be a pandas DataFrame
    assert isinstance(df, pd.DataFrame)

    # Should have the expected columns
    expected_columns = ["timestamp", "open", "high", "low", "close"]
    assert all(col in df.columns for col in expected_columns)

    # Should have reasonable number of rows (at least one candle)
    assert len(df) > 0

    # Timestamp should be datetime
    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])

    # Price columns should be numeric
    for col in ["open", "high", "low", "close"]:
        assert pd.api.types.is_numeric_dtype(df[col])


@flaky(max_runs=3, min_passes=1)
def test_get_apy_invalid_period(api):
    """Test that invalid period raises ValueError.

    Should raise ValueError for invalid time periods.
    """
    import pytest

    with pytest.raises(ValueError) as exc_info:
        api.get_apy(period="invalid_period", use_cache=False)

    assert "Invalid period" in str(exc_info.value)


# ---------------------------------------------------------------------------
# REST API v2 tests
# ---------------------------------------------------------------------------


@flaky(max_runs=3, min_passes=1)
def test_base_v2_url(chain_name, gmx_config):
    """Test that base_v2_url returns the correct URL for the configured chain.

    Only Arbitrum has a v2 endpoint; Avalanche returns an empty string for
    chains without a v2 URL configured.
    """
    api = GMXAPI(gmx_config, retry_config=GMX_TEST_RETRY_CONFIG)

    if chain_name.lower() == "arbitrum":
        assert "gmxapi.io" in api.base_v2_url
        assert api.base_v2_url.endswith("/v1")
    elif chain_name.lower() == "avalanche":
        # Avalanche v2 may or may not be configured — just check it's a string
        assert isinstance(api.base_v2_url, str)


@flaky(max_runs=3, min_passes=1)
def test_make_v2_request_unsupported_chain(gmx_config):
    """Test that _make_v2_request raises ValueError for an unsupported chain.

    When the GMX v2 API has no URL configured for a given chain the method
    should raise :class:`ValueError` rather than making an HTTP request.
    """
    import pytest

    api = GMXAPI(gmx_config, retry_config=GMX_TEST_RETRY_CONFIG)
    # Override chain to something without a v2 URL
    api.chain = "unsupported_test_chain"

    with pytest.raises(ValueError, match="No GMX v2 API URL configured"):
        api._make_v2_request("/pairs")


@flaky(max_runs=3, min_passes=1)
@skip_on_gmx_v2_api_failure
def test_get_pairs_caching(api):
    """Test that get_pairs returns cached data on the second call.

    The second call should return the same object from the module-level
    cache without making a new HTTP request.
    """
    first = api.get_pairs(use_cache=True)
    second = api.get_pairs(use_cache=True)

    assert first is second


@flaky(max_runs=3, min_passes=1)
@skip_on_gmx_v2_api_failure
def test_get_token_info_caching(api):
    """Test that get_token_info returns cached data on the second call."""
    first = api.get_token_info(use_cache=True)
    second = api.get_token_info(use_cache=True)

    assert first is second


@flaky(max_runs=3, min_passes=1)
@skip_on_gmx_v2_api_failure
def test_get_rates_caching(api):
    """Test that get_rates returns cached data on the second call."""
    first = api.get_rates(use_cache=True)
    second = api.get_rates(use_cache=True)

    assert first is second
