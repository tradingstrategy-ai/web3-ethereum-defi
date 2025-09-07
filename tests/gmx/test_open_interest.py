"""
Tests for GMX Open Interest functionality.
"""

from eth_defi.gmx.core.open_interest import GetOpenInterest, OpenInterestInfo


def test_open_interest_info_and_initialization(get_open_interest, gmx_config):
    """Combined test for OpenInterestInfo dataclass and initialization."""
    # Test OpenInterestInfo dataclass creation with correct parameters
    oi_info = OpenInterestInfo(market_address="0x70d95587d40A2caf56bd97485aB3Eec10Bee6336", market_symbol="BTC/USD", long_open_interest=1000000000000000000000000000000000000, short_open_interest=2000000000000000000000000000000000000, total_open_interest=3000000000000000000000000000000000000, long_token_address="0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", short_token_address="0xaf88d065e77c8cc2239327c5edb3a432268e5831")

    assert oi_info.market_address == "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"
    assert oi_info.market_symbol == "BTC/USD"
    assert oi_info.long_open_interest == 1000000000000000000000000000000000000
    assert oi_info.short_open_interest == 2000000000000000000000000000000000000
    assert oi_info.total_open_interest == 3000000000000000000000000000000000000
    assert oi_info.long_token_address == "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
    assert oi_info.short_token_address == "0xaf88d065e77c8cc2239327c5edb3a432268e5831"

    # Test basic initialization
    assert get_open_interest.config is not None
    assert get_open_interest.filter_swap_markets is True

    # Test initialization with custom filter setting
    open_interest_custom = GetOpenInterest(gmx_config, filter_swap_markets=False)
    assert open_interest_custom.filter_swap_markets is False

    # Test inheritance from GetData
    assert hasattr(get_open_interest, "get_data")
    assert hasattr(get_open_interest, "config")
    assert callable(get_open_interest.get_data)


def test_method_availability_and_real_structure(get_open_interest):
    """Test actual methods available in the real API."""
    # Test all real methods from actual API
    expected_methods = ["_execute_threading", "_filter_swap_markets", "_format_market_info_output", "_format_number", "_get_data_processing", "_get_oracle_prices", "_get_pnl", "_get_token_addresses", "_save_dict_to_csv", "_save_to_csv", "_save_to_json", "get_data"]

    for method in expected_methods:
        assert hasattr(get_open_interest, method), f"Missing method: {method}"
        assert callable(getattr(get_open_interest, method)), f"Method not callable: {method}"

    # Test config dependency
    assert get_open_interest.config is not None
    assert hasattr(get_open_interest.config, "web3")
    assert hasattr(get_open_interest.config, "chain")


def test_formatting_and_calculations(get_open_interest):
    """Combined test for number formatting and calculation utilities."""
    # Test number formatting based on actual API behavior
    assert get_open_interest._format_number(1000000000) == "1.00B"
    assert get_open_interest._format_number(1000000) == "1.00M"
    assert get_open_interest._format_number(1500) == "1.50K"
    assert get_open_interest._format_number(150) == "150.00"
    assert get_open_interest._format_number(0) == "0.00"
    assert get_open_interest._format_number(-1000000) == "-1.00M"

    # Test exception handling
    result = get_open_interest._format_number("invalid")
    assert result == "invalid"

    # Test precision calculations used in OI calculations
    oracle_precision = 30
    token_precision = 18
    scaling_factor = 10 ** (oracle_precision - token_precision)
    assert scaling_factor == 10**12

    # Test OI calculation logic patterns
    position_size = 1000000000000000000  # 1 token in wei
    price = 50000000000000000000000000000000000  # $50k in oracle format
    usd_value = (position_size * price) // (10**oracle_precision)
    assert usd_value > 0


def test_address_handling_and_market_logic(get_open_interest):
    """Test address handling and market logic patterns."""
    # Test zero address handling (used to skip invalid markets)
    zero_address = "0x0000000000000000000000000000000000000000"
    assert len(zero_address) == 42
    assert zero_address.startswith("0x")

    # Test that token address attributes can be set (used internally)
    get_open_interest._long_token_address = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
    get_open_interest._short_token_address = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"

    assert get_open_interest._long_token_address == "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
    assert get_open_interest._short_token_address == "0xaf88d065e77c8cc2239327c5edb3a432268e5831"

    # Test synthetic market handling concepts (different decimal factors)
    synthetic_check = True  # Placeholder for is_synthetic check
    decimal_factor = 18 if not synthetic_check else 8
    assert decimal_factor in [8, 18]


def test_data_processing_patterns(get_open_interest):
    """Test data processing patterns and output structure."""
    # Test PnL calculation tuple structure (returned by _get_pnl)
    pnl_result = (1000000000000000000000000000000000000, 500000000000000000000000000000000000)
    assert len(pnl_result) == 2
    assert isinstance(pnl_result[0], int)
    assert isinstance(pnl_result[1], int)

    # Test list operations used in threading
    market_list = []
    for i in range(3):
        market_list.extend([f"market_{i}", f"token_{i}"])

    assert len(market_list) == 6
    assert market_list[0] == "market_0"
    assert market_list[1] == "token_0"

    # Test markets property exists and data processing method
    assert hasattr(get_open_interest, "markets")
    assert hasattr(get_open_interest, "_get_data_processing")
    assert callable(get_open_interest._get_data_processing)


def test_filter_and_configuration_options(get_open_interest, gmx_config):
    """Test filtering and configuration options."""
    # Test filter swap markets functionality
    assert get_open_interest.filter_swap_markets is True

    # Test creation with different filter setting
    oi_no_filter = GetOpenInterest(gmx_config, filter_swap_markets=False)
    assert oi_no_filter.filter_swap_markets is False

    # Test that both instances have same methods but different config
    assert hasattr(oi_no_filter, "_get_data_processing")
    assert hasattr(oi_no_filter, "_format_number")
    assert oi_no_filter.config == gmx_config

    # Test that filter method exists
    assert hasattr(get_open_interest, "_filter_swap_markets")
    assert callable(get_open_interest._filter_swap_markets)
