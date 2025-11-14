"""GMX Open Interest Example.

Demonstrates fetching open interest data using CCXT-compatible interface.
Shows both current and historical open interest for GMX markets.
"""

from web3 import Web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.ccxt import GMXCCXT


def main():
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    gmx = GMXCCXT(config)

    gmx.load_markets()

    symbols = ["ETH/USD", "BTC/USD", "ARB/USD"]

    print("GMX Open Interest Data\n")
    print(f"{'Market':<8} {'Total OI':>15} {'Long OI':>15} {'Short OI':>15}")
    print("-" * 60)

    for symbol in symbols:
        try:
            oi = gmx.fetch_open_interest(symbol)

            token = symbol.replace("/USD", "")
            print(f"{token:<8} ${oi['openInterestValue']:>14,.0f} ${oi['longOpenInterest']:>14,.0f} ${oi['shortOpenInterest']:>14,.0f}")
        except Exception as e:
            print(f"Error fetching OI for {symbol}: {e}")

    print("\n" + "=" * 60)
    print("\nHistorical Open Interest (ETH/USD - Last 5 snapshots)\n")

    try:
        history = gmx.fetch_open_interest_history("ETH/USD", limit=5)

        print(f"{'Index':<6} {'Total OI':>15} {'Long OI':>15} {'Short OI':>15}")
        print("-" * 60)

        for i, snapshot in enumerate(history):
            print(f"{i:<6} ${snapshot['openInterestValue']:>14,.0f} ${snapshot['longOpenInterest']:>14,.0f} ${snapshot['shortOpenInterest']:>14,.0f}")

    except Exception as e:
        print(f"Error fetching OI history: {e}")


if __name__ == "__main__":
    main()
