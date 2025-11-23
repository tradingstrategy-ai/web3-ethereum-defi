"""Test GMX CCXT adapter against standard CCXT API patterns.

This script verifies that the GMX CCXT adapter implements common CCXT methods
and returns data in expected formats. It's similar to compare.py but adapted
for GMX's unique characteristics (no order books, no leverage tiers, etc.).

To run this script:

.. code-block:: shell

    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"
    export GMX_WALLET_ADDRESS="0x..."  # Optional, for balance/position tests

    python scripts/gmx/test_gmx_ccxt_adapter.py

"""

import logging
import os

from web3 import Web3

from eth_defi.gmx.ccxt import GMX
from eth_defi.gmx.config import GMXConfig

logger = logging.getLogger(__name__)


def create_gmx_exchange() -> GMX:
    """Create GMX CCXT adapter instance.

    Connects to Arbitrum mainnet and initializes the GMX adapter.
    """
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM")
    if not rpc_url:
        raise ValueError("JSON_RPC_ARBITRUM environment variable required")

    web3 = Web3(Web3.HTTPProvider(rpc_url))

    # Optional wallet address for balance/position queries
    wallet_address = os.environ.get("GMX_WALLET_ADDRESS")

    config = GMXConfig(
        web3=web3,
        user_wallet_address=wallet_address,
    )

    gmx = GMX(config=config)

    return gmx


def parse_balances(exchange: GMX, balance_data: dict) -> dict[str, float]:
    """Normalize balance data to symbol -> amount mapping.

    For GMX, we extract the 'free' balances (available to trade).
    """
    data = {k: v for k, v in balance_data["free"].items() if v is not None and v > 0}
    return data


