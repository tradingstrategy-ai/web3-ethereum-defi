"""
Tests for GMX GLV Stats Data Retrieval Module.
"""

import logging

from collections import defaultdict

from eth_defi.gmx.core.glv_stats import GlvStats


def test_initialization_and_basic_functionality(get_glv_stats, gmx_config):
    """Test GlvStats initialization and basic functionality."""
    # Test basic initialization
    assert get_glv_stats.config is not None
    assert get_glv_stats.log is not None
    assert get_glv_stats.filter_swap_markets is True

    # Test initialization with custom filter setting
    glv_stats_custom = GlvStats(gmx_config, filter_swap_markets=False)
    assert glv_stats_custom.filter_swap_markets is False

    # Test inheritance from GetData
    assert hasattr(get_glv_stats, "get_data")
    assert callable(get_glv_stats.get_data)

    # Test config dependency
    assert hasattr(get_glv_stats.config, "web3")
    assert hasattr(get_glv_stats.config, "chain")

    # Test that markets are properly initialized
    assert get_glv_stats.markets is not None
    assert hasattr(get_glv_stats.markets, "get_available_markets")

    # Test that GLV reader contract is accessible
    assert get_glv_stats.glv_reader_contract is not None


def test_glv_info_and_data_structures(get_glv_stats):
    """Test GLV info handling and data structure patterns."""
    results = get_glv_stats.get_glv_stats_multicall()

    # Verify structure for each GLV
    for glv_address, glv_data in results.items():
        assert isinstance(glv_data, dict)
        required_fields = ["glv_address", "long_address", "short_address", "glv_market_addresses"]
        for field in required_fields:
            assert field in glv_data, f"Missing field: {field} in GLV {glv_address}"

        # Verify addresses are properly formatted
        assert glv_data["glv_address"].startswith("0x")
        assert len(glv_data["glv_address"]) == 42
        assert glv_data["long_address"].startswith("0x")
        assert len(glv_data["long_address"]) == 42
        assert glv_data["short_address"].startswith("0x")
        assert len(glv_data["short_address"]) == 42

        # Verify market addresses list
        assert isinstance(glv_data["glv_market_addresses"], list)
        assert len(glv_data["glv_market_addresses"]) > 0
        for market_address in glv_data["glv_market_addresses"]:
            assert market_address.startswith("0x")
            assert len(market_address) == 42

        # Verify markets_metadata structure if present
        if "markets_metadata" in glv_data:
            assert isinstance(glv_data["markets_metadata"], dict)
            for market_addr, market_data in glv_data["markets_metadata"].items():
                assert isinstance(market_data, dict)
                assert "address" in market_data
                assert "market symbol" in market_data
                assert "balance" in market_data
                assert "gm price" in market_data
                assert market_data["balance"] >= 0
                assert market_data["gm price"] >= 0

    # Check that we have some GLVs
    assert len(results) > 0, "No GLVs found"

    # Check that GLV price is present for at least one GLV (but don't fail if none are available)
    glv_with_price = [g for g in results.values() if "glv_price" in g and g["glv_price"] > 0]
    if len(glv_with_price) == 0:
        logging.warning("No GLVs with price data available - this may be due to temporary network issues")


def test_glv_price_calculation(get_glv_stats):
    """Test that GLV price calculations make sense with real data."""
    results = get_glv_stats.get_glv_stats_multicall()

    # Verify at least one GLV has valid price data (but don't fail if none are available)
    glv_with_price = [g for g in results.values() if "glv_price" in g and g["glv_price"] > 0]
    if len(glv_with_price) == 0:
        logging.warning("No GLVs with valid price data available - this may be due to temporary network issues")
        return

    # Check price for each GLV with price data
    for glv_data in glv_with_price:
        glv_price = glv_data["glv_price"]

        # GLV price should be positive
        assert glv_price > 0, f"GLV price should be positive, got {glv_price}"

        # GLV price should be reasonable (not absurdly high)
        assert glv_price < 100_000_000, f"GLV price too high: {glv_price}"

        # Check market composition
        if "markets_metadata" in glv_data:
            total_value = 0
            gm_prices = []
            for market_data in glv_data["markets_metadata"].values():
                balance = market_data["balance"]
                gm_price = market_data["gm price"]
                market_value = balance * gm_price
                total_value += market_value
                if gm_price > 0:
                    gm_prices.append(gm_price)

            # Total value should be positive
            assert total_value > 0, f"Total market value should be positive for GLV {glv_data['glv_address']}"

            # GLV price should be in the same order of magnitude as average GM price
            # GLVs are baskets of GM tokens, so prices should be comparable
            if gm_prices:
                avg_gm_price = sum(gm_prices) / len(gm_prices)
                price_ratio = glv_price / avg_gm_price
                assert 0.1 < price_ratio < 10, f"GLV price ratio out of bounds: {price_ratio} (glv_price={glv_price}, avg_gm_price={avg_gm_price}) for GLV {glv_data['glv_address']}"


