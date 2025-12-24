"""
Funding rate analysis example.

Fetches current funding rates with long/short breakdowns and projects to daily/annual rates.
Also demonstrates historical funding rate data.
"""

from web3 import Web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.ccxt import GMX


def main():
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    exchange = GMX(config)

    exchange.load_markets()

    symbols = ["ETH/USD", "BTC/USD", "ARB/USD"]

    print("GMX Funding Rates (Hourly)\n")
    print(f"{'Market':<8} {'Long Rate':>12} {'Short Rate':>12} {'Daily (Long)':>14} {'Annual (Long)':>15} {'Direction':>20}")
    print("-" * 95)

    for symbol in symbols:
        try:
            fr = exchange.fetch_funding_rate(symbol)

            long_rate = fr["longFundingRate"]
            short_rate = fr["shortFundingRate"]

            long_daily = long_rate * 24
            long_annual = long_rate * 24 * 365

            if long_rate > 0:
                direction = "Longs pay Shorts"
            elif long_rate < 0:
                direction = "Shorts pay Longs"
            else:
                direction = "Balanced"

            token = symbol.replace("/USD", "")
            print(
                f"{token:<8} {long_rate} {short_rate} {long_daily} {long_annual} {direction}",
            )

        except Exception as e:
            print(f"Error fetching funding rate for {symbol}: {e}")

    print("\n" + "=" * 95)
    print("\nHistorical Funding Rates (ETH/USD - Last 10 snapshots)\n")

    try:
        history = exchange.fetch_funding_rate_history("ETH/USD", limit=10)

        print(f"{'Index':<6} {'Rate/Second':>15} {'Hourly Rate':>15} {'Daily Rate':>15} {'Long Rate':>15} {'Short Rate':>15}")
        print("-" * 95)

        for i, snapshot in enumerate(history):
            rate = snapshot["fundingRate"]
            hourly = rate * 3600
            daily = rate * 3600 * 24
            long_rate = snapshot["longFundingRate"]
            short_rate = snapshot["shortFundingRate"]

            print(f"{i:<6} {rate:>15.10f} {hourly * 100:>14.4f}% {daily * 100:>14.4f}% {long_rate:>15.10f} {short_rate:>15.10f}")

    except Exception as e:
        print(f"Error fetching funding rate history: {e}")


if __name__ == "__main__":
    main()
