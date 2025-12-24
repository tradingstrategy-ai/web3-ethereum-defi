"""
Fetch markets example.

Demonstrates fetching all available markets from GMX without caching.
"""

from web3 import Web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.ccxt import GMX
from rich.console import Console

print = Console().print


def main():
    # Connect to Arbitrum
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    exchange = GMX(config)

    # Fetch markets (returns list, does not cache)
    print("Fetching markets from GMX...\n")
    markets = exchange.fetch_markets()

    print(f"Found {len(markets)} markets\n")
    # print(f"{'Symbol':12} {'Base':8} {'Quote':8} {'Type':8} {'Active':8}")
    # print("-" * 50)

    print(f"{markets=}")


if __name__ == "__main__":
    main()
