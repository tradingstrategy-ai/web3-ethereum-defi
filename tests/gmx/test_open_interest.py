"""
Tests for GMX Open Interest Data Retrieval Module.
"""

from eth_defi.gmx.core.open_interest import GetOpenInterest, OpenInterestInfo


def test_initialization_and_basic_functionality(get_open_interest, gmx_config):
    """Test GetOpenInterest initialization and basic functionality."""
    # Test basic initialization
    assert get_open_interest.config is not None
    assert get_open_interest.log is not None
    assert get_open_interest.filter_swap_markets is True

    # Test initialization with custom filter setting
    open_interest_custom = GetOpenInterest(gmx_config, filter_swap_markets=False)
    assert open_interest_custom.filter_swap_markets is False

    # Test inheritance from GetData
    assert hasattr(get_open_interest, "get_data")
    assert callable(get_open_interest.get_data)

    # Test config dependency
    assert hasattr(get_open_interest.config, "web3")
    assert hasattr(get_open_interest.config, "chain")

    # Test that markets are properly initialized
    assert get_open_interest.markets is not None
    assert hasattr(get_open_interest.markets, "get_available_markets")


def test_market_info_and_data_structures(get_open_interest):
    """Test market info handling and data structure patterns."""
    results = get_open_interest.get_data()

    assert isinstance(results, dict)
    assert "long" in results
    assert "short" in results
    assert "parameter" in results
    assert results["parameter"] == "open_interest"

    # Check that we have matching markets in both long and short
    assert set(results["long"].keys()) == set(results["short"].keys())

    # Check that we have some markets
    assert len(results["long"]) > 0

    # Check data types for a sample market
    if results["long"]:
        sample_market = next(iter(results["long"].keys()))
        assert isinstance(results["long"][sample_market], float)
        assert isinstance(results["short"][sample_market], float)
        # Open interest values should be positive
        assert results["long"][sample_market] >= 0
        assert results["short"][sample_market] >= 0


def test_open_interest_calculation(get_open_interest):
    """Test that open interest calculations make sense with real data."""
    results = get_open_interest.get_data()

    # Verify at least one market has valid data
    assert len(results["long"]) > 0, "No markets with valid open interest data"

    # Check a few specific markets if they exist
    for market_symbol in ["ETH", "BTC", "ARB"]:
        if market_symbol in results["long"]:
            # Open interest values should be positive
            assert results["long"][market_symbol] >= 0, f"Long open interest for {market_symbol} should be non-negative"
            assert results["short"][market_symbol] >= 0, f"Short open interest for {market_symbol} should be non-negative"

            # Total open interest should be the sum of long and short
            total = results["long"][market_symbol] + results["short"][market_symbol]
            assert total > 0, f"Total open interest for {market_symbol} should be positive"

            # Major markets should have meaningful open interest
            if market_symbol in ["ETH", "BTC"]:
                assert results["long"][market_symbol] > 0, f"ETH/BTC long interest should be positive"

            # Verify long interest is not zero for active markets
            assert results["long"][market_symbol] > 0, f"Long interest for {market_symbol} should not be zero"

            # Verify short interest is not zero for active markets
            assert results["short"][market_symbol] > 0, f"Short interest for {market_symbol} should not be zero"


def test_data_consistency(get_open_interest):
    """Test that open interest data is consistent across multiple calls with real data."""
    results1 = get_open_interest.get_data()
    results2 = get_open_interest.get_data()

    # We expect some minor changes due to trading activity, but not major changes
    tolerance = 0.1  # 10% tolerance for change

    for market in results1["long"].keys():
        if market in results2["long"]:
            long_change = abs(results1["long"][market] - results2["long"][market]) / max(results1["long"][market], 1)
            short_change = abs(results1["short"][market] - results2["short"][market]) / max(results1["short"][market], 1)

            # Allow small changes, but not large ones
            assert long_change < tolerance, f"Long open interest for {market} changed too much: {results1['long'][market]} -> {results2['long'][market]}"
            assert short_change < tolerance, f"Short open interest for {market} changed too much: {results1['short'][market]} -> {results2['short'][market]}"


