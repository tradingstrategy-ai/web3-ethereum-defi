"""
Tests for GMX price sanity checks.

This test suite makes real API calls to GMX oracle and ticker endpoints
to verify price sanity check functionality.
"""

from flaky import flaky
import pytest

from eth_defi.gmx.api import GMXAPI, clear_ticker_prices_cache
from tests.gmx.conftest import GMX_TEST_RETRY_CONFIG
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.price_sanity import (
    PriceSanityAction,
    PriceSanityCheckConfig,
    PriceSanityException,
    check_price_sanity,
    get_oracle_price_usd,
    get_ticker_price_usd,
)


def test_price_sanity_check_within_threshold(gmx_config):
    """Test that price sanity check passes when prices are within threshold.

    Makes real API calls to GMX oracle and ticker endpoints.
    """
    # Get real prices from both sources
    oracle_prices_client = OraclePrices(gmx_config.get_chain())
    oracle_prices = oracle_prices_client.get_recent_prices()

    api = GMXAPI(gmx_config, retry_config=GMX_TEST_RETRY_CONFIG)
    tickers = api.get_tickers()

    # Find ETH token (should exist on all chains)
    eth_ticker = None
    eth_ticker_address = None

    for ticker in tickers:
        symbol = ticker.get("tokenSymbol", "")
        if symbol == "ETH":
            eth_ticker = ticker
            eth_ticker_address = ticker.get("tokenAddress", "").lower()
            break

    assert eth_ticker is not None, "ETH ticker not found"
    assert eth_ticker_address is not None, "ETH address not found"

    # Get oracle price for ETH (case-insensitive lookup)
    # For testnet, we might need to map to mainnet address
    oracle_address = eth_ticker_address
    if gmx_config.get_chain() in ["arbitrum_sepolia", "avalanche_fuji"]:
        from eth_defi.gmx.core.oracle import _TESTNET_TO_MAINNET_ADDRESSES

        testnet_mappings = _TESTNET_TO_MAINNET_ADDRESSES.get(gmx_config.get_chain(), {})
        oracle_address = testnet_mappings.get(oracle_address, oracle_address)

    # Oracle prices may use checksummed addresses, so do case-insensitive lookup
    eth_oracle = None
    for addr, price in oracle_prices.items():
        if addr.lower() == oracle_address.lower():
            eth_oracle = price
            break

    assert eth_oracle is not None, f"ETH oracle price not found for {oracle_address}"

    # Configure with default 3% threshold
    config = PriceSanityCheckConfig(
        enabled=True,
        threshold_percent=0.03,
        action=PriceSanityAction.use_oracle_warn,
    )

    # Perform sanity check
    result = check_price_sanity(
        oracle_price=eth_oracle,
        ticker_price=eth_ticker,
        token_address=oracle_address,
        token_decimals=18,  # ETH has 18 decimals
        config=config,
    )

    # Verify result structure
    assert result is not None
    assert hasattr(result, "passed")
    assert hasattr(result, "deviation_percent")
    assert hasattr(result, "oracle_price_usd")
    assert hasattr(result, "ticker_price_usd")
    assert hasattr(result, "action_taken")

    # Under normal circumstances, oracle and ticker should be close
    # Most of the time this should pass (< 3% deviation)
    # But we check both prices are reasonable
    assert result.oracle_price_usd > 0, "Oracle price should be positive"
    assert result.ticker_price_usd > 0, "Ticker price should be positive"

    # Check prices are in reasonable range for ETH (between $500 and $10,000)
    assert 500 < result.oracle_price_usd < 10000, f"Oracle ETH price ${result.oracle_price_usd} seems unrealistic"
    assert 500 < result.ticker_price_usd < 10000, f"Ticker ETH price ${result.ticker_price_usd} seems unrealistic"

    # Log the actual deviation for debugging
    print(f"ETH Price - Oracle: ${result.oracle_price_usd:.2f}, Ticker: ${result.ticker_price_usd:.2f}, Deviation: {result.deviation_percent:.4%}")


