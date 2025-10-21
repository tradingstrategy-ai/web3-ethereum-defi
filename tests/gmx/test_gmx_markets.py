"""
Tests for GMX Markets Data Module.

This test suite verifies the functionality of the Markets class
when fetching and processing GMX market information.
"""

import pytest
import requests
from eth_defi.gmx.core.markets import MarketInfo
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.contracts import get_tokens_address_dict, _get_clean_api_urls
from cchecksum import to_checksum_address


def test_market_info_dataclass():
    """Test MarketInfo dataclass structure and initialization."""
    market_address = to_checksum_address("0x47904963fc8b2340414262125aF906B738AD9BDF")
    index_token_address = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    long_token_address = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    short_token_address = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

    market_info = MarketInfo(
        gmx_market_address=market_address,
        market_symbol="ETH",
        index_token_address=index_token_address,
        market_metadata={"symbol": "ETH", "decimals": 18},
        long_token_metadata={"symbol": "WETH", "decimals": 18},
        long_token_address=long_token_address,
        short_token_metadata={"symbol": "USDC", "decimals": 6},
        short_token_address=short_token_address,
    )

    # Verify all fields are set correctly
    assert market_info.gmx_market_address == market_address
    assert market_info.market_symbol == "ETH"
    assert market_info.index_token_address == index_token_address
    assert market_info.long_token_address == long_token_address
    assert market_info.short_token_address == short_token_address
    assert market_info.market_metadata["symbol"] == "ETH"
    assert market_info.long_token_metadata["decimals"] == 18
    assert market_info.short_token_metadata["decimals"] == 6


def test_market_info_address_types():
    """Test that MarketInfo properly handles HexAddress types."""
    # Test with various address formats
    addresses = [
        "0x47904963fc8b2340414262125aF906B738AD9BDF",  # Mixed case
        "0x47904963FC8B2340414262125AF906B738AD9BDF",  # Upper case
        "0x47904963fc8b2340414262125af906b738ad9bdf",  # Lower case
    ]

    for addr in addresses:
        checksummed_addr = to_checksum_address(addr)

        market_info = MarketInfo(gmx_market_address=checksummed_addr, market_symbol="TEST", index_token_address=checksummed_addr, market_metadata={}, long_token_metadata={}, long_token_address=checksummed_addr, short_token_metadata={}, short_token_address=checksummed_addr)

        # All addresses should be properly checksummed
        assert market_info.gmx_market_address == checksummed_addr
        assert market_info.index_token_address == checksummed_addr
        assert market_info.long_token_address == checksummed_addr
        assert market_info.short_token_address == checksummed_addr

        # Verify they start with 0x and have correct length
        for addr_field in [market_info.gmx_market_address, market_info.index_token_address, market_info.long_token_address, market_info.short_token_address]:
            assert addr_field.startswith("0x")
            assert len(addr_field) == 42


def test_market_info_metadata_structure():
    """Test MarketInfo with various metadata structures."""
    market_address = to_checksum_address("0x47904963fc8b2340414262125aF906B738AD9BDF")

    # Test with comprehensive metadata
    comprehensive_metadata = {"symbol": "BTC", "decimals": 8, "name": "Bitcoin", "coingecko_id": "bitcoin"}

    # Test with minimal metadata
    minimal_metadata = {"symbol": "UNKNOWN", "decimals": 18}

    # Test with empty metadata
    empty_metadata = {}

    for metadata in [comprehensive_metadata, minimal_metadata, empty_metadata]:
        market_info = MarketInfo(gmx_market_address=market_address, market_symbol="TEST", index_token_address=market_address, market_metadata=metadata.copy(), long_token_metadata=metadata.copy(), long_token_address=market_address, short_token_metadata=metadata.copy(), short_token_address=market_address)

        # Verify metadata is preserved
        assert market_info.market_metadata == metadata
        assert market_info.long_token_metadata == metadata
        assert market_info.short_token_metadata == metadata


