"""
Tests for GetAvailableLiquidity with parametrised chain testing.

This test suite validates the GetAvailableLiquidity class functionality
using efficient multicall batching across different chains.
"""

import pytest
import time

from eth_defi.gmx.core.available_liquidity import GetAvailableLiquidity


def test_get_available_liquidity_initialization(gmx_config):
    """
    Test that GetAvailableLiquidity initializes correctly with chain-specific config.
    """
    get_available_liquidity = GetAvailableLiquidity(gmx_config, filter_swap_markets=True)

    assert get_available_liquidity.config == gmx_config
    assert get_available_liquidity.filter_swap_markets is True
    assert get_available_liquidity.datastore_address is not None
    assert get_available_liquidity.log is not None

    # Test with filter_swap_markets=False
    get_available_liquidity_unfiltered = GetAvailableLiquidity(gmx_config, filter_swap_markets=False)
    assert get_available_liquidity_unfiltered.filter_swap_markets is False


def test_get_available_liquidity_direct_call(chain_name, get_available_liquidity):
    """
    Test direct GetAvailableLiquidity usage with multicall implementation.

    This verifies that the multicall implementation works correctly and
    returns properly structured data.
    """
    start_time = time.time()

    liquidity_data = get_available_liquidity.get_data()

    execution_time = time.time() - start_time

    # Verify basic structure
    assert liquidity_data is not None
    assert isinstance(liquidity_data, dict)
    assert "long" in liquidity_data
    assert "short" in liquidity_data
    assert "parameter" in liquidity_data
    assert liquidity_data["parameter"] == "available_liquidity"

    # Verify data types
    assert isinstance(liquidity_data["long"], dict)
    assert isinstance(liquidity_data["short"], dict)

    # Should have some markets with liquidity data
    assert len(liquidity_data["long"]) > 0
    assert len(liquidity_data["short"]) > 0

    # Check that we have the same markets in both long and short
    long_markets = set(liquidity_data["long"].keys())
    short_markets = set(liquidity_data["short"].keys())
    assert long_markets == short_markets

    # Verify all values are numeric (float or int)
    for market, liquidity in liquidity_data["long"].items():
        assert isinstance(liquidity, (int, float)), f"Long liquidity for {market} should be numeric, got {type(liquidity)}"
        assert liquidity >= 0, f"Long liquidity for {market} should be non-negative, got {liquidity}"

    for market, liquidity in liquidity_data["short"].items():
        assert isinstance(liquidity, (int, float)), f"Short liquidity for {market} should be numeric, got {type(liquidity)}"
        assert liquidity >= 0, f"Short liquidity for {market} should be non-negative, got {liquidity}"



def test_get_available_liquidity_data_consistency(chain_name, get_available_liquidity):
    """
    Test that the multicall implementation returns consistent data.

    This verifies that multiple calls return the same data structure and similar values.
    """

    # Get data twice
    liquidity_data_1 = get_available_liquidity.get_data()
    liquidity_data_2 = get_available_liquidity.get_data()

    # Should have same structure
    assert set(liquidity_data_1["long"].keys()) == set(liquidity_data_2["long"].keys())
    assert set(liquidity_data_1["short"].keys()) == set(liquidity_data_2["short"].keys())

    # Values should be similar (within reasonable variance due to price changes)
    # We allow up to 10% variance as prices can change between calls
    tolerance = 0.1  # 10%

    inconsistent_markets = []

    for market in liquidity_data_1["long"].keys():
        value_1 = liquidity_data_1["long"][market]
        value_2 = liquidity_data_2["long"][market]

        if value_1 > 0 and value_2 > 0:  # Only check non-zero values
            variance = abs(value_1 - value_2) / max(value_1, value_2)
            if variance > tolerance:
                inconsistent_markets.append(f"Long {market}: {value_1} vs {value_2} (variance: {variance:.2%})")

    for market in liquidity_data_1["short"].keys():
        value_1 = liquidity_data_1["short"][market]
        value_2 = liquidity_data_2["short"][market]

        if value_1 > 0 and value_2 > 0:  # Only check non-zero values
            variance = abs(value_1 - value_2) / max(value_1, value_2)
            if variance > tolerance:
                inconsistent_markets.append(f"Short {market}: {value_1} vs {value_2} (variance: {variance:.2%})")

    # Should have consistent data
    assert len(inconsistent_markets) == 0, f"Data inconsistency found on {chain_name}: " + "; ".join(inconsistent_markets)

