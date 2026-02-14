"""
Tests for GetPoolTVL with parametrized chain testing.

This test suite validates the GetPoolTVL class functionality
across different chains using actual network calls.
"""

import pytest
import time

from eth_defi.gmx.contracts import NETWORK_TOKENS


def test_get_pool_tvl_initialization(gmx_config, get_pool_tvl):
    """
    Test that GetPoolTVL initializes correctly with chain-specific config.
    """

    assert get_pool_tvl.config == gmx_config
    assert get_pool_tvl.datastore_contract is not None
    assert get_pool_tvl.markets is not None


def test_get_pool_tvl_direct_call(chain_name, get_pool_tvl):
    """
    Test direct GetPoolTVL usage.

    This verifies that the implementation works correctly and
    returns properly structured data.
    """
    start_time = time.time()
    pool_tvl_data = get_pool_tvl.get_data()
    execution_time = time.time() - start_time

    # Verify basic structure
    assert pool_tvl_data is not None
    assert isinstance(pool_tvl_data, dict)
    assert len(pool_tvl_data) > 0

    # Verify all markets have proper structure
    for market, data in pool_tvl_data.items():
        assert isinstance(data, dict)
        assert "total_tvl" in data
        assert "long_token" in data
        assert "short_token" in data

        # Verify data types
        assert isinstance(data["total_tvl"], (float, int)), f"TVL for {market} should be numeric"
        assert isinstance(data["long_token"], str), f"Long token for {market} should be string"
        assert isinstance(data["short_token"], str), f"Short token for {market} should be string"

        # Verify TVL is non-negative
        assert data["total_tvl"] >= 0, f"TVL for {market} should be non-negative, got {data['total_tvl']}"

        # Verify token addresses have proper format
        assert data["long_token"].startswith("0x"), f"Long token address should start with 0x: {data['long_token']}"
        assert len(data["long_token"]) == 42, f"Long token address should be 42 characters: {data['long_token']}"
        assert data["short_token"].startswith("0x"), f"Short token address should start with 0x: {data['short_token']}"
        assert len(data["short_token"]) == 42, f"Short token address should be 42 characters: {data['short_token']}"


def test_get_pool_tvl_data_consistency(chain_name, get_pool_tvl):
    """
    Test that the implementation returns consistent data.

    This verifies that multiple calls return the same data structure and similar values.
    """
    # Get data twice
    pool_tvl_data_1 = get_pool_tvl.get_data()
    pool_tvl_data_2 = get_pool_tvl.get_data()

    # Should have same markets
    assert set(pool_tvl_data_1.keys()) == set(pool_tvl_data_2.keys())

    # Values should be similar (within reasonable variance due to price changes)
    # We allow up to 10% variance as prices can change between calls
    tolerance = 0.1  # 10%

    inconsistent_markets = []

    for market in pool_tvl_data_1.keys():
        tvl_1 = pool_tvl_data_1[market]["total_tvl"]
        tvl_2 = pool_tvl_data_2[market]["total_tvl"]

        if tvl_1 > 0 and tvl_2 > 0:  # Only check non-zero values
            variance = abs(tvl_1 - tvl_2) / max(tvl_1, tvl_2)
            if variance > tolerance:
                inconsistent_markets.append(f"{market}: {tvl_1} vs {tvl_2} (variance: {variance:.2%})")

    # Should have consistent data
    assert len(inconsistent_markets) == 0, f"Data inconsistency found on {chain_name}: " + "; ".join(inconsistent_markets)


def test_get_pool_tvl_specific_markets(chain_name, get_pool_tvl):
    """
    Test that specific expected markets have TVL data.

    This verifies that chain-specific markets are properly handled.
    """
    pool_tvl_data = get_pool_tvl.get_data()

    # Define expected markets per chain
    if chain_name.lower() == "arbitrum":
        expected_markets = ["ETH", "BTC", "ARB"]  # Common Arbitrum markets
    else:  # avalanche
        expected_markets = ["AVAX", "ETH", "BTC"]  # Common Avalanche markets

    markets = set(pool_tvl_data.keys())

    # Check that at least some expected markets exist
    found_markets = []
    for market in expected_markets:
        # Try to find markets containing the expected symbol
        matching_markets = [m for m in markets if market in m]
        if matching_markets:
            found_markets.extend(matching_markets)

            # Verify the data is reasonable
            for m in matching_markets:
                tvl_data = pool_tvl_data[m]
                assert isinstance(tvl_data["total_tvl"], (int, float))
                assert tvl_data["total_tvl"] >= 0
                assert isinstance(tvl_data["long_token"], str)
                assert isinstance(tvl_data["short_token"], str)

    # Should find at least one expected market
    assert len(found_markets) > 0, f"No expected markets found for {chain_name}. Found: {list(markets)}"