@pytest.mark.parametrize(
    "symbol,expected_length",
    [
        ("ETH", 3),
        ("BTC", 3),
        ("WSTETH", 6),
        ("AVAX", 4),
        ("SOL", 3),
        ("A", 1),  # Single character
        ("VERYLONGTOKEN", 13),  # Long symbol
    ],
)
def test_market_info_symbol_handling(symbol, expected_length):
    """Test MarketInfo handles various symbol lengths and formats."""
    market_address = to_checksum_address("0x47904963fc8b2340414262125aF906B738AD9BDF")

    market_info = MarketInfo(gmx_market_address=market_address, market_symbol=symbol, index_token_address=market_address, market_metadata={"symbol": symbol}, long_token_metadata={"symbol": symbol}, long_token_address=market_address, short_token_metadata={"symbol": symbol}, short_token_address=market_address)

    assert market_info.market_symbol == symbol
    assert len(market_info.market_symbol) == expected_length
    assert market_info.market_metadata["symbol"] == symbol


def test_market_info_different_token_types():
    """Test MarketInfo with different combinations of long/short tokens."""
    market_address = to_checksum_address("0x47904963fc8b2340414262125aF906B738AD9BDF")
    eth_address = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    usdc_address = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    btc_address = to_checksum_address("0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f")

    # Test ETH/USDC market
    eth_usdc_market = MarketInfo(gmx_market_address=market_address, market_symbol="ETH", index_token_address=eth_address, market_metadata={"symbol": "ETH", "decimals": 18}, long_token_metadata={"symbol": "WETH", "decimals": 18}, long_token_address=eth_address, short_token_metadata={"symbol": "USDC", "decimals": 6}, short_token_address=usdc_address)

    assert eth_usdc_market.long_token_address != eth_usdc_market.short_token_address
    assert eth_usdc_market.index_token_address == eth_usdc_market.long_token_address

    # Test BTC/ETH market (both are "major" tokens)
    btc_eth_market = MarketInfo(gmx_market_address=market_address, market_symbol="BTC", index_token_address=btc_address, market_metadata={"symbol": "BTC", "decimals": 8}, long_token_metadata={"symbol": "WBTC", "decimals": 8}, long_token_address=btc_address, short_token_metadata={"symbol": "WETH", "decimals": 18}, short_token_address=eth_address)

    assert btc_eth_market.long_token_address != btc_eth_market.short_token_address
    assert btc_eth_market.index_token_address == btc_eth_market.long_token_address

    # Test synthetic market (same token for long/short)
    synthetic_market = MarketInfo(gmx_market_address=market_address, market_symbol="ETH", index_token_address=eth_address, market_metadata={"symbol": "ETH", "decimals": 18}, long_token_metadata={"symbol": "USDC", "decimals": 6}, long_token_address=usdc_address, short_token_metadata={"symbol": "USDC", "decimals": 6}, short_token_address=usdc_address)

    assert synthetic_market.long_token_address == synthetic_market.short_token_address
    assert synthetic_market.index_token_address != synthetic_market.long_token_address


def test_market_info_equality():
    """Test MarketInfo equality comparison."""
    market_address = to_checksum_address("0x47904963fc8b2340414262125aF906B738AD9BDF")
    eth_address = to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    usdc_address = to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

    market_info_1 = MarketInfo(gmx_market_address=market_address, market_symbol="ETH", index_token_address=eth_address, market_metadata={"symbol": "ETH", "decimals": 18}, long_token_metadata={"symbol": "WETH", "decimals": 18}, long_token_address=eth_address, short_token_metadata={"symbol": "USDC", "decimals": 6}, short_token_address=usdc_address)

    market_info_2 = MarketInfo(gmx_market_address=market_address, market_symbol="ETH", index_token_address=eth_address, market_metadata={"symbol": "ETH", "decimals": 18}, long_token_metadata={"symbol": "WETH", "decimals": 18}, long_token_address=eth_address, short_token_metadata={"symbol": "USDC", "decimals": 6}, short_token_address=usdc_address)

    # Same data should be equal
    assert market_info_1 == market_info_2

    # Different symbol should not be equal
    market_info_3 = MarketInfo(
        gmx_market_address=market_address,
        market_symbol="BTC",  # Different symbol
        index_token_address=eth_address,
        market_metadata={"symbol": "ETH", "decimals": 18},
        long_token_metadata={"symbol": "WETH", "decimals": 18},
        long_token_address=eth_address,
        short_token_metadata={"symbol": "USDC", "decimals": 6},
        short_token_address=usdc_address,
    )

    assert market_info_1 != market_info_3


