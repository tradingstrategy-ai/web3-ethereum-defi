"""
Tests for GMXAPI with parametrized chain testing.

This test suite makes real API calls to GMX API endpoints for Arbitrum and Avalanche networks.
"""

import pytest
import pandas as pd

from eth_defi.gmx.api import GMXAPI


@pytest.mark.flaky(reruns=3)
def test_api_initialization(chain_name, gmx_config):
    """
    Test that the API initializes correctly with chain-specific config.
    """
    api = GMXAPI(gmx_config)
    assert api.chain.lower() == chain_name.lower()
    assert chain_name.lower() in api.base_url
    assert chain_name.lower() in api.backup_url


@pytest.mark.flaky(reruns=3)
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


@pytest.mark.flaky(reruns=3)
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


@pytest.mark.flaky(reruns=3)
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


@pytest.mark.flaky(reruns=3)
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


@pytest.mark.flaky(reruns=3)
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


@pytest.mark.flaky(reruns=3)
def test_api_retry_mechanism(chain_name, gmx_config, monkeypatch):
    """
    Test that the API retries with backup URL on failure.

    This test mocks requests to simulate primary URL failure and backup URL success.
    """
    import requests
    from unittest.mock import Mock, patch

    api = GMXAPI(gmx_config)

    # Create a mock that fails on first call (primary URL) and succeeds on second (backup URL)
    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1

        mock_response = Mock()

        if call_count <= 2:  # First URL with retries (max_retries=2)
            # Simulate primary URL failure
            raise requests.exceptions.ConnectionError("Primary URL failed")
        else:
            # Backup URL succeeds
            mock_response.status_code = 200
            mock_response.json.return_value = []
            return mock_response

    with patch("requests.get", side_effect=mock_get):
        # This should fail on primary, then succeed on backup
        tickers = api.get_tickers()
        assert tickers is not None
        assert isinstance(tickers, list)
        # Verify that we tried primary (2 attempts) then backup
        assert call_count >= 3