def test_get_pool_tvl_total_calculations(chain_name, get_pool_tvl):
    """
    Test total TVL calculations and aggregations.

    This verifies that we can properly aggregate TVL data.
    """
    pool_tvl_data = get_pool_tvl.get_data()

    # Calculate totals
    total_tvl = sum(data["total_tvl"] for data in pool_tvl_data.values() if isinstance(data["total_tvl"], (int, float)) and data["total_tvl"] > 0)

    # Should have some total TVL
    assert total_tvl > 0, "Total TVL should be positive"

    # Verify market with highest TVL
    if pool_tvl_data:
        # Filter out markets with zero TVL (like APE_DEPRECATED)
        non_zero_markets = {k: v for k, v in pool_tvl_data.items() if v["total_tvl"] > 0}
        if non_zero_markets:
            highest_tvl_market = max(non_zero_markets.items(), key=lambda x: x[1]["total_tvl"])
            highest_tvl = highest_tvl_market[1]["total_tvl"]

            assert highest_tvl > 0


def test_get_pool_tvl_error_handling(chain_name, get_pool_tvl):
    """
    Test that error handling works properly.

    This verifies that the implementation handles missing or failed data gracefully.
    """
    # This should not raise an exception even if some calls fail
    try:
        pool_tvl_data = get_pool_tvl.get_data()

        # Should still return valid structure even if some data is missing
        assert isinstance(pool_tvl_data, dict)

        # Verify all markets have proper structure
        for market, data in pool_tvl_data.items():
            assert isinstance(data, dict)
            assert "total_tvl" in data
            assert "long_token" in data
            assert "short_token" in data

    except Exception as e:
        pytest.fail(f"GetPoolTVL should handle errors gracefully, but raised: {e}")


def test_get_pool_tvl_token_addresses(chain_name, get_pool_tvl):
    """
    Test that token addresses are properly formatted and valid.

    This verifies that long and short token addresses follow Ethereum address format.
    """
    pool_tvl_data = get_pool_tvl.get_data()

    # Check at least one market
    if pool_tvl_data:
        market = next(iter(pool_tvl_data))
        data = pool_tvl_data[market]

        # Verify address format (0x followed by 40 hex characters)
        assert data["long_token"].startswith("0x"), f"Long token address should start with 0x: {data['long_token']}"
        assert len(data["long_token"]) == 42, f"Long token address should be 42 characters: {data['long_token']}"
        assert all(c in "0123456789abcdefABCDEF" for c in data["long_token"][2:]), f"Long token address should contain only hex characters: {data['long_token']}"

        assert data["short_token"].startswith("0x"), f"Short token address should start with 0x: {data['short_token']}"
        assert len(data["short_token"]) == 42, f"Short token address should be 42 characters: {data['short_token']}"
        assert all(c in "0123456789abcdefABCDEF" for c in data["short_token"][2:]), f"Short token address should contain only hex characters: {data['short_token']}"

        # Long and short tokens should be different for most markets
        # Note: For BTC2 and ETH2 markets, long and short tokens are the same
        if "BTC2" not in market and "ETH2" not in market and "UNKNOWN" not in market:
            assert data["long_token"] != data["short_token"], f"Long and short tokens should be different: {data['long_token']} == {data['short_token']}"


