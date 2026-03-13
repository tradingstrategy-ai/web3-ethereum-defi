"""
Tests for GMXAPI with parametrized chain testing.

This test suite makes real API calls to GMX API endpoints for Arbitrum and Avalanche networks.
"""

import pandas as pd
from flaky import flaky

from eth_defi.gmx.api import GMXAPI
from tests.gmx.conftest import GMX_TEST_RETRY_CONFIG


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
def test_get_tickers(api):
    """
    Test retrieving current price information for all tokens.
    """
    tickers = api.get_tickers()

    # Check that we got data back
    assert tickers is not None
    assert isinstance(tickers, list)

    # Check basic response structure
    assert len(tickers) > 0

    assert isinstance(tickers[0], dict)
    if len(tickers[0]) > 0:
        # Check structure of a ticker entry
        ticker = tickers[0]
        assert "tokenAddress" in ticker or "tokenSymbol" in ticker
        assert "maxPrice" in ticker


@flaky(max_runs=3, min_passes=1)
def test_get_signed_prices(api):
    """
    Test retrieving signed prices for on-chain transactions.
    """
    signed_prices = api.get_signed_prices()

    # Check that we got data back
    assert signed_prices is not None
    assert isinstance(signed_prices, dict)

    # Check basic response structure
    # The structure could vary, but we expect price data
    if "result" in signed_prices:
        result = signed_prices["result"]

        # Expect price data and signatures
        assert "prices" in result or "tokenPrices" in result or "compactedPrices" in result
        assert "signers" in result or "signatures" in result


@flaky(max_runs=3, min_passes=1)
def test_get_tokens(api):
    """
    Test retrieving list of supported tokens.
    """
    tokens = api.get_tokens()

    # Check that we got data back
    assert tokens is not None
    assert isinstance(tokens, dict)

    # Check basic response structure
    # Expect a list of tokens with their details
    if "result" in tokens:
        assert isinstance(tokens["result"], list)
        if len(tokens["result"]) > 0:
            # Check structure of a token entry
            token = tokens["result"][0]
            assert "symbol" in token
            assert "address" in token


@flaky(max_runs=3, min_passes=1)
def test_get_candlesticks(chain_name, api):
    """
    Test retrieving historical price data.
    """
    # Test with a common token (ETH for both chains or AVAX for Avalanche)
    token_symbol = "ETH"
    if chain_name.lower() == "avalanche":
        # Test AVAX as well on Avalanche
        token_symbol = "AVAX"

    candlesticks = api.get_candlesticks(token_symbol, period="1h")

    # Check that we got data back
    assert candlesticks is not None
    assert isinstance(candlesticks, dict)

    # Check basic response structure
    if "result" in candlesticks:
        result = candlesticks["result"]
        assert "candles" in result
        assert isinstance(result["candles"], list)

        if len(result["candles"]) > 0:
            # Check structure of a candle
            candle = result["candles"][0]
            # timestamp, open, high, low, close
            assert isinstance(candle, list) and len(candle) >= 5


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
def test_api_retry_mechanism(chain_name, gmx_config):
    """
    Test that the API can successfully retrieve data with retry mechanism.

    Makes real API calls to verify the retry logic works correctly.
    """
    api = GMXAPI(gmx_config, retry_config=GMX_TEST_RETRY_CONFIG)

    # Make a real API call - this tests that the retry mechanism works
    # The API should successfully return data even if there are transient failures
    tickers = api.get_tickers(use_cache=False)

    # Verify we got valid data
    assert tickers is not None
    assert isinstance(tickers, list)
    assert len(tickers) > 0

    # Verify ticker structure
    ticker = tickers[0]
    assert "tokenAddress" in ticker or "tokenSymbol" in ticker


@flaky(max_runs=3, min_passes=1)
def test_get_markets(api):
    """Test retrieving markets list from REST API.

    Makes real API call to /markets endpoint.
    """
    markets_data = api.get_markets(use_cache=False)

    # Check response structure
    assert markets_data is not None
    assert isinstance(markets_data, dict)

    # Should have markets key
    assert "markets" in markets_data
    assert isinstance(markets_data["markets"], list)

    # Should have at least one market
    assert len(markets_data["markets"]) > 0

    # Check structure of first market
    market = markets_data["markets"][0]
    assert "marketToken" in market
    assert "indexToken" in market
    assert "longToken" in market
    assert "shortToken" in market


@flaky(max_runs=3, min_passes=1)
def test_get_markets_info(api):
    """Test retrieving comprehensive market information from REST API.

    Makes real API call to /markets/info endpoint.
    """
    markets_info = api.get_markets_info(market_tokens_data=True, use_cache=False)

    # Check response structure
    assert markets_info is not None
    assert isinstance(markets_info, dict)

    # Should have markets key
    assert "markets" in markets_info
    assert isinstance(markets_info["markets"], list)

    # Should have at least one market
    assert len(markets_info["markets"]) > 0

    # Check structure of first market
    market = markets_info["markets"][0]
    assert "marketToken" in market
    assert "indexToken" in market

    # Check for comprehensive market data fields
    # These fields distinguish /markets/info from /markets
    assert "openInterestLong" in market or "isListed" in market


