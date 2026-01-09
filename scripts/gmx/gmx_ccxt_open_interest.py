"""GMX Open Interest Example.

Demonstrates fetching open interest data using CCXT-compatible interface.
Shows both current and historical open interest for GMX markets.

CCXT standard:
- Aggregates long + short into openInterestValue
- Preserves long/short breakdown in info field
"""

from web3 import Web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.ccxt import GMX
from rich.console import Console

print = Console().print


def main():
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    gmx = GMX(config)

    gmx.load_markets()

    symbols = ["ETH/USDC:USDC", "BTC/USDC:USDC", "ARB/USDC:USDC"]

    print("GMX Open Interest Data (CCXT-compliant format)\n")
    print(f"{'Market':<8} {'Total OI':>15} {'Long OI':>15} {'Short OI':>15}")
    print("-" * 60)

    for symbol in symbols:
        try:
            oi = gmx.fetch_open_interest(symbol)

            # Standard CCXT field
            total_oi = oi["openInterestValue"]

            # GMX-specific long/short breakdown (in info field)
            long_oi = oi["info"]["longOpenInterest"]
            short_oi = oi["info"]["shortOpenInterest"]

            token = symbol.replace("/USD", "")
            print(f"{token:<8} ${total_oi:>14,.0f} ${long_oi:>14,.0f} ${short_oi:>14,.0f}")
        except Exception as e:
            print(f"Error fetching OI for {symbol}: {e}")

    print("\n" + "=" * 60)
    print("\nFetch Multiple Markets at Once\n")

    try:
        ois = gmx.fetch_open_interests(["ETH/USDC:USDC", "BTC/USDC:USDC"])
        for symbol, oi in ois.items():
            print(f"{symbol}: ${oi['openInterestValue']:,.0f}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n" + "=" * 60)
    print("\nHistorical Open Interest (ETH/USDC:USDC - Last 5 snapshots)\n")

    try:
        history = gmx.fetch_open_interest_history("ETH/USDC:USDC", limit=5)

        print(f"{'Index':<6} {'Total OI':>15} {'Long OI':>15} {'Short OI':>15}")
        print("-" * 60)

        for i, snapshot in enumerate(history):
            total = snapshot["openInterestValue"]
            long = snapshot["info"]["longOpenInterest"]
            short = snapshot["info"]["shortOpenInterest"]
            print(f"{i:<6} ${total:>14,.0f} ${long:>14,.0f} ${short:>14,.0f}")

    except Exception as e:
        print(f"Error fetching OI history: {e}")


if __name__ == "__main__":
    main()