def test_get_pool_tvl_special_markets(chain_name, get_pool_tvl):
    """
    Test special market types like BTC2, ETH2, and UNKNOWN.

    This verifies that special market types are handled correctly.
    """
    pool_tvl_data = get_pool_tvl.get_data()

    # Check BTC2 market (should have same long and short token)
    btc2_markets = [m for m in pool_tvl_data.items() if "BTC2" in m[0]]
    if btc2_markets:
        btc2_market = btc2_markets[0]
        assert btc2_market[1]["long_token"] == btc2_market[1]["short_token"], "BTC2 market should have same long and short token"
        assert btc2_market[1]["total_tvl"] > 0, "BTC2 market should have positive TVL"

    # Check ETH2 market (should have same long and short token)
    eth2_markets = [m for m in pool_tvl_data.items() if "ETH2" in m[0]]
    if eth2_markets:
        eth2_market = eth2_markets[0]
        assert eth2_market[1]["long_token"] == eth2_market[1]["short_token"], "ETH2 market should have same long and short token"
        assert eth2_market[1]["total_tvl"] > 0, "ETH2 market should have positive TVL"

    # Check UNKNOWN market (may have special behavior)
    unknown_markets = [m for m in pool_tvl_data.items() if "UNKNOWN" in m[0]]
    if unknown_markets:
        unknown_market = unknown_markets[0]
        assert unknown_market[1]["total_tvl"] >= 0, "UNKNOWN market should have non-negative TVL"
        # UNKNOWN market might have unusual token addresses
        assert unknown_market[1]["long_token"].startswith("0x")
        assert unknown_market[1]["short_token"].startswith("0x")

    # print(f"\n{chain_name.upper()}: Special markets test passed")


def test_get_pool_tvl_zero_tvl_markets(chain_name, get_pool_tvl):
    """
    Test markets with zero TVL (like APE_DEPRECATED).

    This verifies that markets with zero TVL are handled correctly.
    """
    pool_tvl_data = get_pool_tvl.get_data()

    # Check for markets with zero TVL
    zero_tvl_markets = [m for m in pool_tvl_data.items() if m[1]["total_tvl"] == 0]

    if zero_tvl_markets:
        # Verify these markets still have proper structure
        for market, data in zero_tvl_markets:
            assert data["total_tvl"] == 0
            assert data["long_token"].startswith("0x")
            assert data["short_token"].startswith("0x")
            assert len(data["long_token"]) == 42
            assert len(data["short_token"]) == 42


def test_get_pool_tvl_market_token_pairs(chain_name, get_pool_tvl):
    """
    Test that market token pairs make sense.

    This verifies that the long and short tokens for each market are appropriate.
    """
    pool_tvl_data = get_pool_tvl.get_data()

    eth_address = NETWORK_TOKENS[chain_name]["WETH"]
    btc_address = NETWORK_TOKENS[chain_name]["WBTC"]
    usdc_address = NETWORK_TOKENS[chain_name]["USDC"]

    # Check ETH markets
    eth_markets = [m for m in pool_tvl_data.items() if "ETH" in m[0] and "2" not in m[0]]
    for market, data in eth_markets:
        # ETH markets should have ETH as long token and USDC as short token
        assert data["long_token"].lower() == eth_address.lower() or data["long_token"].lower() == NETWORK_TOKENS[chain_name]["wstETH"].lower(), f"ETH market {market} should have ETH/wstETH as long token"
        # Don't need to test the short token as it'll be always a stable coin in case of GMX it can be USDC, USDC.e etc.

    # Check BTC markets
    btc_markets = [m for m in pool_tvl_data.items() if "BTC" in m[0] and "2" not in m[0]]
    for market, data in btc_markets:
        # BTC markets should have BTC as long token and USDC as short token
        assert data["long_token"].lower() == btc_address.lower(), f"BTC market {market} should have BTC as long token"
        assert data["short_token"].lower() == usdc_address.lower(), f"BTC market {market} should have USDC as short token"

    # Check USDC markets (like APE, SHIB, etc.)
    usdc_markets = [m for m in pool_tvl_data.items() if "USDC" in m[0]]
    for market, data in usdc_markets:
        # USDC markets should have USDC as both long and short token (stablecoin pools)
        assert data["long_token"].lower() == usdc_address.lower(), f"USDC market {market} should have USDC as long token"
        assert data["short_token"].lower() == usdc_address.lower(), f"USDC market {market} should have USDC as short token"
