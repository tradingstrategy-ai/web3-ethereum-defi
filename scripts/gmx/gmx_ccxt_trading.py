"""
GMX CCXT Trading Examples

Examples of CCXT-compatible trading/account methods:
- fetch_balance() - Account token balances
- fetch_open_orders() - Open positions as orders
- fetch_my_trades() - User trade history

Usage:
    export WALLET_ADDRESS="0xYourAddress"
    export JSON_RPC_ARBITRUM="https://arb1.arbitrum.io/rpc"
    python scripts/gmx/gmx_ccxt_trading.py
"""

import os
from datetime import datetime, timedelta
from web3 import Web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.ccxt import GMX


def example_fetch_balance(gmx: GMX):
    """Example: fetch_balance() - Account token balances"""
    print("\n" + "=" * 60)
    print("1. fetch_balance() - Account Token Balances")
    print("=" * 60)

    try:
        balance = gmx.fetch_balance()

        # Show tokens with non-zero balance
        shown = 0
        print("\nToken balances:\n")
        for currency in sorted(balance.keys()):
            if currency in ["free", "used", "total", "info"]:
                continue

            amounts = balance[currency]
            total = amounts.get("total", 0)

            if total > 0:
                free = amounts.get("free", 0)
                used = amounts.get("used", 0)
                print(f"{currency:8}  Free: {free:>12.6f}  Used: {used:>12.6f}  Total: {total:>12.6f}")
                shown += 1

                if shown >= 10:
                    break

        if shown == 0:
            print("No token balances found (or all balances are 0)")
        else:
            print(f"\nShowing {shown} tokens with balance")

    except ValueError as e:
        print(f"Skipped: {e}")
    except Exception as e:
        print(f"Error: {e}")


def example_fetch_open_orders(gmx: GMX):
    """Example: fetch_open_orders() - Open positions as orders"""
    print("\n" + "=" * 60)
    print("2. fetch_open_orders() - Open Positions")
    print("=" * 60)

    try:
        orders = gmx.fetch_open_orders()

        if not orders:
            print("\nNo open positions found")
            return

        print(f"\nShowing {min(len(orders), 10)} of {len(orders)} positions:\n")
        for order in orders[:10]:
            order_id = order["id"][:15] + "..." if len(order["id"]) > 15 else order["id"]
            symbol = order["symbol"]
            side = order["side"].upper()
            amount = f"{order['amount']:.4f}" if order["amount"] else "N/A"
            price = f"${order['price']:,.2f}" if order["price"] else "N/A"
            cost = f"${order['cost']:,.2f}" if order["cost"] else "N/A"
            status = order["status"]

            print(f"{symbol:12} {side:5}  Amount: {amount:>10}  Price: {price:>12}  Cost: {cost:>15}  Status: {status}")

        # Example: filtering by symbol
        if len(orders) > 0:
            first_symbol = orders[0]["symbol"]
            filtered = gmx.fetch_open_orders(symbol=first_symbol)
            print(f"\nFiltered to {first_symbol}: {len(filtered)} positions")

    except ValueError as e:
        print(f"Skipped: {e}")
    except Exception as e:
        print(f"Error: {e}")


def example_fetch_my_trades(gmx: GMX):
    """Example: fetch_my_trades() - User trade history"""
    print("\n" + "=" * 60)
    print("3. fetch_my_trades() - User Trade History")
    print("=" * 60)

    try:
        # Get trades from last 7 days
        since = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
        trades = gmx.fetch_my_trades(since=since, limit=20)

        if not trades:
            print("\nNo trades found in the last 7 days")
            return

        print(f"\nShowing {min(len(trades), 15)} of {len(trades)} trades:\n")
        for trade in trades[:15]:
            timestamp = datetime.fromisoformat(trade["datetime"].replace("Z", "+00:00"))
            time_str = timestamp.strftime("%m-%d %H:%M")
            symbol = trade["symbol"]
            side = trade["side"].upper()
            amount = f"{trade['amount']:.4f}" if trade["amount"] else "N/A"
            price = f"${trade['price']:,.2f}" if trade["price"] else "N/A"
            cost = f"${trade['cost']:,.2f}" if trade["cost"] else "N/A"

            print(f"{time_str}  {symbol:12} {side:4}  Amount: {amount:>10}  Price: {price:>12}  Cost: {cost:>15}")

    except ValueError as e:
        print(f"Skipped: {e}")
    except Exception as e:
        print(f"Error: {e}")


def main():
    print("\n" + "=" * 60)
    print("GMX CCXT Trading Examples")
    print("Account and trading history methods")
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
        example_fetch_balance(gmx)
        example_fetch_open_orders(gmx)
        example_fetch_my_trades(gmx)

        print("\n" + "=" * 60)
        print("All trading methods executed successfully!")
        print("=" * 60 + "\n")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