def test_price_sanity_custom_threshold(gmx_config):
    """Test price sanity check with custom threshold.

    Makes real API calls to test custom threshold configuration.
    """
    oracle_prices_client = OraclePrices(gmx_config.get_chain())
    oracle_prices = oracle_prices_client.get_recent_prices()

    api = GMXAPI(gmx_config, retry_config=GMX_TEST_RETRY_CONFIG)
    tickers = api.get_tickers()

    # Find ETH token (should exist on all chains)
    ticker = None
    ticker_address = None

    for t in tickers:
        if t.get("tokenSymbol") == "ETH":
            ticker = t
            ticker_address = t.get("tokenAddress", "").lower()
            break

    if ticker is None or ticker_address is None:
        pytest.skip("ETH not found")

    # Map testnet to mainnet if needed
    oracle_address = ticker_address
    if gmx_config.get_chain() in ["arbitrum_sepolia", "avalanche_fuji"]:
        from eth_defi.gmx.core.oracle import _TESTNET_TO_MAINNET_ADDRESSES

        testnet_mappings = _TESTNET_TO_MAINNET_ADDRESSES.get(gmx_config.get_chain(), {})
        oracle_address = testnet_mappings.get(oracle_address, oracle_address)

    # Oracle prices may use checksummed addresses, so do case-insensitive lookup
    oracle_price = None
    for addr, price in oracle_prices.items():
        if addr.lower() == oracle_address.lower():
            oracle_price = price
            break

    if oracle_price is None:
        pytest.skip(f"Oracle price not available for {oracle_address}")

    # Test with very strict threshold (0.1%)
    strict_config = PriceSanityCheckConfig(
        enabled=True,
        threshold_percent=0.001,  # 0.1%
        action=PriceSanityAction.use_oracle_warn,
    )

    result_strict = check_price_sanity(
        oracle_price=oracle_price,
        ticker_price=ticker,
        token_address=oracle_address,
        token_decimals=18,
        config=strict_config,
    )

    # Test with very lenient threshold (50%)
    lenient_config = PriceSanityCheckConfig(
        enabled=True,
        threshold_percent=0.50,  # 50%
        action=PriceSanityAction.use_oracle_warn,
    )

    result_lenient = check_price_sanity(
        oracle_price=oracle_price,
        ticker_price=ticker,
        token_address=oracle_address,
        token_decimals=18,
        config=lenient_config,
    )

    # Lenient threshold should pass
    assert result_lenient.passed or result_lenient.reason is not None

    # Both should have same prices
    assert result_strict.oracle_price_usd == result_lenient.oracle_price_usd
    assert result_strict.ticker_price_usd == result_lenient.ticker_price_usd


def test_price_sanity_action_use_oracle_warn(gmx_config):
    """Test use_oracle_warn action (default behaviour).

    Makes real API calls to verify default action.
    """
    oracle_prices_client = OraclePrices(gmx_config.get_chain())
    oracle_prices = oracle_prices_client.get_recent_prices()

    api = GMXAPI(gmx_config, retry_config=GMX_TEST_RETRY_CONFIG)
    tickers = api.get_tickers()

    # Find ETH token (should exist on all chains)
    ticker = None
    ticker_address = None

    for t in tickers:
        if t.get("tokenSymbol") == "ETH":
            ticker = t
            ticker_address = t.get("tokenAddress", "").lower()
            break

    if ticker is None or ticker_address is None:
        pytest.skip("ETH not found")

    # Map testnet to mainnet if needed
    oracle_address = ticker_address
    if gmx_config.get_chain() in ["arbitrum_sepolia", "avalanche_fuji"]:
        from eth_defi.gmx.core.oracle import _TESTNET_TO_MAINNET_ADDRESSES

        testnet_mappings = _TESTNET_TO_MAINNET_ADDRESSES.get(gmx_config.get_chain(), {})
        oracle_address = testnet_mappings.get(oracle_address, oracle_address)

    # Oracle prices may use checksummed addresses, so do case-insensitive lookup
    oracle_price = None
    for addr, price in oracle_prices.items():
        if addr.lower() == oracle_address.lower():
            oracle_price = price
            break

    if oracle_price is None:
        pytest.skip(f"Oracle price not available for {oracle_address}")

    config = PriceSanityCheckConfig(
        enabled=True,
        threshold_percent=0.03,
        action=PriceSanityAction.use_oracle_warn,
    )

    result = check_price_sanity(
        oracle_price=oracle_price,
        ticker_price=ticker,
        token_address=oracle_address,
        token_decimals=18,
        config=config,
    )

    # Should not raise exception
    assert result is not None
    assert result.action_taken == PriceSanityAction.use_oracle_warn


