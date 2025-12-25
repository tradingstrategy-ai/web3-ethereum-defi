"""Example: Fetch detailed leverage tiers for specific GMX markets.

Shows how leverage changes based on open interest levels.

Usage:
    python scripts/gmx/fetch_leverage_tiers_example.py
"""

from web3 import Web3
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.ccxt.exchange import GMX


def format_usd(value: float) -> str:
    """Format USD value with appropriate suffix."""
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    elif value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.2f}K"
    else:
        return f"${value:.2f}"


def main():
    """Fetch and display leverage tiers for BTC/USD."""

    # Initialize GMX CCXT exchange
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    gmx = GMX(config)

    symbol = "BTC/USD"

    print(f"\n{'=' * 80}")
    print(f"Leverage Tiers for {symbol}")
    print(f"{'=' * 80}\n")

    # Fetch leverage tiers for long positions
    print("LONG POSITIONS:")
    print("-" * 80)
    long_tiers = gmx.fetch_market_leverage_tiers(symbol, {"side": "long", "num_tiers": 5})

    if long_tiers:
        print(f"{'Tier':<6} {'Position Size Range':<40} {'Max Leverage':<15} {'Min Collateral'}")
        print("-" * 80)

        for tier in long_tiers:
            tier_num = tier["tier"]
            min_notional = format_usd(tier["minNotional"])
            max_notional = format_usd(tier["maxNotional"])
            max_lev = f"{tier['maxLeverage']:.1f}x"
            min_col = f"{tier['minCollateralFactor'] * 100:.2f}%"

            notional_range = f"{min_notional} - {max_notional}"
            print(f"{tier_num:<6} {notional_range:<40} {max_lev:<15} {min_col}")
    else:
        print("No tier data available")

    # Fetch leverage tiers for short positions
    print("\n\nSHORT POSITIONS:")
    print("-" * 80)
    short_tiers = gmx.fetch_market_leverage_tiers(symbol, {"side": "short", "num_tiers": 5})

    if short_tiers:
        print(f"{'Tier':<6} {'Position Size Range':<40} {'Max Leverage':<15} {'Min Collateral'}")
        print("-" * 80)

        for tier in short_tiers:
            tier_num = tier["tier"]
            min_notional = format_usd(tier["minNotional"])
            max_notional = format_usd(tier["maxNotional"])
            max_lev = f"{tier['maxLeverage']:.1f}x"
            min_col = f"{tier['minCollateralFactor'] * 100:.2f}%"

            notional_range = f"{min_notional} - {max_notional}"
            print(f"{tier_num:<6} {notional_range:<40} {max_lev:<15} {min_col}")
    else:
        print("No tier data available")

    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()