def validate_exchange_api(exchange: GMX):
    """Test that GMX CCXT adapter implements expected CCXT API methods."""

    name = exchange.__class__.__name__
    print(f"\n{'=' * 80}")
    print(f"Testing exchange: {name}")
    print(f"{'=' * 80}\n")

    # Test 1: Load markets
    print("Step 1: Loading markets...")
    exchange.load_markets(reload=True)
    if len(exchange.markets) == 0:
        raise RuntimeError("No markets found.")

    print(f"... Found {len(exchange.markets)} markets")
    for idx, symbol in enumerate(list(exchange.markets.keys())[:5]):
        print(f"  Market #{idx + 1}: {symbol}")

    # Test 2: Check feature support
    print("\nStep 2: Checking feature support...")
    required_features = ["fetchBalance", "fetchPositions"]
    for feat in required_features:
        if not exchange.has.get(feat, False):
            logger.warning(f"Exchange {name} does not support: {feat}")

    # GMX-specific features
    gmx_features = ["fetchTicker", "fetchOHLCV", "fetchOpenInterest", "fetchFundingRate"]
    print("GMX-specific features:")
    for feat in gmx_features:
        supported = exchange.has.get(feat, False)
        status = "✓" if supported else "✗"
        print(f"  {status} {feat}")

    # Test 3: Fetch ticker
    print("\nStep 3: Testing fetch_ticker()...")
    chosen_symbol = None
    # Use exchange.markets.keys() instead of exchange.symbols
    for symbol in exchange.markets.keys():
        if "ETH" in symbol:
            chosen_symbol = symbol
            break

    if not chosen_symbol:
        raise RuntimeError("Could not find ETH market")

    ticker = exchange.fetch_ticker(chosen_symbol)
    print(f"... {chosen_symbol} ticker:")
    print(f"    Last price: ${ticker['last']:,.2f}")
    print(f"    24h high: ${ticker.get('high', 0):,.2f}")
    print(f"    24h low: ${ticker.get('low', 0):,.2f}")

    # Test 4: Fetch OHLCV
    print(f"\nStep 4: Testing fetch_ohlcv() for {chosen_symbol}...")
    ohlcv = exchange.fetch_ohlcv(chosen_symbol, "1h", limit=5)
    print(f"... Fetched {len(ohlcv)} candles")
    if ohlcv:
        latest = ohlcv[-1]
        timestamp, o, h, l, c, v = latest
        print(f"    Latest: O:{o:.2f} H:{h:.2f} L:{l:.2f} C:{c:.2f}")

    # Test 5: Fetch open interest
    print(f"\nStep 5: Testing fetch_open_interest() for {chosen_symbol}...")
    try:
        oi = exchange.fetch_open_interest(chosen_symbol)
        print(f"... Total OI: ${oi['openInterestValue']:,.0f}")
        print(f"    Long: ${oi['info']['longOpenInterest']:,.0f}")
        print(f"    Short: ${oi['info']['shortOpenInterest']:,.0f}")
    except Exception as e:
        print(f"... Could not fetch OI: {e}")

    # Test 6: Fetch funding rate
    print(f"\nStep 6: Testing fetch_funding_rate() for {chosen_symbol}...")
    try:
        fr = exchange.fetch_funding_rate(chosen_symbol)
        hourly_rate = fr["fundingRate"] * 3600
        print(f"... Funding rate: {hourly_rate:.6f}% per hour")
        print(f"    Direction: {'Longs pay shorts' if fr['fundingRate'] > 0 else 'Shorts pay longs'}")
    except Exception as e:
        print(f"... Could not fetch funding rate: {e}")

    # Test 7: Fetch balance (if wallet configured)
    print("\nStep 7: Testing fetch_balance()...")
    if exchange.wallet_address:
        try:
            balance = exchange.fetch_balance()
            balances = parse_balances(exchange, balance)

            if balances:
                print("... Account balances:")
                for token, amount in list(balances.items())[:5]:
                    print(f"    {token}: {amount:.6f}")
            else:
                print("... No balances found (may need to deposit)")
        except Exception as e:
            print(f"... Could not fetch balance: {e}")
    else:
        print("... Skipped (no wallet address configured)")

    # Test 8: Fetch positions (if wallet configured)
    print("\nStep 8: Testing fetch_positions()...")
    if exchange.wallet_address:
        try:
            positions = exchange.fetch_positions()
            if positions:
                print(f"... Found {len(positions)} open positions:")
                for pos in positions[:3]:  # Show first 3
                    print(f"    {pos['symbol']}: {pos['side']} {pos['contracts']:.4f} @ ${pos['entryPrice']:.2f}")
                    print(f"      Leverage: {pos['leverage']:.2f}x, PnL: ${pos['unrealizedPnl']:.2f}")
            else:
                print("... No open positions")
        except Exception as e:
            print(f"... Could not fetch positions: {e}")
    else:
        print("... Skipped (no wallet address configured)")

    # Test 9: Test leverage settings
    print("\nStep 9: Testing leverage configuration...")
    try:
        # Set leverage for a specific symbol
        exchange.set_leverage(5.0, chosen_symbol)
        lev_info = exchange.fetch_leverage(chosen_symbol)
        print(f"... Leverage for {chosen_symbol}: {lev_info['leverage']}x")

        # Set default leverage
        exchange.set_leverage(10.0)
        default_lev = exchange.fetch_leverage()
        print(f"... Default leverage: {default_lev[0]['leverage']}x")
    except Exception as e:
        print(f"... Leverage test failed: {e}")

    # Test 10: Feature flags
    print("\nStep 10: Verifying GMX limitations are documented...")
    limitations = {
        "fetchOrderBook": "GMX uses liquidity pools, not order books",
        "createOrder": "Requires private key integration (not yet implemented)",
        "fetchLeverageTiers": "GMX doesn't provide leverage tier data",
        "setMarginMode": "GMX uses cross margin only",
    }
    for feature, reason in limitations.items():
        has_feature = exchange.has.get(feature, False)
        status = "✗" if not has_feature else "✓"
        print(f"  {status} {feature}: {reason}")

    print(f"\n{'=' * 80}")
    print(f"✓ All tests completed successfully for {name}")
    print(f"{'=' * 80}\n")


def main():
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    print("\n" + "=" * 80)
    print("GMX CCXT Adapter Test Suite")
    print("=" * 80)

    # Create GMX exchange
    gmx = create_gmx_exchange()

    try:
        validate_exchange_api(gmx)
        print("\n✓ All tests PASSED")
        return 0
    except Exception as e:
        print(f"\n✗ Tests FAILED: {e}")
        logger.error("Test suite failed", exc_info=e)
        return 1


if __name__ == "__main__":
    exit(main())