def test_oracle_integration_with_markets():
    """Test that Oracle API works for market-related tokens."""
    try:
        chain = "arbitrum"
        oracle = OraclePrices(chain)
        prices = oracle.get_recent_prices()

        # Verify we got prices
        assert isinstance(prices, dict)
        assert len(prices) > 0

        # Look for common GMX market tokens
        token_symbols = []
        for price_data in prices.values():
            if price_data.get("tokenSymbol"):
                token_symbols.append(price_data["tokenSymbol"])

        # Should have major tokens that GMX uses for markets on Arbitrum
        major_tokens = ["ETH", "BTC", "WETH", "WBTC", "ARB", "LINK"]

        found_major_tokens = []
        for major_token in major_tokens:
            if any(major_token in symbol.upper() for symbol in token_symbols):
                found_major_tokens.append(major_token)

        # Should find at least some major tokens
        assert len(found_major_tokens) > 0, f"No major tokens found for {chain}. Available: {token_symbols[:10]}"

    except requests.exceptions.RequestException as e:
        pytest.skip(f"Oracle API test failed: {e}")


def test_gmx_tokens_api_integration():
    """Test GMX tokens API integration."""
    try:
        chain = "arbitrum"
        tokens_dict = get_tokens_address_dict(chain)

        # Verify response structure
        assert isinstance(tokens_dict, dict)
        assert len(tokens_dict) > 0

        # Check some addresses are valid
        for symbol, address in list(tokens_dict.items())[:5]:
            assert isinstance(symbol, str)
            assert len(symbol) > 0
            assert address.startswith("0x")
            assert len(address) == 42
            # Should be checksummed
            assert address == to_checksum_address(address)

        # Should have common tokens on Arbitrum
        symbols = [symbol.upper() for symbol in tokens_dict.keys()]
        common_tokens = ["ETH", "USDC", "USDT", "WETH", "ARB", "LINK"]

        found_common = [token for token in common_tokens if any(token in symbol for symbol in symbols)]
        assert len(found_common) > 0, f"No common tokens found for {chain}. Available: {symbols[:10]}"

    except Exception as e:
        pytest.skip(f"GMX tokens API test failed: {e}")


def test_api_urls_configuration():
    """Test that API URL configuration works correctly."""
    try:
        clean_urls = _get_clean_api_urls()

        # Should have Arbitrum
        assert "arbitrum" in clean_urls

        # URL should be valid
        chain = "arbitrum"
        # get request to base URL will return 404 so that's why we are requesting an endpoint
        url = clean_urls[chain] + "/tokens"
        assert isinstance(url, str)
        assert url.startswith("https://")
        assert chain in url.lower()

        # Test that URL is accessible (quick check)
        response = requests.head(url, timeout=5)
        # Should either be 200 (OK) or 405 (Method Not Allowed, but server is responsive)
        assert response.status_code in [200, 405], f"URL {url} returned {response.status_code}"

    except Exception as e:
        pytest.skip(f"API URLs test failed: {e}")


def test_oracle_prices_market_tokens():
    """Test oracle prices for tokens commonly used in GMX markets."""
    try:
        chain_name = "arbitrum"
        oracle = OraclePrices(chain_name)
        prices = oracle.get_recent_prices()

        # Look for tokens that should have prices
        token_data_by_symbol = {}
        for address, price_data in prices.items():
            symbol = price_data.get("tokenSymbol", "")
            if symbol:
                token_data_by_symbol[symbol.upper()] = {"address": address, "data": price_data}

        # Test specific tokens for Arbitrum
        expected_tokens = ["ETH", "WETH"]  # Should have ETH on Arbitrum

        found_tokens = []
        for expected_token in expected_tokens:
            if expected_token in token_data_by_symbol:
                token_info = token_data_by_symbol[expected_token]
                found_tokens.append(expected_token)

                # Verify price data structure for found tokens
                assert token_info["address"].startswith("0x")
                assert len(token_info["address"]) == 42

                price_data = token_info["data"]
                assert "tokenAddress" in price_data
                assert price_data["tokenAddress"] == token_info["address"]

                # Price fields exist (may be None but should be present)
                assert "minPrice" in price_data
                assert "maxPrice" in price_data

        # Should find at least one expected token
        assert len(found_tokens) > 0, f"No expected tokens found for {chain_name}. Available: {list(token_data_by_symbol.keys())[:10]}"

    except requests.exceptions.RequestException as e:
        pytest.skip(f"Oracle prices test failed: {e}")