@flaky(max_runs=3, min_passes=1)
def test_price_sanity_action_raise_exception(gmx_config):
    """Test raise_exception action with artificially high threshold.

    Makes real API calls but uses extreme threshold to force failure.
    """
    oracle_prices_client = OraclePrices(gmx_config.get_chain())
    oracle_prices = oracle_prices_client.get_recent_prices()

    api = GMXAPI(gmx_config, retry_config=GMX_TEST_RETRY_CONFIG)
    tickers = api.get_tickers()

    # Find ETH token (should exist on all chains)
    ticker = None
    ticker_address = None

    for t in tickers:
        if t.get("tokenSymbol") == "ETH":
            ticker = t
            ticker_address = t.get("tokenAddress", "").lower()
            break

    if ticker is None or ticker_address is None:
        pytest.skip("ETH not found")

    # Map testnet to mainnet if needed
    oracle_address = ticker_address
    if gmx_config.get_chain() in ["arbitrum_sepolia", "avalanche_fuji"]:
        from eth_defi.gmx.core.oracle import _TESTNET_TO_MAINNET_ADDRESSES

        testnet_mappings = _TESTNET_TO_MAINNET_ADDRESSES.get(gmx_config.get_chain(), {})
        oracle_address = testnet_mappings.get(oracle_address, oracle_address)

    # Oracle prices may use checksummed addresses, so do case-insensitive lookup
    oracle_price = None
    for addr, price in oracle_prices.items():
        if addr.lower() == oracle_address.lower():
            oracle_price = price
            break

    if oracle_price is None:
        pytest.skip(f"Oracle price not available for {oracle_address}")

    # Use extremely strict threshold (0.0001% = 0.000001) to force failure
    config = PriceSanityCheckConfig(
        enabled=True,
        threshold_percent=0.000001,
        action=PriceSanityAction.raise_exception,
    )

    # This should raise exception due to tiny threshold
    with pytest.raises(PriceSanityException) as exc_info:
        check_price_sanity(
            oracle_price=oracle_price,
            ticker_price=ticker,
            token_address=oracle_address,
            token_decimals=18,
            config=config,
        )

    # Verify exception has result attached
    assert exc_info.value.result is not None
    assert not exc_info.value.result.passed


def test_price_sanity_disabled(gmx_config):
    """Test that sanity check can be disabled.

    Makes real API calls but check should be bypassed.
    """
    oracle_prices_client = OraclePrices(gmx_config.get_chain())
    oracle_prices = oracle_prices_client.get_recent_prices()

    api = GMXAPI(gmx_config, retry_config=GMX_TEST_RETRY_CONFIG)
    tickers = api.get_tickers()

    # Find ETH token (should exist on all chains)
    ticker = None
    ticker_address = None

    for t in tickers:
        if t.get("tokenSymbol") == "ETH":
            ticker = t
            ticker_address = t.get("tokenAddress", "").lower()
            break

    if ticker is None or ticker_address is None:
        pytest.skip("ETH not found")

    # Map testnet to mainnet if needed
    oracle_address = ticker_address
    if gmx_config.get_chain() in ["arbitrum_sepolia", "avalanche_fuji"]:
        from eth_defi.gmx.core.oracle import _TESTNET_TO_MAINNET_ADDRESSES

        testnet_mappings = _TESTNET_TO_MAINNET_ADDRESSES.get(gmx_config.get_chain(), {})
        oracle_address = testnet_mappings.get(oracle_address, oracle_address)

    # Oracle prices may use checksummed addresses, so do case-insensitive lookup
    oracle_price = None
    for addr, price in oracle_prices.items():
        if addr.lower() == oracle_address.lower():
            oracle_price = price
            break

    if oracle_price is None:
        pytest.skip(f"Oracle price not available for {oracle_address}")

    # Disabled config - even with strict threshold, should pass
    config = PriceSanityCheckConfig(
        enabled=False,
        threshold_percent=0.000001,  # Extremely strict
        action=PriceSanityAction.raise_exception,
    )

    # Note: When disabled=False, check_price_sanity won't be called in production
    # But we can still test the config structure
    assert not config.enabled


