"""
Tests for GetPoolTVL with parametrized chain testing.

This test suite validates the GetPoolTVL class functionality
across different chains using actual network calls.
"""

import pytest
import time
from flaky import flaky

from eth_defi.gmx.contracts import NETWORK_TOKENS


@flaky(max_runs=3, min_passes=1)
def test_get_pool_tvl_direct_call(chain_name, get_pool_tvl):
    """
    Test direct GetPoolTVL usage.

    This verifies that the implementation works correctly and
    returns properly structured data.

    Flaky: ``get_pool_tvl.get_data()`` calls the GMX DataStore contract via RPC.
    Under transient RPC/API outages — or when multiple parallel pytest workers
    simultaneously saturate the shared GMX API endpoint — the call can return an
    empty dict.  ``@flaky`` retries up to 3 times; ``pytest.skip()`` is used for
    the known-bad empty-response so the retry budget is not wasted on hard failures.
    """
    start_time = time.time()
    pool_tvl_data = get_pool_tvl.get_data()
    execution_time = time.time() - start_time

    # Empty response is a known-bad transient condition (RPC outage or parallel-test
    # API saturation).  Skip rather than hard-fail so @flaky can attempt the next retry.
    if not pool_tvl_data:
        pytest.skip(f"get_pool_tvl.get_data() returned an empty dict for chain '{chain_name}' — transient RPC/GMX API outage or API saturation from parallel test execution.  @flaky will retry up to 3 times.")

    # Verify basic structure
    assert pool_tvl_data is not None
    assert isinstance(pool_tvl_data, dict)

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


def test_get_pool_tvl_token_addresses(chain_name, get_pool_tvl):
    """
    Test that token addresses are properly formatted and valid.

    This verifies that long and short token addresses follow Ethereum address format.
    """
    pool_tvl_data = get_pool_tvl.get_data()

    for market, data in pool_tvl_data.items():
        # Verify address format (0x followed by 40 hex characters)
        assert data["long_token"].startswith("0x"), f"Long token address should start with 0x: {data['long_token']}"
        assert len(data["long_token"]) == 42, f"Long token address should be 42 characters: {data['long_token']}"
        assert all(c in "0123456789abcdefABCDEF" for c in data["long_token"][2:]), f"Long token address should contain only hex characters: {data['long_token']}"

        assert data["short_token"].startswith("0x"), f"Short token address should start with 0x: {data['short_token']}"
        assert len(data["short_token"]) == 42, f"Short token address should be 42 characters: {data['short_token']}"
        assert all(c in "0123456789abcdefABCDEF" for c in data["short_token"][2:]), f"Short token address should contain only hex characters: {data['short_token']}"

        # Any *2 market is a single-token synthetic pool by construction.
        if not market.endswith("2") and "UNKNOWN" not in market:
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