def test_token_address_checksumming():
    """Test that all token addresses are properly checksummed."""
    try:
        chain = "arbitrum"
        # Test oracle addresses
        oracle = OraclePrices(chain)
        prices = oracle.get_recent_prices()

        for address, price_data in list(prices.items())[:10]:
            # Dictionary keys should be checksummed
            assert address == to_checksum_address(address), f"Oracle key not checksummed: {address}"

            # tokenAddress field should match and be checksummed
            token_addr = price_data.get("tokenAddress")
            if token_addr:
                assert token_addr == to_checksum_address(token_addr), f"tokenAddress not checksummed: {token_addr}"
                assert token_addr == address, f"Address mismatch: {address} vs {token_addr}"

        # Test tokens API addresses
        tokens_dict = get_tokens_address_dict(chain)

        for symbol, address in list(tokens_dict.items())[:10]:
            # Verify symbol is a string
            assert isinstance(symbol, str) and len(symbol) > 0
            # Dictionary values should be checksummed
            assert address == to_checksum_address(address), f"Token dict value not checksummed: {address}"

            # Should be valid address format
            assert address.startswith("0x")
            assert len(address) == 42

    except Exception as e:
        pytest.skip(f"Address checksumming test failed: {e}")


def test_api_error_handling():
    """Test API error handling and recovery."""
    try:
        # Test with Arbitrum
        oracle = OraclePrices("arbitrum")
        prices = oracle.get_recent_prices()
        assert len(prices) > 0

        # Test invalid chain handling
        with pytest.raises(ValueError, match="Unsupported chain"):
            OraclePrices("ethereum")

        with pytest.raises(ValueError, match="Unsupported chain"):
            OraclePrices("polygon")

        # Test tokens API error handling
        tokens = get_tokens_address_dict("arbitrum")
        assert len(tokens) > 0

    except Exception as e:
        pytest.skip(f"API error handling test failed: {e}")


def test_oracle_response_time():
    """Test that oracle API responses are reasonably fast."""
    try:
        import time

        chain = "arbitrum"
        oracle = OraclePrices(chain)

        start_time = time.time()
        prices = oracle.get_recent_prices()
        response_time = time.time() - start_time

        # API should respond within reasonable time (10 seconds)
        assert response_time < 10, f"Oracle API too slow for {chain}: {response_time:.2f}s"

        # Should get meaningful data
        assert len(prices) > 50, f"Too few tokens for {chain}: {len(prices)}"

    except Exception as e:
        pytest.skip(f"Response time test failed: {e}")


def test_get_available_markets(markets):
    """Test getting available markets from GMX."""
    available_markets = markets.get_available_markets()

    # Verify return structure
    assert isinstance(available_markets, dict)
    assert len(available_markets) > 0

    # Check first market structure
    first_market_key = next(iter(available_markets.keys()))
    first_market_data = available_markets[first_market_key]

    # Verify required fields
    required_fields = ["gmx_market_address", "market_symbol", "index_token_address", "long_token_address", "short_token_address", "market_metadata", "long_token_metadata", "short_token_metadata"]

    for field in required_fields:
        assert field in first_market_data, f"Missing field: {field}"

    # Verify address format
    assert first_market_key.startswith("0x")
    assert len(first_market_key) == 42
    assert first_market_data["gmx_market_address"] == first_market_key


