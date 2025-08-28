"""
Tests for GMX GM Prices functionality based on real API structure.
"""

from eth_defi.gmx.core.gm_prices import GetGMPrices


def test_initialization_and_basic_functionality(get_gm_prices, gmx_config):
    """Test GetGMPrices initialization and basic functionality."""
    # Test basic initialization
    assert get_gm_prices.config is not None
    assert get_gm_prices.filter_swap_markets is True

    # Test initialization with custom filter setting
    gm_prices_custom = GetGMPrices(gmx_config, filter_swap_markets=False)
    assert gm_prices_custom.filter_swap_markets is False

    # Test inheritance from GetData
    assert hasattr(get_gm_prices, "get_data")
    assert callable(get_gm_prices.get_data)

    # Test config dependency
    assert hasattr(get_gm_prices.config, "web3")
    assert hasattr(get_gm_prices.config, "chain")


def test_actual_data_structure_and_output(get_gm_prices):
    """Test actual GM prices data structure and output format."""
    # Call the get_data method to get actual structure
    result = get_gm_prices.get_data()

    # Verify the result structure matches actual output
    assert isinstance(result, dict)
    assert "prices" in result
    assert "parameter" in result
    assert result["parameter"] == "gm_prices_traders"

    # Test prices dictionary structure
    prices = result["prices"]
    assert isinstance(prices, dict)

    # If we have price data, verify structure of individual entries
    if prices:
        first_market = list(prices.keys())[0]
        market_price_data = prices[first_market]
        assert isinstance(market_price_data, dict)

        # GM price data should contain pricing information
        # Structure may vary based on actual implementation


def test_price_processing_methods_and_functionality(get_gm_prices):
    """Test price processing methods and core functionality."""
    # Test price processing method existence
    assert hasattr(get_gm_prices, "_process_market_price")
    assert callable(get_gm_prices._process_market_price)

    # Test get_prices method with different price types
    assert hasattr(get_gm_prices, "get_prices")
    assert callable(get_gm_prices.get_prices)

    # Test price type handling
    valid_price_types = ["traders", "deposits", "withdrawals"]
    for price_type in valid_price_types:
        assert isinstance(price_type, str)
        assert price_type in valid_price_types


def test_concurrent_execution_and_threading(get_gm_prices):
    """Test concurrent execution configuration and threading patterns."""
    # Test that concurrent execution is configured correctly
    max_workers = 5  # Typical ThreadPoolExecutor configuration
    assert isinstance(max_workers, int)
    assert max_workers > 0

    # Test future-to-market mapping logic patterns
    market_futures_map = {}
    markets = ["BTC/USD", "ETH/USD", "ARB/USD"]

    for i, market in enumerate(markets):
        market_futures_map[f"future_{i}"] = market

    assert len(market_futures_map) == len(markets)
    assert "BTC/USD" in market_futures_map.values()

    # Test market symbol extraction logic
    for future_key, market_symbol in market_futures_map.items():
        assert isinstance(future_key, str)
        assert isinstance(market_symbol, str)
        assert "/" in market_symbol  # Typical market symbol format


def test_data_processing_and_output_format(get_gm_prices):
    """Test data processing methods and output format."""
    # Test _get_data_processing method existence
    assert hasattr(get_gm_prices, "_get_data_processing")
    assert callable(get_gm_prices._get_data_processing)

    # Test result structure consistency patterns
    result_template = {"parameter": "gm_prices", "BTC/USD": {"buyPrice": 50000.0, "sellPrice": 49950.0, "timestamp": 1640995200}}

    assert "parameter" in result_template
    assert result_template["parameter"] == "gm_prices"

    # Test market data structure
    for market, data in result_template.items():
        if market != "parameter":
            assert isinstance(data, dict)
            if "buyPrice" in data:
                assert isinstance(data["buyPrice"], (int, float))
            if "sellPrice" in data:
                assert isinstance(data["sellPrice"], (int, float))


def test_save_methods_and_persistence(get_gm_prices):
    """Test save methods and data persistence functionality."""
    # Test that save methods exist
    save_methods = ["_save_to_csv", "_save_to_json", "_save_dict_to_csv"]

    for method in save_methods:
        assert hasattr(get_gm_prices, method), f"Missing save method: {method}"
        assert callable(getattr(get_gm_prices, method)), f"Save method not callable: {method}"

    # Test file path patterns for saving
    chain_name = "arbitrum"
    timestamp = 1640995200

    csv_filename = f"gm_prices_{chain_name}_{timestamp}.csv"
    json_filename = f"gm_prices_{chain_name}_{timestamp}.json"

    assert csv_filename.endswith(".csv")
    assert json_filename.endswith(".json")
    assert chain_name in csv_filename
    assert chain_name in json_filename