def test_market_filtering(gmx_config):
    """Test that market filtering works correctly with real data."""
    # Test with filtering enabled (default)
    filtered_interest = GetOpenInterest(gmx_config, filter_swap_markets=True)
    filtered_results = filtered_interest.get_data()

    # Test with filtering disabled
    unfiltered_interest = GetOpenInterest(gmx_config, filter_swap_markets=False)
    unfiltered_results = unfiltered_interest.get_data()

    # Unfiltered should have more markets (including swap markets)
    assert len(unfiltered_results["long"]) >= len(filtered_results["long"])

    # All filtered markets should be in unfiltered results
    assert set(filtered_results["long"].keys()).issubset(set(unfiltered_results["long"].keys()))

    # If there are swap markets, they should be in unfiltered but not filtered
    swap_markets = [m for m in unfiltered_results["long"].keys() if "SWAP" in m]
    if swap_markets:
        assert not any(m in filtered_results["long"] for m in swap_markets), "Swap markets should not be included when filtering is enabled"


def test_open_interest_info_dataclass():
    """Test OpenInterestInfo dataclass structure and initialization."""
    market_address = "0x47904963fc8b2340414262125aF906B738AD9BDF"
    long_token_address = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    short_token_address = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

    open_interest_info = OpenInterestInfo(market_address=market_address, market_symbol="ETH", long_open_interest=1000000.0, short_open_interest=800000.0, total_open_interest=1800000.0, long_token_address=long_token_address, short_token_address=short_token_address)

    # Verify all fields are set correctly
    assert open_interest_info.market_address == market_address
    assert open_interest_info.market_symbol == "ETH"
    assert open_interest_info.long_open_interest == 1000000.0
    assert open_interest_info.short_open_interest == 800000.0
    assert open_interest_info.total_open_interest == 1800000.0
    assert open_interest_info.long_token_address == long_token_address
    assert open_interest_info.short_token_address == short_token_address


def test_open_interest_info_symbol_handling():
    """Test OpenInterestInfo with various symbol lengths and formats."""
    market_address = "0x47904963fc8b2340414262125aF906B738AD9BDF"
    long_token_address = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    short_token_address = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

    # Test with various symbol lengths
    for symbol, expected_length in [
        ("ETH", 3),
        ("BTC", 3),
        ("WSTETH", 6),
        ("AVAX", 4),
        ("SOL", 3),
        ("A", 1),
        ("VERYLONGTOKEN", 13),
    ]:
        open_interest_info = OpenInterestInfo(market_address=market_address, market_symbol=symbol, long_open_interest=1000000.0, short_open_interest=800000.0, total_open_interest=1800000.0, long_token_address=long_token_address, short_token_address=short_token_address)

        assert open_interest_info.market_symbol == symbol
        assert len(open_interest_info.market_symbol) == expected_length
        assert open_interest_info.long_open_interest > 0
        assert open_interest_info.short_open_interest > 0
        assert open_interest_info.total_open_interest == open_interest_info.long_open_interest + open_interest_info.short_open_interest


def test_open_interest_info_equality():
    """Test OpenInterestInfo equality comparison."""
    market_address = "0x47904963fc8b2340414262125aF906B738AD9BDF"
    long_token_address = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    short_token_address = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

    open_interest_info_1 = OpenInterestInfo(market_address=market_address, market_symbol="ETH", long_open_interest=1000000.0, short_open_interest=800000.0, total_open_interest=1800000.0, long_token_address=long_token_address, short_token_address=short_token_address)

    open_interest_info_2 = OpenInterestInfo(market_address=market_address, market_symbol="ETH", long_open_interest=1000000.0, short_open_interest=800000.0, total_open_interest=1800000.0, long_token_address=long_token_address, short_token_address=short_token_address)

    # Same data should be equal
    assert open_interest_info_1 == open_interest_info_2

    # Different symbol should not be equal
    open_interest_info_3 = OpenInterestInfo(
        market_address=market_address,
        market_symbol="BTC",  # Different symbol
        long_open_interest=1000000.0,
        short_open_interest=800000.0,
        total_open_interest=1800000.0,
        long_token_address=long_token_address,
        short_token_address=short_token_address,
    )

    assert open_interest_info_1 != open_interest_info_3

    # Different open interest values should not be equal
    open_interest_info_4 = OpenInterestInfo(
        market_address=market_address,
        market_symbol="ETH",
        long_open_interest=1500000.0,  # Different value
        short_open_interest=800000.0,
        total_open_interest=2300000.0,
        long_token_address=long_token_address,
        short_token_address=short_token_address,
    )

    assert open_interest_info_1 != open_interest_info_4
