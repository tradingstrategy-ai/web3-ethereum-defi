"""
Tests for GMX GLV Stats Data Retrieval Module.
"""

import logging
import math
from decimal import Decimal

from collections import defaultdict

import pytest
from flaky import flaky

from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.gmx.core.glv_stats import GlvStats
from eth_defi.token import fetch_erc20_details

#: Dust-supply MET market that exposed invalid GM price ceilings on 2026-10-22.
MET_MARKET = "0xbA30e198CFFeBEf0A84D6943cC8B2e356E324112"


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
                assert math.isfinite(market_data["gm price"])

    # Check that we have some GLVs
    assert len(results) > 0, "No GLVs found"

    # Check that GLV price is present for at least one GLV (but don't fail if none are available)
    glv_with_price = [g for g in results.values() if "glv_price" in g and g["glv_price"] > 0]
    if len(glv_with_price) == 0:
        logging.warning("No GLVs with price data available - this may be due to temporary network issues")


def test_glv_price_calculation(get_glv_stats: GlvStats) -> None:
    """Check finite GLV prices and balance-weighted composition values.

    GM and GLV share supplies are independent. A dust GM supply can produce
    an enormous per-token price while its actual GLV holding has modest value.

    :param get_glv_stats: Live GLV statistics provider.
    :return: ``None`` after checking non-empty valuation coverage.
    """
    results = get_glv_stats.get_glv_stats_multicall()

    glv_with_price = [g for g in results.values() if "glv_price" in g and g["glv_price"] > 0]
    assert glv_with_price, "No GLVs with valid price data available"

    # Check price for each GLV with price data
    for glv_data in glv_with_price:
        glv_price = glv_data["glv_price"]

        # GLV price should be positive
        assert glv_price > 0, f"GLV price should be positive, got {glv_price}"

        assert math.isfinite(glv_price)

        # Check market composition
        if "markets_metadata" in glv_data:
            total_value = 0
            assert glv_data["markets_metadata"], "No market composition available"
            for market_data in glv_data["markets_metadata"].values():
                balance = market_data["balance"]
                gm_price = market_data["gm price"]
                assert math.isfinite(balance) and balance >= 0
                assert math.isfinite(gm_price)
                market_value = balance * gm_price
                assert math.isfinite(market_value)
                total_value += market_value

            # Total value should be positive
            assert total_value > 0, f"Total market value should be positive for GLV {glv_data['glv_address']}"


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


def test_glv_price_bounds(get_glv_stats: GlvStats) -> None:
    """Check finite prices without imposing arbitrary share-price bounds.

    GM reader prices are signed and depend on supply. Neither a distressed
    negative value nor a dust-supply price is a decoding failure.

    :param get_glv_stats: Live GLV statistics provider.
    :return: ``None`` after checking available prices and balances.
    """
    results = get_glv_stats.get_glv_stats_multicall()
    assert results, "No GLVs found"

    for glv_address, glv_data in results.items():
        # GLV price should be positive
        if "glv_price" in glv_data:
            glv_price = glv_data["glv_price"]
            assert glv_price > 0, f"GLV price should be positive for {glv_address}"

            assert math.isfinite(glv_price), f"Non-finite GLV price for {glv_address}"

        # Check market composition if present
        if "markets_metadata" in glv_data:
            for market_addr, market_data in glv_data["markets_metadata"].items():
                assert math.isfinite(market_data["gm price"]), f"Non-finite GM price for {market_addr}"
                assert math.isfinite(market_data["balance"]) and market_data["balance"] >= 0


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