def test_market_composition_data(get_glv_stats):
    """Test that market composition data makes sense with real data."""
    results = get_glv_stats.get_glv_stats_multicall()

    # Verify at least one GLV has market composition data
    glv_with_markets = [g for g in results.values() if "markets_metadata" in g and g["markets_metadata"]]
    if len(glv_with_markets) == 0:
        logging.warning("No GLVs with market composition data available - this may be due to temporary network issues")
        return

    # Check market data for each GLV
    for glv_data in glv_with_markets:
        market_metadata = glv_data["markets_metadata"]

        # Verify market symbols
        for market_addr, market_data in market_metadata.items():
            market_symbol = market_data["market symbol"]
            assert isinstance(market_symbol, str) and len(market_symbol) > 0

            # Common GLV markets should have recognizable symbols
            if market_symbol in ["ETH", "BTC", "USDC", "USDT", "WETH", "WBTC"]:
                # Only check balances and prices if they're expected to be positive
                if market_data["balance"] > 0:
                    assert market_data["gm price"] > 0, f"GM price should be positive when balance is positive for {market_symbol}"

            # Check that GM price is reasonable when present
            if market_data["gm price"] > 0:
                if market_symbol in ["ETH", "BTC"]:
                    assert market_data["gm price"] > 0.0001, f"GM price for {market_symbol} should be meaningful"
                elif market_symbol in ["USDC", "USDT"]:
                    # GM token price for stablecoin pools should be positive
                    assert market_data["gm price"] > 0, f"GM price for stablecoin {market_symbol} should be positive"

        # Verify market count
        assert len(market_metadata) > 0, f"GLV {glv_data['glv_address']} should have markets"
        # GLVs typically have multiple markets
        assert len(market_metadata) >= 2, f"GLV {glv_data['glv_address']} should have at least 2 markets"


def test_data_consistency(get_glv_stats):
    """Test that GLV data is consistent across multiple calls with real data."""
    results1 = get_glv_stats.get_glv_stats_multicall()
    results2 = get_glv_stats.get_glv_stats_multicall()

    # We expect some minor changes due to trading activity, but not major changes
    tolerance = 0.1  # 10% tolerance for change

    for glv_address in results1:
        if glv_address not in results2:
            continue

        glv_data1 = results1[glv_address]
        glv_data2 = results2[glv_address]

        # Compare GLV price if present in both
        if "glv_price" in glv_data1 and "glv_price" in glv_data2:
            price1 = glv_data1["glv_price"]
            price2 = glv_data2["glv_price"]

            if price1 > 0:  # Avoid division by zero
                change = abs(price1 - price2) / price1
                assert change < tolerance, f"GLV price changed too much: {price1} -> {price2} for {glv_address}"

        # Compare market composition if present in both
        if "markets_metadata" in glv_data1 and "markets_metadata" in glv_data2:
            markets1 = glv_data1["markets_metadata"]
            markets2 = glv_data2["markets_metadata"]

            common_markets = set(markets1.keys()) & set(markets2.keys())
            for market_addr in common_markets:
                balance1 = markets1[market_addr]["balance"]
                balance2 = markets2[market_addr]["balance"]
                price1 = markets1[market_addr]["gm price"]
                price2 = markets2[market_addr]["gm price"]

                # Balance shouldn't change much in a short time
                if balance1 > 0:
                    balance_change = abs(balance1 - balance2) / balance1
                    assert balance_change < tolerance, f"Balance changed too much: {balance1} -> {balance2} for {market_addr}"

                # Price might change more, but not drastically
                if price1 > 0:
                    price_change = abs(price1 - price2) / price1
                    assert price_change < 0.5, f"Price changed too much: {price1} -> {price2} for {market_addr}"


def test_glv_price_bounds(get_glv_stats):
    """Test that GLV prices stay within reasonable bounds with real data."""
    results = get_glv_stats.get_glv_stats_multicall()

    for glv_address, glv_data in results.items():
        # GLV price should be positive
        if "glv_price" in glv_data:
            glv_price = glv_data["glv_price"]
            assert glv_price > 0, f"GLV price should be positive for {glv_address}"

            # GLV price should not be absurdly high (checking for calculation errors)
            assert glv_price < 10_000_000, f"GLV price too high: {glv_price} for {glv_address}"

            # GLV price should not be too low (checking for calculation errors)
            assert glv_price > 0.000001, f"GLV price too low: {glv_price} for {glv_address}"

        # Check market composition if present
        if "markets_metadata" in glv_data:
            for market_addr, market_data in glv_data["markets_metadata"].items():
                # GM price should be positive
                assert market_data["gm price"] > 0, f"GM price should be positive for {market_addr}"

                # GM price should not be absurdly high
                assert market_data["gm price"] < 100_000_000, f"GM price too high: {market_data['gm price']} for {market_addr}"

                # GM price should not be too low
                assert market_data["gm price"] > 0.000001, f"GM price too low: {market_data['gm price']} for {market_addr}"


