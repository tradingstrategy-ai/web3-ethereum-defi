"""
Integration tests for price sanity checks in CCXT exchange.

This test suite makes real API calls to verify price sanity check integration
with the CCXT exchange wrapper.
"""

import pytest

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.price_sanity import (
    PriceSanityAction,
    PriceSanityCheckConfig,
    PriceSanityException,
)
from eth_defi.provider.multi_provider import create_multi_provider_web3


def test_fetch_ticker_with_price_sanity_default(chain_rpc_url):
    """Test fetch_ticker includes price sanity check with default config.

    Makes real API calls to GMX on Arbitrum.
    """
    # Create GMX exchange with default price sanity config
    web3 = create_multi_provider_web3(chain_rpc_url)
    config = GMXConfig(web3)

    # Initialize exchange (uses default PriceSanityCheckConfig)
    gmx = GMX(config=config)
    gmx.load_markets()

    # Fetch ticker for ETH/USDC:USDC
    ticker = gmx.fetch_ticker("ETH/USDC:USDC")

    # Verify ticker structure
    assert ticker is not None
    assert "last" in ticker
    assert "info" in ticker

    # Verify price sanity check was performed
    assert "price_sanity_check" in ticker["info"]

    sanity_check = ticker["info"]["price_sanity_check"]
    assert "passed" in sanity_check
    assert "deviation_percent" in sanity_check
    assert "oracle_price_usd" in sanity_check
    assert "ticker_price_usd" in sanity_check
    assert "action_taken" in sanity_check

    # Verify prices are reasonable
    assert sanity_check["oracle_price_usd"] > 0
    assert sanity_check["ticker_price_usd"] > 0

    print(f"ETH/USDC:USDC - Oracle: ${sanity_check['oracle_price_usd']:.2f}, Ticker: ${sanity_check['ticker_price_usd']:.2f}, Deviation: {sanity_check['deviation_percent']:.4%}")


def test_fetch_ticker_with_custom_sanity_config(chain_rpc_url):
    """Test fetch_ticker with custom price sanity configuration.

    Makes real API calls with custom threshold.
    """
    web3 = create_multi_provider_web3(chain_rpc_url)
    config = GMXConfig(web3)

    # Create custom sanity config with strict threshold
    sanity_config = PriceSanityCheckConfig(
        enabled=True,
        threshold_percent=0.01,  # 1%
        action=PriceSanityAction.use_oracle_warn,
    )

    gmx = GMX(config=config, price_sanity_config=sanity_config)
    gmx.load_markets()

    ticker = gmx.fetch_ticker("ETH/USDC:USDC")

    assert ticker is not None
    assert "price_sanity_check" in ticker["info"]

    sanity_check = ticker["info"]["price_sanity_check"]

    # Even if check fails, should still return ticker (with oracle price if action is use_oracle_warn)
    assert ticker["last"] > 0


def test_fetch_ticker_disabled_sanity_check(chain_rpc_url):
    """Test fetch_ticker with disabled price sanity check.

    Makes real API calls but sanity check should be bypassed.
    """
    web3 = create_multi_provider_web3(chain_rpc_url)
    config = GMXConfig(web3)

    # Disable sanity checks
    sanity_config = PriceSanityCheckConfig(enabled=False)

    gmx = GMX(config=config, price_sanity_config=sanity_config)
    gmx.load_markets()

    ticker = gmx.fetch_ticker("ETH/USDC:USDC")

    assert ticker is not None
    assert ticker["last"] > 0

    # When disabled, price_sanity_check should not be in info
    # (or if present, should indicate it was skipped)
    if "price_sanity_check" in ticker["info"]:
        # If the check ran anyway, that's a bug
        pytest.fail("Price sanity check should not run when disabled")