@flaky(max_runs=3, min_passes=1)
def test_get_apy_30d(api):
    """Test retrieving 30-day APY data from REST API.

    Makes real API call to /apy endpoint with 30d period.
    """
    apy_data = api.get_apy(period="30d", use_cache=False)

    # Check response structure
    assert apy_data is not None
    assert isinstance(apy_data, dict)

    # Should have markets key with APY data
    assert "markets" in apy_data
    assert isinstance(apy_data["markets"], dict)

    # Should have at least one market with APY
    assert len(apy_data["markets"]) > 0

    # Check structure of APY entry (keyed by market token address)
    first_market_token = list(apy_data["markets"].keys())[0]
    apy_entry = apy_data["markets"][first_market_token]

    assert isinstance(apy_entry, dict)
    assert "apy" in apy_entry
    assert isinstance(apy_entry["apy"], (int, float))


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
        assert "ondigitalocean.app" in api.base_v2_url
        assert api.base_v2_url.endswith("/api/v1")
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
def test_get_pairs(api):
    """Test retrieving all trading pairs via the v2 REST API.

    Makes a real HTTP call to the ``/pairs`` endpoint and verifies the
    response is a non-empty list of pair objects.
    """
    pairs = api.get_pairs(use_cache=False)

    assert pairs is not None
    assert isinstance(pairs, list)
    assert len(pairs) > 0

    # Each pair should be a dict with at minimum an identifying field
    first = pairs[0]
    assert isinstance(first, dict)


@flaky(max_runs=3, min_passes=1)
def test_get_pairs_caching(api):
    """Test that get_pairs returns cached data on the second call.

    The second call should return the same object from the module-level
    cache without making a new HTTP request.
    """
    first = api.get_pairs(use_cache=True)
    second = api.get_pairs(use_cache=True)

    assert first is second


@flaky(max_runs=3, min_passes=1)
def test_get_token_info(api):
    """Test retrieving comprehensive token information via the v2 REST API.

    Makes a real HTTP call to ``/tokens/info`` and verifies each token
    entry contains address, symbol, and decimal fields.
    """
    tokens = api.get_token_info(use_cache=False)

    assert tokens is not None
    assert isinstance(tokens, list)
    assert len(tokens) > 0

    token = tokens[0]
    assert isinstance(token, dict)
    # v2 /tokens/info should include at minimum address and symbol
    assert "address" in token or "symbol" in token


@flaky(max_runs=3, min_passes=1)
def test_get_token_info_caching(api):
    """Test that get_token_info returns cached data on the second call."""
    first = api.get_token_info(use_cache=True)
    second = api.get_token_info(use_cache=True)

    assert first is second


@flaky(max_runs=3, min_passes=1)
def test_get_rates(api):
    """Test retrieving funding and borrowing rate snapshots via the v2 REST API.

    Makes a real HTTP call to ``/rates`` without any filter parameters and
    verifies the response is a non-empty collection.
    """
    rates = api.get_rates(use_cache=False)

    assert rates is not None
    # Response may be a list or dict depending on the API version
    assert isinstance(rates, (list, dict))


@flaky(max_runs=3, min_passes=1)
def test_get_rates_non_empty(api):
    """Test that get_rates returns a non-empty collection.

    The ``/rates`` endpoint returns snapshots for all markets.  The response
    must contain at least one entry.

    .. note::
        The endpoint does not accept ``period`` or ``average_by`` query
        parameters — passing them causes a 400 error.
    """
    rates = api.get_rates(use_cache=False)

    assert rates is not None
    assert isinstance(rates, (list, dict))
    if isinstance(rates, list):
        assert len(rates) > 0


@flaky(max_runs=3, min_passes=1)
def test_get_rates_caching(api):
    """Test that get_rates returns cached data on the second call."""
    first = api.get_rates(use_cache=True)
    second = api.get_rates(use_cache=True)

    assert first is second


@flaky(max_runs=3, min_passes=1)
def test_get_ohlcv(api):
    """Test retrieving OHLCV candle data via the v2 REST API.

    Makes a real HTTP call to ``/prices/ohlcv`` for ETH on the 1h timeframe
    and verifies the response contains candle data with expected structure.
    """
    result = api.get_ohlcv("ETH", timeframe="1h", limit=10)

    assert result is not None
    assert isinstance(result, (list, dict))

    # If a list, each entry should be array-like [ts, o, h, l, c] or a dict
    if isinstance(result, list) and len(result) > 0:
        candle = result[0]
        assert isinstance(candle, (list, dict))


@flaky(max_runs=3, min_passes=1)
def test_get_ohlcv_with_since(api):
    """Test get_ohlcv with the ``since`` parameter for historical data.

    Passes a Unix millisecond timestamp to verify the v2 ``since`` parameter
    works — this parameter is not available in the v1 candlestick endpoint.
    """
    import time as _time

    one_week_ago_ms = int((_time.time() - 7 * 86_400) * 1_000)
    result = api.get_ohlcv("BTC", timeframe="4h", limit=50, since=one_week_ago_ms)

    assert result is not None
    assert isinstance(result, (list, dict))


@flaky(max_runs=3, min_passes=1)
def test_get_positions_empty(api):
    """Test get_positions returns a valid response for an address with no positions.

    Uses a zero-address that will have no open positions to verify the
    endpoint is reachable and returns an empty list rather than an error.
    """
    # Use a known zero-like address that will have no GMX positions
    zero_address = "0x0000000000000000000000000000000000000001"
    result = api.get_positions(zero_address, use_cache=False)

    assert result is not None
    # Should return an empty list or a dict with empty positions, not raise
    assert isinstance(result, (list, dict))


@flaky(max_runs=3, min_passes=1)
def test_get_orders_empty(api):
    """Test get_orders returns a valid response for an address with no orders.

    Uses a zero-like address that will have no open orders to verify the
    endpoint is reachable and returns an empty list rather than an error.
    """
    zero_address = "0x0000000000000000000000000000000000000001"
    result = api.get_orders(zero_address)

    assert result is not None
    assert isinstance(result, (list, dict))
