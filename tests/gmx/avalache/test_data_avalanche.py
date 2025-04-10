"""
Tests for GMXMarketData on Avalanche network.

This test suite validates the functionality of the GMXMarketData class
when connecting to the Avalanche network. Each test focuses on a specific
method of the GMXMarketData class to ensure it returns properly structured data.
"""

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.data import GMXMarketData


# Tests
def test_get_available_markets(market_data_avalanche):
    """
    Test that we can retrieve the available markets on Avalanche.

    This verifies that the API call works and returns expected data structure.
    Avalanche may have different markets compared to Arbitrum.
    """
    markets = market_data_avalanche.get_available_markets()

    # Check that we got data back
    assert markets is not None
    assert isinstance(markets, dict)

    # Check that the data contains expected market information
    for market_key, market_data in markets.items():
        assert isinstance(market_key, str)
        assert isinstance(market_data, dict)

        # Check for key fields in market data
        assert "longToken" in market_data
        assert "shortToken" in market_data
        assert "indexToken" in market_data


def test_get_available_liquidity(market_data_avalanche):
    """
    Test that we can retrieve available liquidity for all markets on Avalanche.

    This verifies that liquidity data is returned in the expected format.
    Avalanche liquidity pools may differ from Arbitrum ones.
    """
    liquidity = market_data_avalanche.get_available_liquidity()

    # Check that we got data back
    assert liquidity is not None
    assert isinstance(liquidity, dict)

    # Check structure of the returned data
    for market_key, liquidity_data in liquidity.items():
        assert isinstance(market_key, str)
        assert isinstance(liquidity_data, dict)

        # Each market should have long and short liquidity
        assert "longLiquidity" in liquidity_data
        assert "shortLiquidity" in liquidity_data

        # These should be numeric values
        assert isinstance(liquidity_data["longLiquidity"], (int, float))
        assert isinstance(liquidity_data["shortLiquidity"], (int, float))


def test_get_borrow_apr(market_data_avalanche):
    """
    Test that we can retrieve borrow APR data for all markets on Avalanche.

    This verifies that the APR data is returned in the expected format.
    Avalanche may have different borrowing rates than Arbitrum.
    """
    borrow_apr = market_data_avalanche.get_borrow_apr()

    # Check that we got data back
    assert borrow_apr is not None
    assert isinstance(borrow_apr, dict)

    # Check structure of the returned data
    for market_key, apr_data in borrow_apr.items():
        assert isinstance(market_key, str)
        assert isinstance(apr_data, dict)

        # Each market should have long and short APR
        assert "longBorrowingRate" in apr_data
        assert "shortBorrowingRate" in apr_data

        # APRs should be numeric values
        assert isinstance(apr_data["longBorrowingRate"], (int, float))
        assert isinstance(apr_data["shortBorrowingRate"], (int, float))


def test_get_claimable_fees(market_data_avalanche):
    """
    Test that we can retrieve claimable fees information on Avalanche.

    This verifies that fee data is returned in the expected format.
    Fee structures may vary between Avalanche and Arbitrum.
    """
    fees = market_data_avalanche.get_claimable_fees()

    # Check that we got data back
    assert fees is not None
    assert isinstance(fees, dict)

    # Basic structure check
    # Exact structure will depend on the actual implementation
    if "markets" in fees:
        assert isinstance(fees["markets"], dict)


def test_get_contract_tvl(market_data_avalanche):
    """
    Test that we can retrieve contract TVL (Total Value Locked) on Avalanche.

    This verifies that TVL data is returned in the expected format.
    Avalanche may have different TVL metrics compared to Arbitrum.
    """
    tvl = market_data_avalanche.get_contract_tvl()

    # Check that we got data back
    assert tvl is not None
    assert isinstance(tvl, dict)

    # Check for core TVL data fields
    assert "totalUsd" in tvl
    assert isinstance(tvl["totalUsd"], (int, float))


def test_get_funding_apr(market_data_avalanche):
    """
    Test that we can retrieve funding rates for all markets on Avalanche.

    This verifies that funding rate data is returned in the expected format.
    Funding rates on Avalanche may differ from those on Arbitrum.
    """
    funding_apr = market_data_avalanche.get_funding_apr()

    # Check that we got data back
    assert funding_apr is not None
    assert isinstance(funding_apr, dict)

    # Check structure of the returned data
    for market_key, funding_data in funding_apr.items():
        assert isinstance(market_key, str)
        assert isinstance(funding_data, dict)

        # Each market should have long and short funding rates
        assert "longFundingRate" in funding_data
        assert "shortFundingRate" in funding_data

        # Rates should be numeric values
        assert isinstance(funding_data["longFundingRate"], (int, float))
        assert isinstance(funding_data["shortFundingRate"], (int, float))