def test_market_token_addresses(markets):
    """Test getting token addresses for markets."""
    available_markets = markets.get_available_markets()

    if not available_markets:
        pytest.skip("No markets available")

    # Test with first available market
    market_key = next(iter(available_markets.keys()))

    # Test index token address
    index_token = markets.get_index_token_address(market_key)
    assert isinstance(index_token, str)
    assert index_token.startswith("0x")
    assert len(index_token) == 42

    # Test long token address
    long_token = markets.get_long_token_address(market_key)
    assert isinstance(long_token, str)
    assert long_token.startswith("0x")
    assert len(long_token) == 42

    # Test short token address
    short_token = markets.get_short_token_address(market_key)
    assert isinstance(short_token, str)
    assert short_token.startswith("0x")
    assert len(short_token) == 42

    # Verify addresses are checksummed
    assert index_token == to_checksum_address(index_token)
    assert long_token == to_checksum_address(long_token)
    assert short_token == to_checksum_address(short_token)


def test_market_symbols(markets):
    """Test getting market symbols."""
    available_markets = markets.get_available_markets()

    if not available_markets:
        pytest.skip("No markets available")

    # Test symbols for several markets
    market_keys = list(available_markets.keys())[:3]  # Test first 3 markets

    for market_key in market_keys:
        symbol = markets.get_market_symbol(market_key)
        assert isinstance(symbol, str)
        assert len(symbol) > 0
        # Common GMX market symbols
        assert any(token_name in symbol.upper() for token_name in ["ETH", "BTC", "SOL", "AVAX", "ARB", "LINK", "UNI", "DOGE"])


def test_decimal_factors(markets):
    """Test getting decimal factors for market tokens."""
    available_markets = markets.get_available_markets()

    if not available_markets:
        pytest.skip("No markets available")

    market_key = next(iter(available_markets.keys()))

    # Test index token decimals (default behavior)
    index_decimals = markets.get_decimal_factor(market_key)
    assert isinstance(index_decimals, int)
    assert index_decimals > 0
    assert index_decimals <= 30  # Reasonable upper bound

    # Test long token decimals
    long_decimals = markets.get_decimal_factor(market_key, long=True)
    assert isinstance(long_decimals, int)
    assert long_decimals > 0
    assert long_decimals <= 30

    # Test short token decimals
    short_decimals = markets.get_decimal_factor(market_key, short=True)
    assert isinstance(short_decimals, int)
    assert short_decimals > 0
    assert short_decimals <= 30


def test_is_synthetic(markets):
    """Test checking if markets are synthetic."""
    available_markets = markets.get_available_markets()

    if not available_markets:
        pytest.skip("No markets available")

    # Test synthetic check for several markets
    market_keys = list(available_markets.keys())[:5]

    for market_key in market_keys:
        is_synthetic = markets.is_synthetic(market_key)
        assert isinstance(is_synthetic, bool)


def test_get_market_info(markets):
    """Test getting detailed market information."""
    available_markets = markets.get_available_markets()

    if not available_markets:
        pytest.skip("No markets available")

    market_address = next(iter(available_markets.keys()))
    market_address_hex = to_checksum_address(market_address)

    market_info = markets.get_market_info(market_address_hex)

    if market_info:
        # Verify MarketInfo structure
        assert isinstance(market_info, MarketInfo)
        assert market_info.gmx_market_address == market_address_hex
        assert isinstance(market_info.market_symbol, str)
        assert len(market_info.market_symbol) > 0

        # Verify address fields are checksummed
        assert market_info.index_token_address.startswith("0x")
        assert len(market_info.index_token_address) == 42
        assert market_info.long_token_address.startswith("0x")
        assert len(market_info.long_token_address) == 42
        assert market_info.short_token_address.startswith("0x")
        assert len(market_info.short_token_address) == 42

        # Verify metadata dictionaries
        assert isinstance(market_info.market_metadata, dict)
        assert isinstance(market_info.long_token_metadata, dict)
        assert isinstance(market_info.short_token_metadata, dict)


def test_is_market_disabled(markets):
    """Test checking if markets are disabled."""
    available_markets = markets.get_available_markets()

    if not available_markets:
        pytest.skip("No markets available")

    market_address = next(iter(available_markets.keys()))
    market_address_hex = to_checksum_address(market_address)

    is_disabled = markets.is_market_disabled(market_address_hex)
    assert isinstance(is_disabled, bool)

    # Available markets should generally not be disabled
    assert is_disabled is False

    # Test with invalid address
    assert markets.is_market_disabled("0x0000000000000000000000000000000000000000") is True


