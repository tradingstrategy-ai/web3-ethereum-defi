"""
Tests for GMXMarketData on arbitrum network.

This test suite validates the functionality of the GMXMarketData class
when connecting to the arbitrum network. Each test focuses on a specific
method of the GMXMarketData class to ensure it returns properly structured data.
"""

import logging
import os
import pytest

mainnet_rpc = os.environ.get("ARBITRUM_JSON_RPC_URL")

pytestmark = pytest.mark.skipif(not mainnet_rpc, reason="No ARBITRUM_JSON_RPC_URL environment variable")

# Supress logs of gmx_python_sdk module

logger = logging.getLogger()
logger.setLevel(logging.WARN)


# Tests
def test_get_available_markets(market_data_arbitrum):
    """
    Test that we can retrieve the available markets on arbitrum.

    This verifies that the API call works and returns expected data structure.
    arbitrum may have different markets compared to Arbitrum.
    """
    markets = market_data_arbitrum.get_available_markets()

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


def test_get_available_liquidity(market_data_arbitrum):
    """
    Test that we can retrieve available liquidity for all markets on arbitrum.

    This verifies that liquidity data is returned in the expected format.
    arbitrum liquidity pools may differ from Arbitrum ones.
    """
    liquidity = market_data_arbitrum.get_available_liquidity()

    # Check that we got data back
    assert liquidity is not None
    assert isinstance(liquidity, dict)

    assert "long" in liquidity
    assert "short" in liquidity

    # Check structure of the returned data
    assert isinstance(liquidity["short"], dict)
    assert isinstance(liquidity["short"]["BTC"], float)


def test_get_borrow_apr(market_data_arbitrum):
    """
    Test that we can retrieve borrow APR data for all markets on arbitrum.

    This verifies that the APR data is returned in the expected format.
    arbitrum may have different borrowing rates than Arbitrum.
    """
    borrow_apr = market_data_arbitrum.get_borrow_apr()

    # Check that we got data back
    assert borrow_apr is not None
    assert isinstance(borrow_apr, dict)

    assert "long" in borrow_apr
    assert "short" in borrow_apr

    # Check structure of the returned data
    assert isinstance(borrow_apr["short"], dict)
    assert isinstance(borrow_apr["short"]["BTC"], float)


def test_get_claimable_fees(market_data_arbitrum):
    """
    Test that we can retrieve claimable fees information on arbitrum.

    This verifies that fee data is returned in the expected format.
    Fee structures may vary between arbitrum and Arbitrum.
    """
    fees = market_data_arbitrum.get_claimable_fees()

    # Check that we got data back
    assert fees is not None
    assert isinstance(fees, dict)

    # Basic structure check
    assert isinstance(fees["total_fees"], float)


def test_get_contract_tvl(market_data_arbitrum):
    """
    Test that we can retrieve contract TVL (Total Value Locked) on arbitrum.

    This verifies that TVL data is returned in the expected format.
    arbitrum may have different TVL metrics compared to Arbitrum.
    """
    tvl = market_data_arbitrum.get_contract_tvl()

    # Check that we got data back
    assert tvl is not None
    assert isinstance(tvl, dict)

    # Check for core TVL data fields
    assert "BTC" in tvl
    assert isinstance(tvl["BTC"], dict)


def test_get_funding_apr(market_data_arbitrum):
    """
    Test that we can retrieve funding rates for all markets on arbitrum.

    This verifies that funding rate data is returned in the expected format.
    Funding rates on arbitrum may differ from those on Arbitrum.
    """
    funding_apr = market_data_arbitrum.get_funding_apr()

    # Check that we got data back
    assert funding_apr is not None
    assert isinstance(funding_apr, dict)

    assert "long" in funding_apr
    assert "funding_apr" == funding_apr["parameter"]


def test_get_gm_price(market_data_arbitrum):
    """
    Test that we can retrieve GM (liquidity provider) token prices on arbitrum.

    This verifies that GM price data is returned in the expected format.
    GM token prices on arbitrum may differ from Arbitrum.
    """
    gm_prices = market_data_arbitrum.get_gm_price()

    # Check that we got data back
    assert gm_prices is not None
    assert isinstance(gm_prices, dict)

    assert isinstance(gm_prices["BTC"], float)
    assert "gm_prices" in gm_prices["parameter"]


def test_get_open_interest(market_data_arbitrum):
    """
    Test that we can retrieve open interest for all markets on arbitrum.

    This verifies that open interest data is returned in the expected format.
    arbitrum markets may have different open interest than Arbitrum.
    """
    open_interest = market_data_arbitrum.get_open_interest()

    # Check that we got data back
    assert open_interest is not None
    assert isinstance(open_interest, dict)

    assert isinstance(open_interest["long"]["BTC"], float)
    assert "open_interest" in open_interest["parameter"]


def test_get_oracle_prices(market_data_arbitrum):
    """
    Test that we can retrieve oracle prices for all assets on arbitrum.

    This verifies that oracle price data is returned in the expected format.
    arbitrum may use different oracles or have different assets than Arbitrum.
    """
    prices = market_data_arbitrum.get_oracle_prices()

    # Check that we got data back
    assert prices is not None
    assert isinstance(prices, dict)


def test_get_pool_tvl(market_data_arbitrum):
    """
    Test that we can retrieve pool TVL (Total Value Locked) on arbitrum.

    This verifies that pool TVL data is returned in the expected format.
    arbitrum liquidity pools may have different TVL metrics than Arbitrum.
    """
    pool_tvl = market_data_arbitrum.get_pool_tvl()

    # Check that we got data back
    assert pool_tvl is not None
    assert isinstance(pool_tvl, dict)

    assert "total_tvl" in pool_tvl
    assert isinstance(pool_tvl["total_tvl"]["BTC"], float)


# This test will fail. Ignore it untill https://github.com/snipermonke01/gmx_python_sdk/issues/6 is fixed
# def test_get_glv_stats(market_data_arbitrum):
#     """
#     Test that we can retrieve GLV (GMX Liquidity Vector) token statistics on arbitrum.
#
#     This verifies that GLV stats data is returned in the expected format.
#     GLV metrics may vary between arbitrum and Arbitrum.
#     """
#     glv_stats = market_data_arbitrum.get_glv_stats()
#
#     # Check that we got data back
#     assert glv_stats is not None
#     assert isinstance(glv_stats, dict)


def test_get_user_positions(market_data_arbitrum):
    """
    Test that we can retrieve user positions with a valid address on arbitrum.

    This test uses a test address to verify the API call works correctly.
    Users may have different positions on arbitrum compared to Arbitrum.
    """
    # Use a test address that may have positions on arbitrum
    test_address = "0xf75cD383A1C59f43bab52ADD648EDF5B1B75E2Bf"

    positions = market_data_arbitrum.get_user_positions(address=test_address)

    # Check that we got data back in expected format
    assert positions is not None
    assert isinstance(positions, dict)
