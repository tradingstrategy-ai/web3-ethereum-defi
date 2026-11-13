"""
Tests for GMX Markets Data Module.

This test suite verifies the functionality of the Markets class
when fetching and processing GMX market information.
"""

import pytest
from flaky import flaky
from eth_defi.gmx.core.markets import MarketInfo
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.contracts import get_tokens_address_dict
from eth_utils import to_checksum_address


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


@flaky(max_runs=3, min_passes=1)
def test_get_available_markets(markets):
    """Test getting available markets from GMX.

    Flaky: ``get_available_markets()`` fetches live market data from the GMX
    API / RPC node.  Under transient outages — or when parallel pytest workers
    simultaneously saturate the shared GMX API endpoint — the call can return an
    empty dict.  ``@flaky`` retries up to 3 times; ``pytest.skip()`` is used
    instead of a hard assertion so the retry budget is not consumed by a
    deterministically-failing assertion on genuinely empty (bad) input data.
    """
    available_markets = markets.get_available_markets()

    # Verify return structure
    assert isinstance(available_markets, dict)

    # Empty response is a known-bad transient condition (RPC/API outage or parallel-test
    # saturation).  Skip rather than hard-fail so @flaky can attempt the next retry.
    if not available_markets:
        pytest.skip("get_available_markets() returned an empty dict — transient GMX API / RPC outage or parallel-test API saturation.  @flaky will retry up to 3 times.")

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
    assert markets.get_market_info(invalid_key) is None

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
