"""
Tests for GMXMarketData with parametrized chain testing.

This test suite validates the functionality of the GMXMarketData class
across different chains. Each test focuses on a specific method
to ensure it returns properly structured data.
"""
import logging
import pytest

# Suppress logs of gmx_python_sdk module
logger = logging.getLogger()
logger.setLevel(logging.WARN)


def test_get_available_markets(chain_name, market_data):
    """
    Test that we can retrieve the available markets.

    This verifies that the API call works and returns expected data structure.
    Different chains may have different markets.
    """
    markets = market_data.get_available_markets()

    # Check that we got data back
    assert markets is not None
    assert isinstance(markets, dict)

    # Check that the data contains expected market information
    for market_key, market_data in markets.items():
        assert isinstance(market_key, str)
        assert isinstance(market_data, dict)

        # Check for key fields in market data
        assert "gmx_market_address" in market_data
        assert "long_token_metadata" in market_data
        assert "short_token_metadata" in market_data


def test_get_available_liquidity(market_data):
    """
    Test that we can retrieve available liquidity for all markets.

    This verifies that liquidity data is returned in the expected format.
    Different chains may have different liquidity pools.
    """
    liquidity = market_data.get_available_liquidity()

    # Check that we got data back
    assert liquidity is not None
    assert isinstance(liquidity, dict)

    assert "long" in liquidity
    assert "short" in liquidity

    # Check structure of the returned data
    assert isinstance(liquidity["short"], dict)
    assert isinstance(liquidity["short"]["BTC"], float)


def test_get_borrow_apr(market_data):
    """
    Test that we can retrieve borrow APR data for all markets.

    This verifies that the APR data is returned in the expected format.
    Different chains may have different borrowing rates.
    """
    borrow_apr = market_data.get_borrow_apr()

    # Check that we got data back
    assert borrow_apr is not None
    assert isinstance(borrow_apr, dict)

    assert "long" in borrow_apr
    assert "short" in borrow_apr

    # Check structure of the returned data
    assert isinstance(borrow_apr["short"], dict)
    assert isinstance(borrow_apr["short"]["BTC"], float)


def test_get_claimable_fees(market_data):
    """
    Test that we can retrieve claimable fees information.

    This verifies that fee data is returned in the expected format.
    Fee structures may vary between chains.
    """
    fees = market_data.get_claimable_fees()

    # Check that we got data back
    assert fees is not None
    assert isinstance(fees, dict)

    # Basic structure check
    assert isinstance(fees["total_fees"], float)


def test_get_contract_tvl(market_data):
    """
    Test that we can retrieve contract TVL (Total Value Locked).

    This verifies that TVL data is returned in the expected format.
    Different chains may have different TVL metrics.
    """
    tvl = market_data.get_contract_tvl()

    # Check that we got data back
    assert tvl is not None
    assert isinstance(tvl, dict)

    # Check for core TVL data fields
    assert "BTC" in tvl
    assert isinstance(tvl["BTC"], dict)


def test_get_funding_apr(market_data):
    """
    Test that we can retrieve funding rates for all markets.

    This verifies that funding rate data is returned in the expected format.
    Funding rates may differ between chains.
    """
    funding_apr = market_data.get_funding_apr()

    # Check that we got data back
    assert funding_apr is not None
    assert isinstance(funding_apr, dict)

    assert "long" in funding_apr
    assert "funding_apr" == funding_apr["parameter"]


def test_get_gm_price(market_data):
    """
    Test that we can retrieve GM (liquidity provider) token prices.

    This verifies that GM price data is returned in the expected format.
    GM token prices may differ between chains.
    """
    gm_prices = market_data.get_gm_price()

    # Check that we got data back
    assert gm_prices is not None
    assert isinstance(gm_prices, dict)

    assert isinstance(gm_prices["BTC"], float)
    assert "gm_prices" in gm_prices["parameter"]


def test_get_open_interest(market_data):
    """
    Test that we can retrieve open interest for all markets.

    This verifies that open interest data is returned in the expected format.
    Different chains may have different open interest.
    """
    open_interest = market_data.get_open_interest()

    # Check that we got data back
    assert open_interest is not None
    assert isinstance(open_interest, dict)

    assert isinstance(open_interest["long"]["BTC"], float)
    assert "open_interest" in open_interest["parameter"]


def test_get_oracle_prices(market_data):
    """
    Test that we can retrieve oracle prices for all assets.

    This verifies that oracle price data is returned in the expected format.
    Different chains may use different oracles or have different assets.
    """
    prices = market_data.get_oracle_prices()

    # Check that we got data back
    assert prices is not None
    assert isinstance(prices, dict)


def test_get_pool_tvl(market_data):
    """
    Test that we can retrieve pool TVL (Total Value Locked).

    This verifies that pool TVL data is returned in the expected format.
    Different chains may have different TVL metrics.
    """
    pool_tvl = market_data.get_pool_tvl()

    # Check that we got data back
    assert pool_tvl is not None
    assert isinstance(pool_tvl, dict)

    assert "total_tvl" in pool_tvl
    assert isinstance(pool_tvl["total_tvl"]["BTC"], float)


def test_get_user_positions(chain_name, market_data):
    """
    Test that we can retrieve user positions with a valid address.

    This test uses a test address to verify the API call works correctly.
    Users may have different positions on different chains.
    """
    # Use a test address that may have positions
    test_address = "0xf75cD383A1C59f43bab52ADD648EDF5B1B75E2Bf"

    positions = market_data.get_user_positions(address=test_address)

    # Check that we got data back in expected format
    assert positions is not None
    assert isinstance(positions, dict)


def test_get_glv_stats(chain_name, market_data):
    """
    Test that we can retrieve GLV (GMX Liquidity Vector) token statistics.

    This verifies that GLV stats data is returned in the expected format.
    GLV metrics may vary between chains.
    """
    # This test may fail on Arbitrum due to a known issue
    # https://github.com/snipermonke01/gmx_python_sdk/issues/6
    try:
        glv_stats = market_data.get_glv_stats()

        # Check that we got data back
        assert glv_stats is not None
        assert isinstance(glv_stats, dict)
    except Exception as e:
        if chain_name == "arbitrum":
            pytest.skip(f"Known issue with GLV stats on Arbitrum: {str(e)}")
        else:
            raise