@flaky(max_runs=3, min_passes=1)
def test_glv_market_consistency(get_glv_stats):
    """Test consistency between GLV markets and overall GMX markets.

    Flaky: ``get_available_markets()`` calls the GMX API over the network.
    Under transient outages — or when parallel pytest workers saturate the
    shared GMX API endpoint — it can return an empty dict.  When that happens
    every GLV market appears in ``missing_markets``, causing the final assertion
    ``len(missing_markets) < len(all_glv_markets)`` to evaluate as
    ``N < N → False`` even though the test logic is correct.

    ``@flaky`` retries up to 3 times; ``pytest.skip()`` is used for the
    known-bad empty-response sentinel so the retry budget is not consumed by
    a deterministically-failing assertion on bad input data.
    """
    results = get_glv_stats.get_glv_stats_multicall()
    available_markets = get_glv_stats.markets.get_available_markets()

    # Empty available_markets is a known-bad transient condition (RPC/API outage or
    # parallel-test API saturation).  Every GLV market would appear missing, making the
    # final assertion fail despite correct test logic.  Skip so @flaky can retry.
    if not available_markets:
        pytest.skip("get_available_markets() returned an empty dict — transient GMX API / RPC outage or parallel-test API saturation.  @flaky will retry up to 3 times.")

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


def test_gm_price_matches_pool_value(get_glv_stats: GlvStats) -> None:
    """Verify real GM reader prices against pool value and same-block supply.

    Check a representative market and Arbitrum's dust-supply MET market.
    The reader uses signed pool value and a one-dollar zero-supply default:
    https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/market/MarketUtils.sol.

    :param get_glv_stats: Live GLV statistics provider.
    :return: ``None`` after checking the reader formula and decoded USD price.
    """
    results = get_glv_stats.get_glv_stats_multicall()
    assert results, "No GLVs found"
    glv_address, glv_data = next(iter(results.items()))
    assert glv_data["markets_metadata"], "No market composition available"
    market_addresses = {next(iter(glv_data["markets_metadata"]))}
    if get_glv_stats.config.chain == "arbitrum":
        market_addresses.add(MET_MARKET)

    web3 = get_glv_stats.config.web3
    block = web3.eth.block_number
    for market_address in sorted(market_addresses):
        call = get_glv_stats.create_gm_price_call(glv_address, market_address)
        assert call is not None, f"No price call for {market_address}"
        result = call.call_as_result(web3, block_identifier=block)
        assert result.success, f"Price read failed for {market_address}"
        price_raw = int.from_bytes(result.result[:32], "big", signed=True)
        pool_value_raw = int.from_bytes(result.result[32:64], "big", signed=True)
        token = fetch_erc20_details(web3, market_address)
        supply_raw = token.contract.functions.totalSupply().call(block_identifier=block)
        if supply_raw == 0:
            assert price_raw == 10**30
        else:
            # Solidity signed division truncates towards zero.
            expected_raw = abs(pool_value_raw) * 10**18 // supply_raw
            if pool_value_raw < 0:
                expected_raw = -expected_raw
            assert price_raw == expected_raw

        price = get_glv_stats.extract_gm_price({f"gm_price_{market_address}": result}, market_address)
        assert math.isfinite(price)
        assert price == pytest.approx(float(Decimal(price_raw) / Decimal(10**30)))


@pytest.mark.parametrize("price_raw", [15 * 10**29, 239 * 10**49, 10**30, 0, -2 * 10**30], ids=["ordinary", "dust-supply", "zero-supply-default", "zero", "negative"])
def test_extract_gm_price_signed(price_raw: int) -> None:
    """Decode ordinary, dust-supply, zero and distressed GM prices.

    Construct signed ABI results without a provider, so unsigned decoding or
    clamping fails deterministically even when live pool conditions change.

    :param price_raw: Signed reader price with 30 decimal places.
    :return: ``None`` after checking the decoded USD value.
    """
    call = EncodedCall(address=MET_MARKET, data=b"", func_name="getMarketTokenPrice", extra_data={})
    result = EncodedCallResult(call=call, success=True, result=price_raw.to_bytes(32, "big", signed=True) + bytes(32), block_identifier=1)
    stats = GlvStats.__new__(GlvStats)
    price = stats.extract_gm_price({f"gm_price_{MET_MARKET}": result}, MET_MARKET)
    assert price == pytest.approx(float(Decimal(price_raw) / Decimal(10**30)))


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
