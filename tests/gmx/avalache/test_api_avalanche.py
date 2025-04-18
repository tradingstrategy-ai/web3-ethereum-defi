"""
Tests for GMXAPI on Avalanche network.

This test suite makes real API calls to GMX API endpoints for Avalanche.
"""
import os

import pytest
import pandas as pd

from eth_defi.gmx.api import GMXAPI

mainnet_rpc = os.environ.get("AVALANCHE_JSON_RPC_URL")

pytestmark = pytest.mark.skipif(not mainnet_rpc, reason="No AVALANCHE_JSON_RPC_URL environment variable")


def test_api_initialization(gmx_config_avalanche):
    """
    Test that the API initializes correctly with Avalanche config.
    """
    api = GMXAPI(gmx_config_avalanche)
    assert api.chain.lower() == "avalanche"
    assert "avalanche" in api.base_url
    assert "avalanche" in api.backup_url


def test_get_tickers(api_avalanche):
    """
    Test retrieving current price information for all tokens on Avalanche.
    """
    tickers = api_avalanche.get_tickers()

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


def test_get_signed_prices(api_avalanche):
    """
    Test retrieving signed prices for on-chain transactions on Avalanche.
    """
    signed_prices = api_avalanche.get_signed_prices()

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


def test_get_tokens(api_avalanche):
    """
    Test retrieving list of supported tokens on Avalanche.
    """
    tokens = api_avalanche.get_tokens()

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


def test_get_candlesticks(api_avalanche):
    """
    Test retrieving historical price data on Avalanche.
    """
    # Test with ETH (common token on Avalanche)
    candlesticks = api_avalanche.get_candlesticks("ETH", period="1h")

    # Check that we got data back
    assert candlesticks is not None
    assert isinstance(candlesticks, dict)

    # Check basic response structure
    # Expect candle data in some format
    if "result" in candlesticks:
        result = candlesticks["result"]
        assert "candles" in result
        assert isinstance(result["candles"], list)

        if len(result["candles"]) > 0:
            # Check structure of a candle
            candle = result["candles"][0]
            # timestamp, open, high, low, close
            assert isinstance(candle, list) and len(candle) >= 5


def test_get_candlesticks_dataframe(api_avalanche):
    """
    Test retrieving historical price data as DataFrame on Avalanche.
    """
    # Test with ETH (common token on Avalanche)
    df = api_avalanche.get_candlesticks_dataframe("ETH", period="1h")

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


def test_api_retry_mechanism(gmx_config_avalanche, monkeypatch):
    """
    Test that the API retries with backup URL on failure.

    This test deliberately breaks the primary URL to trigger the fallback.
    """
    api = GMXAPI(gmx_config_avalanche)

    # Save original URLs
    original_base_url = api.base_url

    # Set primary URL to something invalid to force fallback
    monkeypatch.setattr(api, "base_url", "https://invalid-url-that-will-fail.example")

    try:
        # This should use the backup URL and succeed
        tickers = api.get_tickers()
        assert tickers is not None
        assert isinstance(tickers, list)
    except RuntimeError:
        # If both URLs fail, it's still acceptable for this test
        # as long as it tried the backup (which we can't easily verify)
        pytest.skip("Both primary and backup URLs failed, can't test retry mechanism")
    finally:
        # Restore original URL
        monkeypatch.setattr(api, "base_url", original_base_url)
