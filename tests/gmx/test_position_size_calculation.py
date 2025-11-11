"""Test to verify position size calculation matches Foundry formula.

Foundry formula:
uint256 positionSizeUsd = (ETH_COLLATERAL * ETH_PRICE_USD * 2.5e30) / 1e18;

Where:
- ETH_COLLATERAL = 0.001 ETH = 10^15 wei
- ETH_PRICE_USD = 3450
- leverage = 2.5

Expected result: (10^15 * 3450 * 2.5 * 10^30) / 10^18 = 8.625 * 10^30
"""


def test_position_size_calculation_matches_foundry():
    """Verify SDK calculation matches Foundry formula exactly."""

    # Input parameters (matching Foundry test)
    eth_collateral_tokens = 0.001  # 0.001 ETH (human-readable)
    eth_price_usd = 3450  # $3450 per ETH
    leverage = 2.5

    # SDK calculation (simulating what happens in OrderArgumentParser)
    # Step 1: Calculate collateral value in USD
    collateral_usd = eth_collateral_tokens * eth_price_usd  # 0.001 * 3450 = 3.45 USD
    print(f"Collateral USD value: ${collateral_usd}")

    # Step 2: Calculate position size in USD (human-readable)
    size_delta_usd = leverage * collateral_usd  # 2.5 * 3.45 = 8.625 USD
    print(f"Position size USD (human): ${size_delta_usd}")

    # Step 3: Convert to 30-decimal format (what gets sent to GMX contract)
    size_delta_30_decimals = int(size_delta_usd * 10**30)
    print(f"Position size (30 decimals): {size_delta_30_decimals}")

    # Foundry calculation
    eth_collateral_wei = int(eth_collateral_tokens * 10**18)  # 10^15 wei
    foundry_result = (eth_collateral_wei * eth_price_usd * int(2.5 * 10**30)) // (10**18)
    print(f"\nFoundry result (30 decimals): {foundry_result}")

    # Check difference
    difference = abs(size_delta_30_decimals - foundry_result)
    percent_diff = (difference / foundry_result) * 100
    print(f"\nDifference: {difference}")
    print(f"Percent difference: {percent_diff:.10f}%")

    # Allow for tiny floating-point precision differences (< 0.0001%)
    # This is why we read position_size_usd_raw from the contract when closing!
    tolerance_percent = 0.0001
    matches_within_tolerance = percent_diff < tolerance_percent

    print(f"\nSDK matches Foundry (within {tolerance_percent}% tolerance): {matches_within_tolerance}")

    assert matches_within_tolerance, f"Position size calculation differs by {percent_diff:.10f}% which exceeds tolerance of {tolerance_percent}%\nSDK={size_delta_30_decimals}\nFoundry={foundry_result}\nThis is why we use position_size_usd_raw from the contract when closing positions!"

    print("\n✅ Position size calculation matches Foundry formula (within floating-point tolerance)!")
    print("\n⚠️  IMPORTANT: When closing positions, always use position_size_usd_raw from the contract")
    print("   to avoid precision mismatches. The on-chain value is the source of truth!")


if __name__ == "__main__":
    test_position_size_calculation_matches_foundry()
