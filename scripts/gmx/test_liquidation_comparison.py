"""
Test script to compare Python liquidation price calculations against JavaScript SDK output.

This script helps verify that the Python implementation matches the official GMX TypeScript SDK.

Usage:
    1. Run the corresponding JavaScript code to get reference values
    2. Update the REFERENCE_DATA dictionary below with JavaScript output
    3. Run this script to compare Python calculations

    python scripts/gmx/test_liquidation_comparison.py

Environment variables:
    JSON_RPC_ARBITRUM: Arbitrum RPC endpoint
    WALLET_ADDRESS: Wallet address with positions (optional, uses test address by default)
"""

import os
import sys
from decimal import Decimal

from web3 import Web3

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.utils import get_positions, calculate_estimated_liquidation_price
from eth_defi.gmx.core.liquidation import get_liquidation_price


# Reference data from JavaScript SDK
# Update these values after running the equivalent JavaScript code
REFERENCE_DATA = {
    "ETH_long": {
        "entry_price": 2000.0,
        "collateral_usd": 1000.0,
        "size_usd": 5000.0,
        "is_long": True,
        "pending_funding_fees_usd": 5.0,
        "pending_borrowing_fees_usd": 10.0,
        # Expected liquidation price from JavaScript SDK
        "js_liquidation_price": None,  # UPDATE THIS
    },
    "BTC_short": {
        "entry_price": 40000.0,
        "collateral_usd": 2000.0,
        "size_usd": 10000.0,
        "is_long": False,
        "pending_funding_fees_usd": 8.0,
        "pending_borrowing_fees_usd": 15.0,
        # Expected liquidation price from JavaScript SDK
        "js_liquidation_price": None,  # UPDATE THIS
    },
}


def print_comparison_header():
    """Print formatted header for comparison table."""
    print("\n" + "=" * 120)
    print("LIQUIDATION PRICE COMPARISON: Python vs JavaScript SDK")
    print("=" * 120)
    print(f"{'Position':<15} {'Entry':<12} {'Collateral':<12} {'Size':<12} {'Leverage':<10} {'Python':<15} {'JavaScript':<15} {'Diff %':<10}")
    print("-" * 120)


def calculate_simplified_liquidation(position_data: dict) -> float:
    """Calculate liquidation price using simplified utils function."""
    return calculate_estimated_liquidation_price(
        entry_price=position_data["entry_price"],
        collateral_usd=position_data["collateral_usd"],
        size_usd=position_data["size_usd"],
        is_long=position_data["is_long"],
        maintenance_margin=0.01,
        pending_funding_fees_usd=position_data["pending_funding_fees_usd"],
        pending_borrowing_fees_usd=position_data["pending_borrowing_fees_usd"],
        include_closing_fee=True,
    )


def compare_with_reference_data():
    """Compare Python calculations against reference JavaScript values."""
    print_comparison_header()

    for position_name, position_data in REFERENCE_DATA.items():
        # Calculate using Python
        python_liq_price = calculate_simplified_liquidation(position_data)

        # Get JavaScript reference
        js_liq_price = position_data["js_liquidation_price"]

        # Calculate metrics
        leverage = position_data["size_usd"] / position_data["collateral_usd"]

        # Calculate difference
        if js_liq_price is not None:
            diff_pct = ((python_liq_price - js_liq_price) / js_liq_price) * 100
            diff_str = f"{diff_pct:+.2f}%"
            js_str = f"${js_liq_price:,.2f}"
        else:
            diff_str = "NO REF"
            js_str = "NOT SET"

        # Print row
        print(f"{position_name:<15} ${position_data['entry_price']:>10,.2f} ${position_data['collateral_usd']:>10,.2f} ${position_data['size_usd']:>10,.2f} {leverage:>8.2f}x ${python_liq_price:>13,.2f} {js_str:>15} {diff_str:>10}")

    print("-" * 120)