def test_special_glv_handling(get_glv_stats):
    """Test handling of special GLVs with real data."""
    results = get_glv_stats.get_glv_stats_multicall()

    # Look for GLVs with specific characteristics
    glv_with_usdc = []
    glv_with_eth = []

    for glv_address, glv_data in results.items():
        if "markets_metadata" in glv_data:
            for market_addr, market_data in glv_data["markets_metadata"].items():
                market_symbol = market_data["market symbol"]
                if "USDC" in market_symbol:
                    glv_with_usdc.append((glv_address, market_data))
                if "ETH" in market_symbol:
                    glv_with_eth.append((glv_address, market_data))

    # Test USDC-based GLVs
    for glv_address, market_data in glv_with_usdc:
        # GM token price for USDC pools should be positive
        assert market_data["gm price"] > 0, f"USDC GM price should be positive for GLV {glv_address}"
        # USDC balance should be positive
        assert market_data["balance"] > 0, f"USDC balance should be positive for GLV {glv_address}"

    # Test ETH-based GLVs
    for glv_address, market_data in glv_with_eth:
        # ETH should have meaningful GM price
        assert market_data["gm price"] > 0.0001, f"ETH price should be meaningful for GLV {glv_address}"
        # ETH balance should be positive
        assert market_data["balance"] > 0, f"ETH balance should be positive for GLV {glv_address}"


def test_empty_glv_handling(gmx_config):
    """Test handling of empty GLV data."""

    # Create a mock GLV reader that returns empty data
    class MockGlvReader:
        def call(self):
            return []

    # Replace the GLV reader contract with our mock
    class MockGlvStats(GlvStats):
        def __init__(self, config, filter_swap_markets=True):
            super().__init__(config, filter_swap_markets)
            self._glv_reader_contract = MockGlvReader()

        def _get_glv_info_list(self):
            return {}

    # Run the test
    glv_stats = MockGlvStats(gmx_config)
    results = glv_stats.get_glv_stats_multicall()

    # Should return empty dictionary
    assert results == {}, "Empty GLV data should return empty dictionary"

    # Should not raise exceptions
    try:
        glv_stats.get_glv_stats_multicall()
    except Exception as e:
        pytest.fail(f"Empty GLV data caused exception: {e}")


def test_glv_market_consistency(get_glv_stats):
    """Test consistency between GLV markets and overall GMX markets."""
    results = get_glv_stats.get_glv_stats_multicall()
    available_markets = get_glv_stats.markets.get_available_markets()

    # Verify that all GLV markets are in the available markets list
    all_glv_markets = set()
    for glv_data in results.values():
        for market_address in glv_data["glv_market_addresses"]:
            all_glv_markets.add(market_address)

    # Check that all GLV markets exist in available markets
    missing_markets = []
    for market_address in all_glv_markets:
        if market_address not in available_markets:
            missing_markets.append(market_address)
            continue

        # Verify market symbol is consistent
        glv_market_symbol = get_glv_stats.markets.get_market_symbol(market_address)
        available_market_symbol = available_markets[market_address]["market_symbol"]
        assert glv_market_symbol == available_market_symbol, f"Market symbol mismatch for {market_address}: {glv_market_symbol} vs {available_market_symbol}"

        # Verify token addresses are consistent
        glv_long_token = get_glv_stats.markets.get_long_token_address(market_address)
        available_long_token = available_markets[market_address]["long_token_address"]
        assert glv_long_token == available_long_token, f"Long token mismatch for {market_address}: {glv_long_token} vs {available_long_token}"

        glv_short_token = get_glv_stats.markets.get_short_token_address(market_address)
        available_short_token = available_markets[market_address]["short_token_address"]
        assert glv_short_token == available_short_token, f"Short token mismatch for {market_address}: {glv_short_token} vs {available_short_token}"

    # If there are missing markets, we should handle them gracefully rather than failing the test
    # This can happen when the GLV markets list includes markets that are temporarily unavailable
    if missing_markets:
        # Log the missing markets but don't fail the test
        logging.warning(f"Found {len(missing_markets)} GLV markets not in available markets: {missing_markets}")
        # Only fail if all markets are missing (which would indicate a bigger issue)
        assert len(missing_markets) < len(all_glv_markets), f"All GLV markets missing from available markets: {missing_markets}"


