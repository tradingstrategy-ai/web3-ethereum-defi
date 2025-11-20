"""
GMX CCXT Position Management Examples

Examples of CCXT-compatible position management methods:
- fetch_positions() - Detailed position information with metrics
- set_leverage() - Configure leverage settings
- fetch_leverage() - Query leverage configuration

Usage:
    export WALLET_ADDRESS="0xYourAddress"
    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"
    python scripts/gmx/gmx_ccxt_positions.py
"""

import os
from web3 import Web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.ccxt import GMX


def example_fetch_positions(gmx: GMX):
    """Example: fetch_positions() - Detailed position information"""
    print("\n" + "=" * 60)
    print("1. fetch_positions() - Detailed Position Information")
    print("=" * 60)

    try:
        positions = gmx.fetch_positions()

        if not positions:
            print("\nNo open positions found")
            return

        print(f"\nShowing {min(len(positions), 10)} of {len(positions)} positions:\n")
        for pos in positions[:10]:
            symbol = pos["symbol"]
            side = pos["side"].upper()
            contracts = f"{pos['contracts']:.4f}" if pos["contracts"] else "N/A"
            entry_price = f"${pos['entryPrice']:,.2f}" if pos["entryPrice"] else "N/A"
            mark_price = f"${pos['markPrice']:,.2f}" if pos["markPrice"] else "N/A"
            leverage = f"{pos['leverage']:.2f}x" if pos["leverage"] else "N/A"

            # PnL
            unrealized_pnl = pos.get("unrealizedPnl")
            percentage = pos.get("percentage")
            if unrealized_pnl is not None and percentage is not None:
                pnl_str = f"${unrealized_pnl:,.2f} ({percentage:.2f}%)"
            else:
                pnl_str = "N/A"

            liq_price = f"${pos['liquidationPrice']:,.2f}" if pos["liquidationPrice"] else "N/A"

            print(f"Symbol: {symbol}")
            print(f"Side: {side}")
            print(f"Size: {contracts}")
            print(f"Entry Price: {entry_price}")
            print(f"Mark Price: {mark_price}")
            print(f"Leverage: {leverage}")
            print(f"PnL: {pnl_str}")
            print(f"Liquidation Price: {liq_price}")
            print()

        # Example: filtering by symbols
        if len(positions) > 0:
            first_symbol = positions[0]["symbol"]
            filtered = gmx.fetch_positions(symbols=[first_symbol])
            print(f"Filtered to {first_symbol}: {len(filtered)} position(s)")

            # Show detailed metrics for first position
            if filtered:
                pos = filtered[0]
                print(f"\nDetailed metrics for {first_symbol}:")
                print(f"  Contracts: {pos['contracts']:.6f}" if pos["contracts"] else "  Contracts: N/A")
                print(f"  Notional: ${pos['notional']:,.2f}" if pos["notional"] else "  Notional: N/A")
                print(f"  Collateral: ${pos['collateral']:,.2f}" if pos["collateral"] else "  Collateral: N/A")
                print(f"  Initial Margin: ${pos['initialMargin']:,.2f}" if pos["initialMargin"] else "  Initial Margin: N/A")
                print(f"  Maintenance Margin: ${pos['maintenanceMargin']:,.2f}" if pos["maintenanceMargin"] else "  Maintenance Margin: N/A")
                print(f"  Margin Ratio: {pos['marginRatio']:.4f}" if pos["marginRatio"] else "  Margin Ratio: N/A")

    except ValueError as e:
        print(f"Skipped: {e}")
    except Exception as e:
        print(f"Error: {e}")


def example_set_leverage(gmx: GMX):
    """Example: set_leverage() - Configure leverage settings"""
    print("\n" + "=" * 60)
    print("2. set_leverage() - Configure Leverage Settings")
    print("=" * 60)

    try:
        # Set leverage for specific symbol
        print("\nSetting leverage for ETH/USD to 5x:")
        result = gmx.set_leverage(5.0, "ETH/USD")
        print(f"  {result['info']['message']}")

        # Set leverage for another symbol
        print("\nSetting leverage for BTC/USD to 10x:")
        result = gmx.set_leverage(10.0, "BTC/USD")
        print(f"  {result['info']['message']}")

        # Set default leverage
        print("\nSetting default leverage to 3x:")
        result = gmx.set_leverage(3.0)
        print(f"  {result['info']['message']}")

        print("\nNote: Leverage settings are stored locally for future order creation")

    except ValueError as e:
        print(f"Skipped: {e}")
    except Exception as e:
        print(f"Error: {e}")


def example_fetch_leverage(gmx: GMX):
    """Example: fetch_leverage() - Query leverage configuration"""
    print("\n" + "=" * 60)
    print("3. fetch_leverage() - Query Leverage Configuration")
    print("=" * 60)

    try:
        # Get leverage for specific symbol
        print("\nGetting leverage for ETH/USD:")
        leverage_info = gmx.fetch_leverage("ETH/USD")
        print(f"  ETH/USD leverage: {leverage_info['leverage']}x")

        print("\nGetting leverage for BTC/USD:")
        leverage_info = gmx.fetch_leverage("BTC/USD")
        print(f"  BTC/USD leverage: {leverage_info['leverage']}x")

        # Get all leverage settings
        print("\nAll leverage settings:")
        all_leverage = gmx.fetch_leverage()
        for lev in all_leverage:
            symbol = lev["symbol"]
            leverage = lev["leverage"]
            print(f"  {symbol:12} {leverage:.1f}x")

    except ValueError as e:
        print(f"Skipped: {e}")
    except Exception as e:
        print(f"Error: {e}")


def main():
    print("\n" + "=" * 60)
    print("GMX CCXT Position Management Examples")
    print("=" * 60)

    # Get wallet address from environment or use default test address
    wallet_address = os.environ.get("WALLET_ADDRESS", "0x91666112b851E33D894288A95846d14781e86cad")

    # Initialize GMX CCXT wrapper
    rpc = os.environ.get("JSON_RPC_ARBITRUM", "https://arb1.arbitrum.io/rpc")

    print(f"\nUsing wallet address: {wallet_address}")

    try:
        web3 = Web3(Web3.HTTPProvider(rpc))
        config = GMXConfig(web3, user_wallet_address=wallet_address)
        gmx = GMX(config)

        print(f"Chain ID: {web3.eth.chain_id}")
        print("Connected successfully")

        # Run all examples
        example_fetch_positions(gmx)
        example_set_leverage(gmx)
        example_fetch_leverage(gmx)

        print("\n" + "=" * 60)
        print("All position management methods executed successfully!")
        print("=" * 60 + "\n")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
