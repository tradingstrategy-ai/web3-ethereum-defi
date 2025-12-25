"""
Tests for GMX Funding APR functionality (alias for GetFundingFee).
"""

from flaky import flaky

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


def test_funding_fee_calculation(get_funding_fee):
    """Test that funding fee calculations return valid data."""
    results = get_funding_fee.get_data()

    # Verify at least one market has valid data
    assert len(results["long"]) > 0, "No markets with valid funding fee data"

    # Check data types for specific markets if they exist
    for market_symbol in ["ETH", "BTC", "ARB"]:
        if market_symbol in results["long"]:
            assert isinstance(results["long"][market_symbol], float)
            assert isinstance(results["short"][market_symbol], float)


@flaky(max_runs=3, min_passes=1)
def test_data_consistency(get_funding_fee):
    """Test that funding fee data is consistent across multiple calls with real data."""
    results1 = get_funding_fee.get_data()
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


def test_funding_rate_types(get_funding_fee):
    """Test that funding rates are valid float values."""
    results = get_funding_fee.get_data()

    for market in results["long"].keys():
        assert isinstance(results["long"][market], float), f"Long funding rate for {market} should be float"
        assert isinstance(results["short"][market], float), f"Short funding rate for {market} should be float"