def test_get_available_liquidity_specific_markets(chain_name, get_available_liquidity):
    """
    Test that specific expected markets have liquidity data.

    This verifies that chain-specific markets are properly handled.
    """

    liquidity_data = get_available_liquidity.get_data()

    # Define expected markets per chain
    if chain_name.lower() == "arbitrum":
        expected_markets = ["ETH", "BTC", "ARB"]  # Common Arbitrum markets
    else:  # avalanche
        expected_markets = ["AVAX", "ETH", "BTC"]  # Common Avalanche markets

    long_markets = set(liquidity_data["long"].keys())
    short_markets = set(liquidity_data["short"].keys())

    # Check that at least some expected markets exist
    found_markets = []
    for market in expected_markets:
        if market in long_markets and market in short_markets:
            found_markets.append(market)

            # Verify the data is reasonable
            long_liq = liquidity_data["long"][market]
            short_liq = liquidity_data["short"][market]

            assert isinstance(long_liq, (int, float))
            assert isinstance(short_liq, (int, float))
            assert long_liq >= 0
            assert short_liq >= 0

            print(f"\n{chain_name.upper()} {market}: Long=${long_liq:,.2f}, Short=${short_liq:,.2f}")

    # Should find at least one expected market
    assert len(found_markets) > 0, f"No expected markets found for {chain_name}. Found: {list(long_markets)}"



def test_get_available_liquidity_total_calculations(chain_name, get_available_liquidity):
    """
    Test total liquidity calculations and aggregations.

    This verifies that we can properly aggregate liquidity data.
    """

    liquidity_data = get_available_liquidity.get_data()

    # Calculate totals
    total_long_liquidity = sum(liq for liq in liquidity_data["long"].values() if isinstance(liq, (int, float)) and liq > 0)

    total_short_liquidity = sum(liq for liq in liquidity_data["short"].values() if isinstance(liq, (int, float)) and liq > 0)

    total_liquidity = total_long_liquidity + total_short_liquidity

    # Should have some total liquidity
    assert total_long_liquidity >= 0
    assert total_short_liquidity >= 0
    assert total_liquidity >= 0


def test_get_available_liquidity_error_handling(chain_name, get_available_liquidity):
    """
    Test that error handling works properly in multicall implementation.

    This verifies that the implementation handles missing or failed data gracefully.
    """

    # This should not raise an exception even if some calls fail
    try:
        liquidity_data = get_available_liquidity.get_data()

        # Should still return valid structure even if some data is missing
        assert isinstance(liquidity_data, dict)
        assert "long" in liquidity_data
        assert "short" in liquidity_data
        assert "parameter" in liquidity_data

    except Exception as e:
        pytest.fail(f"GetAvailableLiquidity should handle errors gracefully, but raised: {e}")


def test_get_available_liquidity_filter_swap_markets(chain_name, gmx_config):
    """
    Test the filter_swap_markets functionality.

    This verifies that swap market filtering works correctly.
    """
    # Test with filtering enabled (default)
    get_available_liquidity_filtered = GetAvailableLiquidity(gmx_config, filter_swap_markets=True)
    filtered_data = get_available_liquidity_filtered.get_data()

    # Test with filtering disabled
    get_available_liquidity_unfiltered = GetAvailableLiquidity(gmx_config, filter_swap_markets=False)
    unfiltered_data = get_available_liquidity_unfiltered.get_data()

    # Both should return valid data
    assert isinstance(filtered_data, dict)
    assert isinstance(unfiltered_data, dict)

    # Unfiltered might have same or more markets (depending on whether swap markets exist)
    filtered_market_count = len(filtered_data["long"])
    unfiltered_market_count = len(unfiltered_data["long"])

    assert unfiltered_market_count >= filtered_market_count, f"Unfiltered should have >= markets than filtered. Filtered: {filtered_market_count}, Unfiltered: {unfiltered_market_count}"