@pytest.mark.skip(reason="Avik: marked for a fix")
def test_fetch_ticker_raise_exception_action(chain_rpc_url):
    """Test fetch_ticker with raise_exception action.

    Makes real API calls with extremely strict threshold to force exception.
    """
    web3 = create_multi_provider_web3(chain_rpc_url)
    config = GMXConfig(web3)

    # Use extremely strict threshold to force failure
    sanity_config = PriceSanityCheckConfig(
        enabled=True,
        threshold_percent=0.0000001,  # 0.00001%
        action=PriceSanityAction.raise_exception,
    )

    gmx = GMX(config=config, price_sanity_config=sanity_config)
    gmx.load_markets()

    # Should raise exception due to tiny threshold
    with pytest.raises(PriceSanityException) as exc_info:
        gmx.fetch_ticker("ETH/USDC:USDC")

    # Verify exception structure
    assert exc_info.value.result is not None
    assert not exc_info.value.result.passed


def test_fetch_ticker_multiple_markets(chain_rpc_url):
    """Test price sanity checks work for multiple markets.

    Makes real API calls for different markets.
    """
    web3 = create_multi_provider_web3(chain_rpc_url)
    config = GMXConfig(web3)

    gmx = GMX(config=config)
    gmx.load_markets()

    # Test multiple markets
    markets_to_test = ["ETH/USDC:USDC", "BTC/USDC:USDC"]

    for symbol in markets_to_test:
        if symbol not in gmx.markets:
            continue

        ticker = gmx.fetch_ticker(symbol)

        assert ticker is not None
        assert ticker["last"] > 0

        # All should have sanity checks
        if "price_sanity_check" in ticker["info"]:
            sanity_check = ticker["info"]["price_sanity_check"]
            assert "passed" in sanity_check
            assert "deviation_percent" in sanity_check

            print(f"{symbol} - Deviation: {sanity_check['deviation_percent']:.4%}")


def test_oracle_prices_property(chain_rpc_url):
    """Test that oracle_prices property works correctly.

    Makes real API calls to verify oracle price access.
    """
    web3 = create_multi_provider_web3(chain_rpc_url)
    config = GMXConfig(web3)

    gmx = GMX(config=config)

    # Access oracle_prices property
    oracle_prices_instance = gmx.oracle_prices

    assert oracle_prices_instance is not None

    # Get prices
    prices = oracle_prices_instance.get_recent_prices()

    assert prices is not None
    assert len(prices) > 0

    # Should be cached on second access
    oracle_prices_instance2 = gmx.oracle_prices
    assert oracle_prices_instance is oracle_prices_instance2


def test_price_sanity_use_oracle_price_on_deviation(chain_rpc_url):
    """Test that oracle price is used when deviation exceeds threshold.

    Makes real API calls and verifies price substitution.
    """
    web3 = create_multi_provider_web3(chain_rpc_url)
    config = GMXConfig(web3)

    # Use moderate threshold
    sanity_config = PriceSanityCheckConfig(
        enabled=True,
        threshold_percent=0.02,  # 2%
        action=PriceSanityAction.use_oracle_warn,
    )

    gmx = GMX(config=config, price_sanity_config=sanity_config)
    gmx.load_markets()

    ticker = gmx.fetch_ticker("ETH/USDC:USDC")

    assert ticker is not None
    assert ticker["last"] > 0

    if "price_sanity_check" in ticker["info"]:
        sanity_check = ticker["info"]["price_sanity_check"]

        # If check failed, last price should be oracle price
        if not sanity_check["passed"]:
            if sanity_check["action_taken"] == "use_oracle_warn":
                # Ticker last should be oracle price
                assert ticker["last"] == pytest.approx(sanity_check["oracle_price_usd"], rel=1e-6)
                print(f"Price deviation detected - using oracle price: ${ticker['last']:.2f}")


def test_ccxt_initialization_with_params(chain_rpc_url):
    """Test CCXT-style initialization with price sanity config.

    Makes real API calls using params dict initialization.
    """
    # CCXT-style initialization
    gmx = GMX(
        params={
            "rpcUrl": chain_rpc_url,
            "chainId": 42161,  # Arbitrum
        }
    )

    # Should have default price sanity config
    assert gmx._price_sanity_config is not None
    assert gmx._price_sanity_config.enabled

    gmx.load_markets()
    ticker = gmx.fetch_ticker("ETH/USDC:USDC")

    assert ticker is not None
    assert ticker["last"] > 0