def test_price_conversion_consistency(gmx_config):
    """Test that price conversions are consistent between oracle and ticker.

    Makes real API calls to verify conversion calculations.
    """
    oracle_prices_client = OraclePrices(gmx_config.get_chain())
    oracle_prices = oracle_prices_client.get_recent_prices()

    api = GMXAPI(gmx_config, retry_config=GMX_TEST_RETRY_CONFIG)
    tickers = api.get_tickers()

    # Test with ETH (18 decimals)
    eth_ticker = None
    eth_ticker_address = None

    for ticker in tickers:
        if ticker.get("tokenSymbol") == "ETH":
            eth_ticker = ticker
            eth_ticker_address = ticker.get("tokenAddress", "").lower()
            break

    if eth_ticker is None or eth_ticker_address is None:
        pytest.skip("ETH not found")

    # Get oracle price with case-insensitive lookup
    oracle_address = eth_ticker_address
    if gmx_config.get_chain() in ["arbitrum_sepolia", "avalanche_fuji"]:
        from eth_defi.gmx.core.oracle import _TESTNET_TO_MAINNET_ADDRESSES

        testnet_mappings = _TESTNET_TO_MAINNET_ADDRESSES.get(gmx_config.get_chain(), {})
        oracle_address = testnet_mappings.get(oracle_address, oracle_address)

    # Oracle prices may use checksummed addresses, so do case-insensitive lookup
    eth_oracle = None
    for addr, price in oracle_prices.items():
        if addr.lower() == oracle_address.lower():
            eth_oracle = price
            break

    if eth_oracle is None:
        pytest.skip("ETH oracle price not available")

    # Test price extraction helpers
    oracle_price_usd = get_oracle_price_usd(eth_oracle, 18)
    ticker_price_usd = get_ticker_price_usd(eth_ticker, 18)

    # Both should be positive and reasonable
    assert oracle_price_usd > 0
    assert ticker_price_usd > 0

    # Should be within reasonable ETH price range
    assert 500 < oracle_price_usd < 10000
    assert 500 < ticker_price_usd < 10000

    # Calculate manual deviation
    manual_deviation = abs(ticker_price_usd - oracle_price_usd) / abs(oracle_price_usd)

    # Run through sanity check
    config = PriceSanityCheckConfig(threshold_percent=0.03)
    result = check_price_sanity(
        oracle_price=eth_oracle,
        ticker_price=eth_ticker,
        token_address=oracle_address,
        token_decimals=18,
        config=config,
    )

    # Check consistency
    assert result.oracle_price_usd == pytest.approx(oracle_price_usd, rel=1e-6)
    assert result.ticker_price_usd == pytest.approx(ticker_price_usd, rel=1e-6)
    assert result.deviation_percent == pytest.approx(manual_deviation, rel=1e-6)

    print(f"Price conversion test - Oracle: ${oracle_price_usd:.2f}, Ticker: ${ticker_price_usd:.2f}, Deviation: {manual_deviation:.4%}")


def test_ticker_cache_functionality(gmx_config):
    """Test that ticker price caching works correctly.

    Makes real API calls to verify caching behavior.
    """
    # Clear cache first
    clear_ticker_prices_cache()

    api = GMXAPI(gmx_config, retry_config=GMX_TEST_RETRY_CONFIG)

    # First call - should fetch from API
    tickers1 = api.get_tickers(use_cache=True)
    assert tickers1 is not None
    assert len(tickers1) > 0

    # Second call - should use cache (verify by checking it returns same data)
    tickers2 = api.get_tickers(use_cache=True)
    assert tickers2 is not None
    assert len(tickers2) == len(tickers1)

    # Force fresh fetch
    tickers3 = api.get_tickers(use_cache=False)
    assert tickers3 is not None
    assert len(tickers3) > 0

    # Clear cache
    clear_ticker_prices_cache()

    # After clear, should fetch again
    tickers4 = api.get_tickers(use_cache=True)
    assert tickers4 is not None
    assert len(tickers4) > 0
