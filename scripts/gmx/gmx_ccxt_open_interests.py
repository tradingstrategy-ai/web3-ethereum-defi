"""GMX Open Interests (Multiple Markets) Example.

Demonstrates fetching open interest for multiple markets at once.
Uses the fetch_open_interests() method to efficiently query multiple symbols.
"""

from web3 import Web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.ccxt import GMX


def main():
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    gmx = GMX(config)

    gmx.load_markets()

    print("Fetch Open Interest for Multiple Markets\n")

    # Fetch OI for specific markets
    symbols = ["ETH/USDC:USDC", "BTC/USDC:USDC", "ARB/USDC:USDC", "LINK/USDC:USDC", "UNI/USDC:USDC", "SOL/USDC:USDC"]
    ois = gmx.fetch_open_interests(symbols)

    print(
        f"{'Market':<10} {'Total OI':>18} {'Long OI':>18} {'Short OI':>18} {'Long %':>10}",
    )
    print("-" * 80)

    for symbol in symbols:
        if symbol in ois:
            oi = ois[symbol]
            total = oi["openInterestValue"]
            long_oi = oi["info"]["longOpenInterest"]
            short_oi = oi["info"]["shortOpenInterest"]
            long_pct = (long_oi / total * 100) if total > 0 else 0

            token = symbol.replace("/USD", "")
            print(
                f"{token:<10} ${total:>17,.0f} ${long_oi:>17,.0f} ${short_oi:>17,.0f} {long_pct:>9.1f}%",
            )
        else:
            print(f"{symbol:<10} No data available")

    print("\n" + "=" * 80)
    print("\nFetch All Markets\n")

    # Fetch OI for all available markets
    all_ois = gmx.fetch_open_interests()

    # Sort by total OI descending
    sorted_markets = sorted(
        all_ois.items(),
        key=lambda x: x[1]["openInterestValue"],
        reverse=True,
    )

    print(f"{'Market':<10} {'Total OI':>18} {'Long/Short Ratio':>20}")
    print("-" * 55)

    for symbol, oi in sorted_markets[:10]:
        total = oi["openInterestValue"]
        long_oi = oi["info"]["longOpenInterest"]
        short_oi = oi["info"]["shortOpenInterest"]

        if short_oi > 0:
            ratio = long_oi / short_oi
            ratio_str = f"{ratio:.3f}"
        else:
            ratio_str = "N/A"

        token = symbol.replace("/USD", "")
        print(f"{token:<10} ${total:>17,.0f} {ratio_str:>20}")

    print(f"\nTotal markets: {len(all_ois)}")


if __name__ == "__main__":
    main()
