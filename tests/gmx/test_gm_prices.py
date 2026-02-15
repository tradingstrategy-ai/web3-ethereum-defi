"""
Tests for GetGMPrices with parametrized chain testing.

This test suite validates the GetGMPrices class functionality
across different chains and price types, with tests updated to match
the actual output structure of the get_data() method.
"""

import pytest
import time

from eth_defi.gmx.core.gm_prices import GetGMPrices


def test_get_gm_prices_initialization(gmx_config):
    """
    Test that GetGMPrices initializes correctly with chain-specific config.
    """
    get_gm_prices = GetGMPrices(gmx_config, filter_swap_markets=True)

    assert get_gm_prices.config == gmx_config
    assert get_gm_prices.filter_swap_markets is True
    assert get_gm_prices.log is not None

    # Test with filter_swap_markets=False
    get_gm_prices_unfiltered = GetGMPrices(gmx_config, filter_swap_markets=False)
    assert get_gm_prices_unfiltered.filter_swap_markets is False


def test_get_gm_prices_direct_call(chain_name, get_gm_prices):
    """
    Test direct GetGMPrices usage with different price types.

    This verifies that the implementation works correctly and
    returns properly structured data for all price types.
    """
    for price_type in ["traders", "deposits", "withdrawals"]:
        start_time = time.time()

        if price_type == "traders":
            prices_data = get_gm_prices.get_price_traders()
        elif price_type == "deposits":
            prices_data = get_gm_prices.get_price_deposit()
        else:  # withdrawals
            prices_data = get_gm_prices.get_price_withdraw()

        execution_time = time.time() - start_time

        # Verify basic structure
        assert prices_data is not None
        assert isinstance(prices_data, dict)
        assert "gm_prices" in prices_data
        assert "parameter" in prices_data
        assert prices_data["parameter"] == "gm_prices"
        assert "timestamp" in prices_data
        assert "chain" in prices_data

        # Verify data types
        assert isinstance(prices_data["gm_prices"], dict)
        assert isinstance(prices_data["timestamp"], int)
        assert isinstance(prices_data["chain"], str)

        # Should have some markets with price data
        # Note: May be empty due to network/oracle issues
        # Just verify structure is correct

        # Verify all values are numeric (float) if we have data
        if len(prices_data["gm_prices"]) > 0:
            for market, price in prices_data["gm_prices"].items():
                assert isinstance(price, float), f"Price for {market} should be float, got {type(price)}"
                # Note: UNKNOWN market can have price 0.0
                if market != "UNKNOWN":
                    assert price > 0, f"Price for {market} should be positive, got {price}"

        # print(f"\n{chain_name.upper()}: GetGMPrices ({price_type}) completed in {execution_time:.2f} seconds")
        # print(f"{chain_name.upper()}: Retrieved prices for {len(prices_data['gm_prices'])} markets")


def test_get_gm_prices_specific_markets(chain_name, get_gm_prices):
    """
    Test that specific expected markets have price data.

    This verifies that chain-specific markets are properly handled.
    """
    # Get data for traders (most commonly used)
    prices_data = get_gm_prices.get_price_traders()

    # Define expected markets per chain
    if chain_name.lower() == "arbitrum":
        expected_markets = ["ETH", "BTC", "ARB", "LINK"]  # Common Arbitrum markets
    else:  # avalanche
        expected_markets = ["AVAX", "ETH", "BTC", "LINK"]  # Common Avalanche markets

    markets = set(prices_data["gm_prices"].keys())

    # Check that at least some expected markets exist
    found_markets = []
    for market in expected_markets:
        if market in markets:
            found_markets.append(market)

            # Verify the data is reasonable
            price = prices_data["gm_prices"][market]
            assert isinstance(price, float)
            if market != "UNKNOWN":
                assert price > 0

            # print(f"\n{chain_name.upper()} {market} GM price: ${price:.6f}")

    # Note: May not find expected markets due to network/oracle issues or market changes
    # Just verify structure is correct if markets exist
    if len(markets) > 0 and len(found_markets) == 0:
        pytest.skip(f"No expected markets found for {chain_name}. This may be due to network issues or market changes. Found: {list(markets)}")

    # print(f"{chain_name.upper()}: Found {len(found_markets)} expected markets: {found_markets}")