def test_live_positions():
    """Test with live positions from on-chain data."""
    wallet_address = os.environ.get("WALLET_ADDRESS", "0xaea8E3Bd369217CC6E3e6AbdDf0dA318fBA8E59b")
    rpc_url = os.environ.get("JSON_RPC_ARBITRUM", "https://arb1.arbitrum.io/rpc")

    print("\n" + "=" * 120)
    print(f"LIVE POSITION TESTING - Wallet: {wallet_address}")
    print("=" * 120)

    try:
        web3 = Web3(Web3.HTTPProvider(rpc_url))
        config = GMXConfig(web3, user_wallet_address=wallet_address)

        # Get positions
        print("\nFetching positions from chain...")
        positions = get_positions(config, wallet_address)

        if not positions:
            print("No open positions found.")
            return

        print(f"Found {len(positions)} position(s)\n")
        print("Note: 'Simplified' calculation includes fees and is the recommended method.")
        print("      'On-Chain' method is experimental and may fail due to contract compatibility.\n")
        print(f"{'Position':<20} {'Entry Price':<15} {'Collateral':<15} {'Size':<15} {'Simplified':<15} {'On-Chain':<15}")
        print("-" * 120)

        for position_key, position in positions.items():
            # Simplified calculation
            simplified_liq = calculate_estimated_liquidation_price(
                entry_price=position["entry_price"],
                collateral_usd=position["initial_collateral_amount_usd"],
                size_usd=position["position_size"],
                is_long=position["is_long"],
                maintenance_margin=0.01,
                include_closing_fee=True,
            )

            # Try on-chain calculation (may fail if missing data)
            try:
                onchain_liq = get_liquidation_price(config, position, wallet_address)
                onchain_str = f"${onchain_liq:,.2f}" if onchain_liq else "FAILED"
            except Exception as e:
                onchain_str = "N/A (contract error)"

            print(f"{position_key:<20} ${position['entry_price']:>13,.2f} ${position['initial_collateral_amount_usd']:>13,.2f} ${position['position_size']:>13,.2f} ${simplified_liq:>13,.2f} {onchain_str:>15}")

        print("-" * 120)

    except Exception as e:
        print(f"Error testing live positions: {e}")
        import traceback

        traceback.print_exc()


def generate_javascript_test_code():
    """Generate JavaScript code to get reference values."""
    print("\n" + "=" * 120)
    print("JAVASCRIPT REFERENCE CODE")
    print("=" * 120)
    print(
        """
Run this TypeScript/JavaScript code to get reference liquidation prices:

```typescript
import { getLiquidationPrice } from '@gmx-io/sdk';

// ETH Long Position
const ethLongLiqPrice = getLiquidationPrice({
    sizeInUsd: BigInt(5000) * BigInt(10**30),
    sizeInTokens: BigInt(2.5) * BigInt(10**18),  // size_usd / entry_price
    collateralAmount: BigInt(0.5) * BigInt(10**18),  // If using ETH as collateral
    collateralUsd: BigInt(1000) * BigInt(10**30),
    collateralToken: ETH_TOKEN,
    marketInfo: ETH_MARKET_INFO,
    pendingFundingFeesUsd: BigInt(5) * BigInt(10**30),
    pendingBorrowingFeesUsd: BigInt(10) * BigInt(10**30),
    pendingImpactAmount: BigInt(0),
    minCollateralUsd: BigInt(5) * BigInt(10**30),
    isLong: true,
    userReferralInfo: undefined,
});

console.log('ETH Long Liquidation:', Number(ethLongLiqPrice) / 10**30);

// BTC Short Position
const btcShortLiqPrice = getLiquidationPrice({
    sizeInUsd: BigInt(10000) * BigInt(10**30),
    sizeInTokens: BigInt(0.25) * BigInt(10**8),  // BTC has 8 decimals
    collateralAmount: BigInt(2000) * BigInt(10**6),  // If using USDC (6 decimals)
    collateralUsd: BigInt(2000) * BigInt(10**30),
    collateralToken: USDC_TOKEN,
    marketInfo: BTC_MARKET_INFO,
    pendingFundingFeesUsd: BigInt(8) * BigInt(10**30),
    pendingBorrowingFeesUsd: BigInt(15) * BigInt(10**30),
    pendingImpactAmount: BigInt(0),
    minCollateralUsd: BigInt(5) * BigInt(10**30),
    isLong: false,
    userReferralInfo: undefined,
});

console.log('BTC Short Liquidation:', Number(btcShortLiqPrice) / 10**30);
```

Update REFERENCE_DATA dictionary with these values, then re-run this script.
"""
    )
    print("=" * 120)


def main():
    """Run all comparison tests."""
    print("\n" + "=" * 60)
    print("GMX Liquidation Price Calculation Test Suite")
    print("=" * 60)

    # Test 1: Compare with reference data
    print("\n[TEST 1] Comparing with JavaScript SDK reference data...")
    compare_with_reference_data()

    # Test 2: Live positions (if available)
    print("\n[TEST 2] Testing with live on-chain positions...")
    test_live_positions()

    # Test 3: Show JavaScript code
    generate_javascript_test_code()

    print("\n" + "=" * 60)
    print("Test suite completed!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