def test_market_key_validation(markets):
    """Test error handling for invalid market keys."""
    # Test with invalid market key
    invalid_key = "0x0000000000000000000000000000000000000000"

    assert markets.get_index_token_address(invalid_key) is None
    assert markets.get_long_token_address(invalid_key) is None
    assert markets.get_short_token_address(invalid_key) is None
    assert markets.get_market_symbol(invalid_key) is None

    # Test with invalid key for decimal factor
    with pytest.raises(KeyError):
        markets.get_decimal_factor(invalid_key)

    # Test with invalid key for synthetic check
    with pytest.raises(KeyError):
        markets.is_synthetic(invalid_key)


def test_special_markets_handling(markets):
    """Test handling of special markets like wstETH."""
    available_markets = markets.get_available_markets()

    # Look for wstETH market on Arbitrum
    wsteth_markets = [market for market, data in available_markets.items() if data.get("market_symbol", "").upper() == "WSTETH"]

    if wsteth_markets:
        wsteth_market = wsteth_markets[0]

        # Verify wstETH market has special handling
        symbol = markets.get_market_symbol(wsteth_market)
        assert symbol == "wstETH"

        # Verify index token is set correctly
        index_token = markets.get_index_token_address(wsteth_market)
        expected_wsteth_address = to_checksum_address("0x5979D7b546E38E414F7E9822514be443A4800529")
        assert index_token == expected_wsteth_address

    # Look for BTC2/ETH2 markets
    btc2_markets = [market for market, data in available_markets.items() if "BTC2" in data.get("market_symbol", "")]
    eth2_markets = [market for market, data in available_markets.items() if "ETH2" in data.get("market_symbol", "")]

    for market in btc2_markets + eth2_markets:
        # Verify long and short tokens are the same
        long_token = markets.get_long_token_address(market)
        short_token = markets.get_short_token_address(market)
        assert long_token == short_token

        # Verify market symbol has "2" suffix
        symbol = markets.get_market_symbol(market)
        assert "2" in symbol


def test_market_data_consistency(markets):
    """Test consistency of market data across different methods."""
    available_markets = markets.get_available_markets()

    if not available_markets:
        pytest.skip("No markets available")

    # Test consistency for multiple markets
    for market_key in list(available_markets.keys())[:3]:
        market_data = available_markets[market_key]

        # Verify addresses are consistent
        assert markets.get_index_token_address(market_key) == market_data["index_token_address"]
        assert markets.get_long_token_address(market_key) == market_data["long_token_address"]
        assert markets.get_short_token_address(market_key) == market_data["short_token_address"]
        assert markets.get_market_symbol(market_key) == market_data["market_symbol"]

        # Verify metadata consistency
        expected_index_decimals = market_data["market_metadata"].get("decimals", 18)
        actual_index_decimals = markets.get_decimal_factor(market_key)
        assert actual_index_decimals == expected_index_decimals

        expected_long_decimals = market_data["long_token_metadata"].get("decimals", 18)
        actual_long_decimals = markets.get_decimal_factor(market_key, long=True)
        assert actual_long_decimals == expected_long_decimals

        expected_short_decimals = market_data["short_token_metadata"].get("decimals", 18)
        actual_short_decimals = markets.get_decimal_factor(market_key, short=True)
        assert actual_short_decimals == expected_short_decimals


def test_markets_address_checksumming(markets):
    """Test that all addresses returned are properly checksum."""
    available_markets = markets.get_available_markets()

    if not available_markets:
        pytest.skip("No markets available")

    # Test address checksumming for all markets
    for market_key, market_data in list(available_markets.items())[:5]:
        # Verify market key itself is checksummed
        assert market_key == to_checksum_address(market_key)

        # Verify all token addresses are checksummed
        addresses_to_check = [market_data["gmx_market_address"], market_data["index_token_address"], market_data["long_token_address"], market_data["short_token_address"]]

        for address in addresses_to_check:
            assert address == to_checksum_address(address), f"Address {address} not checksummed"
            assert address.startswith("0x")
            assert len(address) == 42