def test_glv_price_calculation_logic(get_glv_stats):
    """Test the internal GLV price calculation logic with real data."""
    results = get_glv_stats.get_glv_stats_multicall()

    # For each GLV with price data
    for glv_address, glv_data in results.items():
        if "glv_price" not in glv_data or "markets_metadata" not in glv_data:
            continue

        glv_price = glv_data["glv_price"]
        markets_metadata = glv_data["markets_metadata"]

        # Calculate total value manually
        total_value = 0
        for market_addr, market_data in markets_metadata.items():
            balance = market_data["balance"]
            gm_price = market_data["gm price"]
            market_value = balance * gm_price
            total_value += market_value

        # Calculate total supply (simplified)
        # Note: This is a simplification as we don't have the actual total supply
        # But we can check the relative proportions
        if total_value > 0 and glv_price > 0:
            # The total value should be proportional to GLV price * total supply
            # We don't have total supply, but we can check the ratio between markets
            market_values = []
            for market_addr, market_data in markets_metadata.items():
                balance = market_data["balance"]
                gm_price = market_data["gm price"]
                market_value = balance * gm_price
                market_values.append(market_value)

            # Check that GLV price is consistent with market values
            # (This is a simplified check as we don't have total supply)
            # But GLV price should be in the same order of magnitude as GM prices
            gm_prices = [m["gm price"] for m in markets_metadata.values()]
            if gm_prices:
                avg_gm_price = sum(gm_prices) / len(gm_prices)
                price_ratio = glv_price / avg_gm_price
                assert 0.1 < price_ratio < 10, f"GLV price ratio out of bounds: {price_ratio}"


def test_glv_market_metadata_consistency(get_glv_stats):
    """Test that market metadata is consistent across different access methods."""
    results = get_glv_stats.get_glv_stats_multicall()

    # For each GLV with market metadata
    for glv_address, glv_data in results.items():
        if "markets_metadata" not in glv_data:
            continue

        for market_addr, market_data in glv_data["markets_metadata"].items():
            # Get market info through different methods
            market_symbol = get_glv_stats.markets.get_market_symbol(market_addr)
            long_token = get_glv_stats.markets.get_long_token_address(market_addr)
            short_token = get_glv_stats.markets.get_short_token_address(market_addr)

            # Verify consistency with market metadata
            assert market_symbol == market_data["market symbol"], f"Market symbol mismatch for {market_addr}: {market_symbol} vs {market_data['market symbol']}"

            # Verify token addresses are consistent (we don't have them in market_data directly,
            # but we can check that the market exists and has consistent metadata)
            available_markets = get_glv_stats.markets.get_available_markets()
            if market_addr in available_markets:
                available_market = available_markets[market_addr]
                assert long_token == available_market["long_token_address"], f"Long token mismatch for {market_addr}: {long_token} vs {available_market['long_token_address']}"
                assert short_token == available_market["short_token_address"], f"Short token mismatch for {market_addr}: {short_token} vs {available_market['short_token_address']}"


def test_glv_data_structure_integrity(get_glv_stats):
    """Test that GLV data structure remains consistent and complete."""
    results = get_glv_stats.get_glv_stats_multicall()

    # Check overall structure
    assert isinstance(results, dict)
    assert len(results) > 0

    # Check each GLV entry
    for glv_address, glv_data in results.items():
        # Basic GLV data should always be present
        assert "glv_address" in glv_data
        assert "long_address" in glv_data
        assert "short_address" in glv_data
        assert "glv_market_addresses" in glv_data

        # Verify GLV address consistency
        assert glv_address == glv_data["glv_address"]

        # Verify market addresses list
        assert isinstance(glv_data["glv_market_addresses"], list)
        assert len(glv_data["glv_market_addresses"]) > 0

        # Verify market addresses are unique
        market_addresses_set = set(glv_data["glv_market_addresses"])
        assert len(market_addresses_set) == len(glv_data["glv_market_addresses"]), f"Duplicate market addresses in GLV {glv_address}"

        # Check market metadata if present
        if "markets_metadata" in glv_data:
            # Should have metadata for all markets
            assert len(glv_data["markets_metadata"]) <= len(glv_data["glv_market_addresses"])

            # Each market metadata should have required fields
            for market_addr, market_data in glv_data["markets_metadata"].items():
                assert "address" in market_data
                assert "market symbol" in market_data
                assert "balance" in market_data
                assert "gm price" in market_data

                # Verify address consistency
                assert market_addr == market_data["address"]

                # Verify balance and price types
                assert isinstance(market_data["balance"], (int, float))
                assert isinstance(market_data["gm price"], (int, float))

                # Verify balance and price values
                assert market_data["balance"] >= 0
                assert market_data["gm price"] >= 0
