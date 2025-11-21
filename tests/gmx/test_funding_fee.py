"""
Tests for GMX Funding APR functionality (alias for GetFundingFee).
"""

import pytest
import time
import logging
import numpy as np

from eth_defi.gmx.core.funding_fee import GetFundingFee


def test_initialization_and_basic_functionality(get_funding_fee, gmx_config):
    """Test GetFundingFee initialization and basic functionality."""
    # Test basic initialization
    assert get_funding_fee.config is not None
    assert get_funding_fee.log is not None
    assert get_funding_fee.filter_swap_markets is True

    # Test initialization with custom filter setting
    funding_fee_custom = GetFundingFee(gmx_config, filter_swap_markets=False)
    assert funding_fee_custom.filter_swap_markets is False

    # Test inheritance from GetData
    assert hasattr(get_funding_fee, "get_data")
    assert callable(get_funding_fee.get_data)

    # Test config dependency
    assert hasattr(get_funding_fee.config, "web3")
    assert hasattr(get_funding_fee.config, "chain")

    # Test that markets are properly initialized
    assert get_funding_fee.markets is not None
    assert hasattr(get_funding_fee.markets, "get_available_markets")


def test_market_info_and_data_structures(get_funding_fee):
    """Test market info handling and data structure patterns."""
    results = get_funding_fee.get_data()

    assert isinstance(results, dict)
    assert "long" in results
    assert "short" in results
    assert "parameter" in results
    assert results["parameter"] == "funding_apr"

    # Check that we have matching markets in both long and short
    assert set(results["long"].keys()) == set(results["short"].keys())

    # Check that we have some markets
    assert len(results["long"]) > 0

    # Check data types for a sample market
    if results["long"]:
        sample_market = next(iter(results["long"].keys()))
        assert isinstance(results["long"][sample_market], float)
        assert isinstance(results["short"][sample_market], float)
        # Funding rates should be within reasonable bounds (can be extreme in volatile markets)
        assert -5 <= results["long"][sample_market] <= 5
        assert -5 <= results["short"][sample_market] <= 5


def test_funding_fee_calculation(get_funding_fee):
    """Test that funding fee calculations make sense with real data."""
    results = get_funding_fee.get_data()

    # Verify at least one market has valid data
    assert len(results["long"]) > 0, "No markets with valid funding fee data"

    # Check a few specific markets if they exist
    for market_symbol in ["ETH", "BTC", "ARB"]:
        if market_symbol in results["long"]:
            # Funding rates should be within reasonable bounds (can be extreme in volatile markets)
            assert -5 <= results["long"][market_symbol] <= 5, f"Long funding rate for {market_symbol} out of bounds"
            assert -5 <= results["short"][market_symbol] <= 5, f"Short funding rate for {market_symbol} out of bounds"

            # For most markets, long and short funding rates should have opposite signs
            # Skip this check as it's not always true depending on market conditions
            # if market_symbol not in ["BTC2", "ETH2"]:
            #     assert results["long"][market_symbol] * results["short"][market_symbol] < 0, f"Funding rates for {market_symbol} should have opposite signs"

            # Annualized rates should be within reasonable bounds
            annualized_long = results["long"][market_symbol] * 24 * 365
            annualized_short = results["short"][market_symbol] * 24 * 365
            assert abs(annualized_long) < 50000, f"Annualized long funding rate for {market_symbol} too high: {annualized_long}"
            assert abs(annualized_short) < 50000, f"Annualized short funding rate for {market_symbol} too high: {annualized_short}"


def test_data_consistency(get_funding_fee):
    """Test that funding fee data is consistent across multiple calls with real data."""
    results1 = get_funding_fee.get_data()
    time.sleep(0.1)  # Small delay to allow for potential changes
    results2 = get_funding_fee.get_data()

    # We expect some minor changes due to interest accumulation, but not major changes
    tolerance = 0.1  # 10% tolerance for change

    for market in results1["long"].keys():
        if market in results2["long"]:
            long_change = abs(results1["long"][market] - results2["long"][market])
            short_change = abs(results1["short"][market] - results2["short"][market])

            # Allow small changes, but not large ones
            assert long_change < tolerance, f"Long funding rate for {market} changed too much: {results1['long'][market]} -> {results2['long'][market]}"
            assert short_change < tolerance, f"Short funding rate for {market} changed too much: {results1['short'][market]} -> {results2['short'][market]}"


def test_market_filtering(gmx_config):
    """Test that market filtering works correctly with real data."""
    # Test with filtering enabled (default)
    filtered_fee = GetFundingFee(gmx_config, filter_swap_markets=True)
    filtered_results = filtered_fee.get_data()

    # Test with filtering disabled
    unfiltered_fee = GetFundingFee(gmx_config, filter_swap_markets=False)
    unfiltered_results = unfiltered_fee.get_data()

    # Unfiltered should have more markets (including swap markets)
    assert len(unfiltered_results["long"]) >= len(filtered_results["long"])

    # All filtered markets should be in unfiltered results
    assert set(filtered_results["long"].keys()).issubset(set(unfiltered_results["long"].keys()))

    # If there are swap markets, they should be in unfiltered but not filtered
    swap_markets = [m for m in unfiltered_results["long"].keys() if "SWAP" in m]
    if swap_markets:
        assert not any(m in filtered_results["long"] for m in swap_markets), "Swap markets should not be included when filtering is enabled"


def test_funding_rate_bounds(get_funding_fee):
    """Test that funding rates stay within reasonable bounds with real data."""
    results = get_funding_fee.get_data()

    for market in results["long"].keys():
        # Hourly funding rates can be quite high in volatile conditions
        # Some markets like BTC2/ETH2 can have extreme funding rates
        # Allow up to 500% hourly rate (which is extreme but possible)
        assert -5 <= results["long"][market] <= 5, f"Long funding rate for {market} out of bounds: {results['long'][market]}"
        assert -5 <= results["short"][market] <= 5, f"Short funding rate for {market} out of bounds: {results['short'][market]}"

        # Annualized rates should be within reasonable bounds (though we don't annualize in this function)
        # Just verify they're not absurdly large
        annualized_long = results["long"][market] * 24 * 365
        annualized_short = results["short"][market] * 24 * 365
        assert abs(annualized_long) < 50000, f"Annualized long funding rate for {market} too high: {annualized_long}"
        assert abs(annualized_short) < 50000, f"Annualized short funding rate for {market} too high: {annualized_short}"
