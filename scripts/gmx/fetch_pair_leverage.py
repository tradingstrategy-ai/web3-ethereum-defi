"""Simple example: Fetch leverage limits for each GMX trading pair.

This is the quick reference script for fetching leverage data.

Usage:
    python scripts/gmx/fetch_pair_leverage.py
"""

from web3 import Web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.graphql.client import GMXSubsquidClient


def main():
    """Fetch and display leverage limits for all GMX pairs."""

    # Initialize GMX CCXT exchange
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    gmx = GMX(config)

    # Fetch all markets with leverage data
    print("\nFetching GMX markets with leverage limits...\n")
    markets = gmx.fetch_markets()

    # Display leverage for each pair
    print(f"{'Symbol':<15} {'Max Leverage':<15} {'Min Collateral %'}")
    print("=" * 50)

    for market in markets:
        symbol = market["symbol"]
        leverage_limits = market.get("limits", {}).get("leverage", {})
        max_lev = leverage_limits.get("max")
        min_collateral = market.get("info", {}).get("min_collateral_factor")

        # Format max leverage
        if max_lev is not None:
            max_lev_str = f"{max_lev:.1f}x"
        else:
            max_lev_str = "N/A"

        # Format min collateral percentage
        if min_collateral:
            min_col_pct = float(GMXSubsquidClient.from_fixed_point(min_collateral, 30)) * 100
            min_col_str = f"{min_col_pct:.2f}%"
        else:
            min_col_str = "N/A"

        print(f"{symbol:<15} {max_lev_str:<15} {min_col_str}")

    print(f"\nTotal markets: {len(markets)}\n")


if __name__ == "__main__":
    main()
