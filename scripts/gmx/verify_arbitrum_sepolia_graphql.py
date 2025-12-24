#!/usr/bin/env python3
"""
Verify Arbitrum Sepolia Subsquid GraphQL endpoint connectivity.

This script tests that the new Arbitrum Sepolia endpoint is accessible
and returns valid data.
"""

from eth_defi.gmx.graphql.client import GMXSubsquidClient


def main():
    print("Testing Arbitrum Sepolia Subsquid GraphQL endpoint...")
    print()

    # Initialize client for Arbitrum Sepolia
    try:
        client = GMXSubsquidClient(chain="arbitrum_sepolia")
        print(f"Client initialized successfully")
        print(f"Endpoint: {client.endpoint}")
        print()
    except Exception as e:
        print(f"Failed to initialize client: {e}")
        return

    # Test 1: Fetch markets
    print("Test 1: Fetching available markets...")
    try:
        markets = client.get_markets()
        print(f"Found {len(markets)} markets")
        if markets:
            print("Sample market:")
            market = markets[0]
            print(f"  Market Address: {market['id']}")
            print(f"  Index Token: {market['indexToken']}")
            print(f"  Long Token: {market['longToken']}")
            print(f"  Short Token: {market['shortToken']}")
        print()
    except Exception as e:
        print(f"Failed to fetch markets: {e}")
        print()

    # Test 2: Fetch market infos
    print("Test 2: Fetching market information...")
    try:
        market_infos = client.get_market_infos(limit=5)
        print(f"Found {len(market_infos)} market info snapshots")
        if market_infos:
            print("Sample market info:")
            info = market_infos[0]
            print(f"  Market: {info['marketTokenAddress']}")
            print(f"  Long OI: {info['longOpenInterestUsd']}")
            print(f"  Short OI: {info['shortOpenInterestUsd']}")

            # Calculate max leverage if available
            if info.get("minCollateralFactor"):
                max_leverage = GMXSubsquidClient.calculate_max_leverage(info["minCollateralFactor"])
                if max_leverage:
                    print(f"  Max Leverage: {max_leverage:.1f}x")
        print()
    except Exception as e:
        print(f"Failed to fetch market infos: {e}")
        print()

    # Test 3: Test position query (will likely return empty for test account)
    print("Test 3: Testing position query...")
    test_account = "0x0000000000000000000000000000000000000001"
    try:
        positions = client.get_positions(account=test_account, only_open=False, limit=10)
        print(f"Query successful - found {len(positions)} positions for test account")
        print()
    except Exception as e:
        print(f"Failed to query positions: {e}")
        print()

    print("All tests completed!")
    print()
    print("Arbitrum Sepolia Subsquid endpoint is now available for:")
    print("  - GMXSubsquidClient(chain='arbitrum_sepolia')")
    print("  - GMX CCXT adapter with arbitrum_sepolia configuration")


if __name__ == "__main__":
    main()