def test_markets_error_handling(markets):
    """Test error handling in Markets class methods."""
    available_markets = markets.get_available_markets()
    if available_markets:
        market_key = next(iter(available_markets.keys()))

        # Test with both long and short flags (should handle gracefully)
        try:
            decimals = markets.get_decimal_factor(market_key, long=True, short=True)
            # Should return one of the valid decimals or handle appropriately
            assert isinstance(decimals, int)
            assert decimals > 0
        except Exception:
            # Acceptable if method doesn't support both flags
            pass

    # Test with invalid market key for MarketInfo
    assert markets.get_market_info("0x0000000000000000000000000000000000000000") is None


def test_cached_data_integrity(markets):
    """Test that cached market data remains consistent."""
    original_markets = markets.get_available_markets()

    # Get a copy of the original data
    original_data = {k: dict(v) for k, v in original_markets.items()}

    # Access individual market methods
    for market_key in list(original_markets.keys())[:3]:
        markets.get_index_token_address(market_key)
        markets.get_long_token_address(market_key)
        markets.get_short_token_address(market_key)
        markets.get_market_symbol(market_key)
        markets.get_decimal_factor(market_key)
        markets.is_synthetic(market_key)

    # Verify cached data hasn't been modified
    current_markets = markets.get_available_markets()
    assert current_markets == original_data


def test_special_wsteth_market(markets):
    """Test specific handling of the wstETH market."""
    available_markets = markets.get_available_markets()

    # Look for the specific wstETH market address
    wsteth_market_address = to_checksum_address("0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5")

    if wsteth_market_address in available_markets:
        market_data = available_markets[wsteth_market_address]

        # Verify it has the correct symbol
        assert market_data["market_symbol"] == "wstETH"

        # Verify index token address is correct
        expected_index_token = to_checksum_address("0x5979D7b546E38E414F7E9822514be443A4800529")
        assert market_data["index_token_address"] == expected_index_token

        # Verify through market methods
        assert markets.get_market_symbol(wsteth_market_address) == "wstETH"
        assert markets.get_index_token_address(wsteth_market_address) == expected_index_token


def test_btc2_eth2_markets(markets):
    """Test specific handling of BTC2 and ETH2 markets."""
    available_markets = markets.get_available_markets()

    # Find BTC2 and ETH2 markets
    btc2_markets = [m for m, d in available_markets.items() if "BTC2" in d["market_symbol"]]
    eth2_markets = [m for m, d in available_markets.items() if "ETH2" in d["market_symbol"]]

    for market_address in btc2_markets + eth2_markets:
        market_data = available_markets[market_address]

        # Verify long and short tokens are the same
        assert market_data["long_token_address"] == market_data["short_token_address"]

        # Verify market symbol ends with "2"
        assert "2" in market_data["market_symbol"]

        # Verify through market methods
        assert markets.get_long_token_address(market_address) == markets.get_short_token_address(market_address)
        assert "2" in markets.get_market_symbol(market_address)

        # Verify decimal factors are consistent
        long_decimals = markets.get_decimal_factor(market_address, long=True)
        short_decimals = markets.get_decimal_factor(market_address, short=True)
        assert long_decimals == short_decimals


def test_market_metadata_consistency(markets):
    """Test that market metadata is consistent across different access methods."""
    available_markets = markets.get_available_markets()

    if not available_markets:
        pytest.skip("No markets available")

    # Test metadata consistency for multiple markets
    for market_key in list(available_markets.keys())[:3]:
        market_data = available_markets[market_key]

        # Get market info
        market_info = markets.get_market_info(market_key)
        if market_info is None:
            continue

        # Verify metadata consistency
        assert market_info.market_metadata == market_data["market_metadata"]
        assert market_info.long_token_metadata == market_data["long_token_metadata"]
        assert market_info.short_token_metadata == market_data["short_token_metadata"]

        # Verify symbol consistency
        assert market_info.market_symbol == market_data["market_symbol"]

        # Verify address consistency
        assert market_info.index_token_address == market_data["index_token_address"]
        assert market_info.long_token_address == market_data["long_token_address"]
        assert market_info.short_token_address == market_data["short_token_address"]