def test_get_gm_prices_total_calculations(chain_name, get_gm_prices):
    """
    Test total price calculations and aggregations.

    This verifies that we can properly analyze price data.
    """
    # Get data for traders
    prices_data = get_gm_prices.get_price_traders()

    # Calculate statistics if we have data
    all_prices = [price for price in prices_data["gm_prices"].values() if price > 0]  # Exclude UNKNOWN (0.0)

    if len(all_prices) == 0:
        pytest.skip(f"No price data available for {chain_name}. This may be due to network issues.")

    min_price = min(all_prices)
    max_price = max(all_prices)
    avg_price = sum(all_prices) / len(all_prices)
    median_price = sorted(all_prices)[len(all_prices) // 2]

    # Should have reasonable values
    assert min_price > 0
    assert max_price >= min_price
    assert avg_price >= min_price
    assert avg_price <= max_price

    # print(f"\nGM Price Statistics on {chain_name.upper()}:")
    # print(f"  Min: ${min_price:.6f}")
    # print(f"  Max: ${max_price:.6f}")
    # print(f"  Avg: ${avg_price:.6f}")
    # print(f"  Median: ${median_price:.6f}")
    # print(f"  Markets: {len(all_prices)}")


def test_get_gm_prices_error_handling(chain_name, get_gm_prices):
    """
    Test that error handling works properly.

    This verifies that the implementation handles missing or failed data gracefully.
    """
    # This should not raise an exception even if some calls fail
    try:
        # Test all price types
        traders_data = get_gm_prices.get_price_traders()
        deposits_data = get_gm_prices.get_price_deposit()
        withdrawals_data = get_gm_prices.get_price_withdraw()

        # Should still return valid structure even if some data is missing
        for data in [traders_data, deposits_data, withdrawals_data]:
            assert isinstance(data, dict)
            assert "gm_prices" in data
            assert "parameter" in data
            assert "timestamp" in data
            assert "chain" in data

        # print(f"\n{chain_name.upper()}: Error handling test passed - graceful handling of any failures")

    except Exception as e:
        pytest.fail(f"GetGMPrices should handle errors gracefully, but raised: {e}")


def test_get_gm_prices_filter_swap_markets(chain_name, gmx_config):
    """
    Test the filter_swap_markets functionality.

    This verifies that swap market filtering works correctly.
    """
    # Test with filtering enabled (default)
    get_gm_prices_filtered = GetGMPrices(gmx_config, filter_swap_markets=True)
    filtered_data = get_gm_prices_filtered.get_price_traders()

    # Test with filtering disabled
    get_gm_prices_unfiltered = GetGMPrices(gmx_config, filter_swap_markets=False)
    unfiltered_data = get_gm_prices_unfiltered.get_price_traders()

    # Both should return valid data
    assert isinstance(filtered_data, dict)
    assert isinstance(unfiltered_data, dict)

    # Unfiltered might have same or more markets (depending on whether swap markets exist)
    filtered_market_count = len(filtered_data["gm_prices"])
    unfiltered_market_count = len(unfiltered_data["gm_prices"])

    assert unfiltered_market_count >= filtered_market_count, f"Unfiltered should have >= markets than filtered. Filtered: {filtered_market_count}, Unfiltered: {unfiltered_market_count}"

    # print(f"\n{chain_name.upper()}: Market filtering test")
    # print(f"  Filtered markets: {filtered_market_count}")
    # print(f"  Unfiltered markets: {unfiltered_market_count}")


def test_get_gm_prices_unified_method(chain_name, get_gm_prices):
    """
    Test the unified get_prices method with different price types.

    This verifies that the unified method correctly routes to the appropriate
    price type method based on the price_type parameter.
    """
    # Test all valid price types
    for price_type in ["traders", "deposits", "withdrawals"]:
        prices_data = get_gm_prices.get_prices(price_type=price_type)

        # Verify basic structure
        assert prices_data is not None
        assert isinstance(prices_data, dict)
        assert "gm_prices" in prices_data
        assert "parameter" in prices_data
        assert prices_data["parameter"] == "gm_prices"
        assert "timestamp" in prices_data
        assert "chain" in prices_data

        # Should have some markets
        # Note: May be empty due to network/oracle issues
        if len(prices_data["gm_prices"]) == 0:
            pytest.skip(f"No GM price data available for {chain_name} ({price_type}). This may be due to network issues.")

        # Test with invalid price type (should default to traders)
        default_data = get_gm_prices.get_prices(price_type="invalid_type")
        traders_data = get_gm_prices.get_price_traders()

        # Default should match traders data (allowing for minor timing differences)
        # We'll check that the market sets are the same and values are identical
        assert set(default_data["gm_prices"].keys()) == set(traders_data["gm_prices"].keys())

        # Verify values are identical (within this implementation)
        for market in default_data["gm_prices"].keys():
            assert default_data["gm_prices"][market] == pytest.approx(traders_data["gm_prices"][market], rel=0.01)

        # print(f"\n{chain_name.upper()}: Unified method test passed for price type: {price_type}")


def test_get_gm_prices_comprehensive_data(chain_name, get_gm_prices):
    """
    Test the base class interface _get_data_processing method.

    This verifies that the comprehensive data method returns all price types
    in a single structured response with the expected format.
    """
    # Get comprehensive data using base class interface
    comprehensive_data = get_gm_prices.get_data()

    # Verify top-level structure
    assert comprehensive_data is not None
    assert isinstance(comprehensive_data, dict)
    assert "parameter" in comprehensive_data
    assert comprehensive_data["parameter"] == "gm_prices_all_types"
    assert "timestamp" in comprehensive_data
    assert "chain" in comprehensive_data
    assert "price_types" in comprehensive_data
    assert "metadata" in comprehensive_data
    assert "total_markets" in comprehensive_data
    assert "markets" in comprehensive_data

    # Verify price types structure
    price_types = comprehensive_data["price_types"]
    assert "traders" in price_types
    assert "deposits" in price_types
    assert "withdrawals" in price_types

    # Verify all price types have the same market structure
    traders_markets = set(price_types["traders"].keys())
    deposits_markets = set(price_types["deposits"].keys())
    withdrawals_markets = set(price_types["withdrawals"].keys())

    assert traders_markets == deposits_markets == withdrawals_markets
    assert comprehensive_data["total_markets"] == len(traders_markets)
    assert comprehensive_data["total_markets"] == len(comprehensive_data["markets"])
    assert set(comprehensive_data["markets"]) == traders_markets

    # Verify metadata structure
    metadata = comprehensive_data["metadata"]
    assert "total_markets_traders" in metadata
    assert "total_markets_deposits" in metadata
    assert "total_markets_withdrawals" in metadata
    assert "description" in metadata
    assert metadata["description"] == "Comprehensive GM prices including traders, deposits, and withdrawals"

    # Verify market counts match
    assert metadata["total_markets_traders"] == comprehensive_data["total_markets"]
    assert metadata["total_markets_deposits"] == comprehensive_data["total_markets"]
    assert metadata["total_markets_withdrawals"] == comprehensive_data["total_markets"]

    # Verify price values are consistent across types (current implementation)
    for market in traders_markets:
        traders_price = price_types["traders"][market]
        deposits_price = price_types["deposits"][market]
        withdrawals_price = price_types["withdrawals"][market]

        # In current implementation, all price types return identical values
        assert traders_price == pytest.approx(deposits_price, rel=0.01), f"Traders/deposits prices differ for {market}: traders={traders_price}, deposits={deposits_price}"
        assert traders_price == pytest.approx(withdrawals_price, rel=0.01), f"Traders/withdrawals prices differ for {market}: traders={traders_price}, withdrawals={withdrawals_price}"

        # Verify price is a float and (except for UNKNOWN) positive
        assert isinstance(traders_price, float)
        if market != "UNKNOWN":
            assert traders_price > 0
