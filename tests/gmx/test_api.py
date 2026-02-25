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
