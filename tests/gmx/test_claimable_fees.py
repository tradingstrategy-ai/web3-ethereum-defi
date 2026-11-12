"""
Tests for GetClaimableFees with parametrised chain testing.
"""

import pytest
import time

from eth_defi.gmx.core.claimable_fees import GetClaimableFees


def test_get_claimable_fees_initialization(gmx_config):
    """
    Test that GetClaimableFees initializes correctly with chain-specific config.
    """
    get_claimable_fees = GetClaimableFees(gmx_config)

    assert get_claimable_fees.config == gmx_config
    assert get_claimable_fees.log is not None
    assert get_claimable_fees.oracle_prices is not None


def test_get_claimable_fees_direct_call(chain_name, get_claimable_fees):
    """
    Test direct GetClaimableFees usage.

    This verifies that the implementation works correctly and
    returns properly structured data.
    """
    start_time = time.time()
    fees_data = get_claimable_fees.get_data()
    execution_time = time.time() - start_time

    # Verify basic structure
    assert fees_data is not None
    assert isinstance(fees_data, dict)
    assert "total_fees" in fees_data
    assert "parameter" in fees_data
    assert fees_data["parameter"] == "total_fees"

    # Verify data types
    assert isinstance(fees_data["total_fees"], (int, float)), f"Total fees should be numeric, got {type(fees_data['total_fees'])}"

    # Total fees should be non-negative
    assert fees_data["total_fees"] >= 0, f"Total fees should be non-negative, got {fees_data['total_fees']}"


def test_get_claimable_fees_special_markets(chain_name, get_claimable_fees):
    """
    Test special market handling like BTC2, ETH2, etc.

    This verifies that special market types are handled correctly.
    """
    # Get per-market claimable fees
    market_fees = get_claimable_fees.get_per_market_claimable_fees()

    # Check BTC2/ETH2 markets (short fees should be 0)
    btc2_markets = [m for m in market_fees.items() if "BTC2" in m[0]]
    eth2_markets = [m for m in market_fees.items() if "ETH2" in m[0]]

    for market, fees in btc2_markets + eth2_markets:
        assert fees["short"] == 0, f"Short fees for {market} should be 0, got {fees['short']}"


def test_get_claimable_fees_calculation(chain_name, get_claimable_fees):
    """
    Test that claimable fee calculations are reasonable.

    This verifies that the calculated fees make sense relative to each other.
    """
    # Get per-market claimable fees
    market_fees = get_claimable_fees.get_per_market_claimable_fees()

    # Skip test if no markets available
    if not market_fees:
        pytest.skip("No markets available for testing")

    # Verify ETH/BTC markets have valid fee values
    eth_markets = [m for m in market_fees.items() if "ETH" in m[0] and "2" not in m[0]]
    btc_markets = [m for m in market_fees.items() if "BTC" in m[0] and "2" not in m[0]]

    # Check that major markets have fee data
    if eth_markets:
        for market, fees in eth_markets:
            assert fees["total"] is not None, f"ETH market {market} should have total fees"

    if btc_markets:
        for market, fees in btc_markets:
            assert fees["total"] is not None, f"BTC market {market} should have total fees"


def test_get_claimable_fees_zero_values(chain_name, get_claimable_fees):
    """
    Test that zero values are handled correctly.

    This verifies that markets with zero fees are handled properly.
    """
    # Get per-market claimable fees
    market_fees = get_claimable_fees.get_per_market_claimable_fees()

    # Check for markets with zero fees
    zero_fee_markets = [m for m in market_fees.items() if m[1]["total"] == 0]

    # Verify that zero fee markets have zero values for both long and short
    for market, fees in zero_fee_markets:
        assert fees["long"] == 0 and fees["short"] == 0, f"Zero fee market {market} should have zero long and short fees"