def test_get_gm_price(market_data_avalanche):
    """
    Test that we can retrieve GM (liquidity provider) token prices on Avalanche.

    This verifies that GM price data is returned in the expected format.
    GM token prices on Avalanche may differ from Arbitrum.
    """
    gm_prices = market_data_avalanche.get_gm_price()

    # Check that we got data back
    assert gm_prices is not None
    assert isinstance(gm_prices, dict)

    # Basic structure check
    # Exact structure will depend on the implementation
    for market_key, price_data in gm_prices.items():
        assert isinstance(market_key, str)
        if isinstance(price_data, dict) and "gmTokenPrice" in price_data:
            assert isinstance(price_data["gmTokenPrice"], (int, float))


def test_get_open_interest(market_data_avalanche):
    """
    Test that we can retrieve open interest for all markets on Avalanche.

    This verifies that open interest data is returned in the expected format.
    Avalanche markets may have different open interest than Arbitrum.
    """
    open_interest = market_data_avalanche.get_open_interest()

    # Check that we got data back
    assert open_interest is not None
    assert isinstance(open_interest, dict)

    # Check structure of the returned data
    for market_key, interest_data in open_interest.items():
        assert isinstance(market_key, str)
        assert isinstance(interest_data, dict)

        # Each market should have long and short interest
        assert "longOpenInterest" in interest_data
        assert "shortOpenInterest" in interest_data

        # Interest values should be numeric
        assert isinstance(interest_data["longOpenInterest"], (int, float))
        assert isinstance(interest_data["shortOpenInterest"], (int, float))


def test_get_oracle_prices(market_data_avalanche):
    """
    Test that we can retrieve oracle prices for all assets on Avalanche.

    This verifies that oracle price data is returned in the expected format.
    Avalanche may use different oracles or have different assets than Arbitrum.
    """
    prices = market_data_avalanche.get_oracle_prices()

    # Check that we got data back
    assert prices is not None
    assert isinstance(prices, dict)

    # Each price entry should have numeric values
    for token, price_data in prices.items():
        assert isinstance(token, str)
        if isinstance(price_data, dict):
            if "min" in price_data:
                assert isinstance(price_data["min"], (int, float))
            if "max" in price_data:
                assert isinstance(price_data["max"], (int, float))


def test_get_pool_tvl(market_data_avalanche):
    """
    Test that we can retrieve pool TVL (Total Value Locked) on Avalanche.

    This verifies that pool TVL data is returned in the expected format.
    Avalanche liquidity pools may have different TVL metrics than Arbitrum.
    """
    pool_tvl = market_data_avalanche.get_pool_tvl()

    # Check that we got data back
    assert pool_tvl is not None
    assert isinstance(pool_tvl, dict)

    # Check for core TVL data fields
    assert "totalUsd" in pool_tvl
    assert isinstance(pool_tvl["totalUsd"], (int, float))


def test_get_glv_stats(market_data_avalanche):
    """
    Test that we can retrieve GLV (GMX Liquidity Vector) token statistics on Avalanche.

    This verifies that GLV stats data is returned in the expected format.
    GLV metrics may vary between Avalanche and Arbitrum.
    """
    glv_stats = market_data_avalanche.get_glv_stats()

    # Check that we got data back
    assert glv_stats is not None
    assert isinstance(glv_stats, dict)

    # Check for key GLV stats fields
    # Actual field names may vary based on implementation
    if "price" in glv_stats:
        assert isinstance(glv_stats["price"], (int, float))
    if "supply" in glv_stats:
        assert isinstance(glv_stats["supply"], (int, float))


def test_get_user_positions(market_data_avalanche):
    """
    Test that we can retrieve user positions with a valid address on Avalanche.

    This test uses a test address to verify the API call works correctly.
    Users may have different positions on Avalanche compared to Arbitrum.
    """
    # Use a test address that may have positions on Avalanche
    # In a production test, you might want to use a known address with positions
    test_address = "0x1234567890123456789012345678901234567890"  # Example address

    positions = market_data_avalanche.get_user_positions(address=test_address)

    # Check that we got data back in expected format
    assert positions is not None
    assert isinstance(positions, dict)
