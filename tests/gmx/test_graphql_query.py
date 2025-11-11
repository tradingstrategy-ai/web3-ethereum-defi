"""Test GraphQL queries against GMX Subsquid endpoint."""

import requests
import json

GRAPHQL_URL = "https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql"

# Corrected test query using actual fields
POSITIONS_QUERY = """
query TestPositionsQuery($account: String!) {
  positions(
    limit: 10,
    where: {
      account_eq: $account,
      sizeInUsd_gt: "0"
    }
  ) {
    id
    positionKey
    account
    market
    collateralToken
    isLong
    collateralAmount
    sizeInTokens
    sizeInUsd
    entryPrice
    realizedPnl
    unrealizedPnl
    realizedFees
    unrealizedFees
    realizedPriceImpact
    unrealizedPriceImpact
    leverage
    openedAt
  }
}
"""

# PnL Summary query (from TypeScript code)
PNL_SUMMARY_QUERY = """
query AccountHistoricalPnlResolver($account: String!) {
  accountPnlSummaryStats(account: $account) {
    bucketLabel
    losses
    pnlBps
    pnlUsd
    realizedPnlUsd
    unrealizedPnlUsd
    startUnrealizedPnlUsd
    volume
    wins
    winsLossesRatioBps
    usedCapitalUsd
  }
}
"""


def test_positions_query(account: str):
    """Test fetching positions for an account."""
    print("=" * 80)
    print(f"Fetching positions for account: {account}")
    print("=" * 80)

    response = requests.post(GRAPHQL_URL, json={"query": POSITIONS_QUERY, "variables": {"account": account}}, headers={"Content-Type": "application/json"})

    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        print(response.text)
        return

    data = response.json()

    if "errors" in data:
        print("GraphQL Errors:")
        for error in data["errors"]:
            print(f"  - {error['message']}")
        return

    positions = data.get("data", {}).get("positions", [])
    print(f"\nFound {len(positions)} positions\n")

    if positions:
        for pos in positions:
            print(f"Position ID: {pos['id']}")
            print(f"  Market: {pos['market']}")
            print(f"  Collateral: {pos['collateralToken']}")
            print(f"  Direction: {'LONG' if pos['isLong'] else 'SHORT'}")
            print(f"  Size (USD): {int(pos['sizeInUsd']) / 10**30:.2f}")
            print(f"  Collateral Amount: {int(pos['collateralAmount']) / 10**30:.2f}")
            print(f"  Entry Price: {int(pos['entryPrice']) / 10**30:.2f}")
            print(f"  Leverage: {int(pos['leverage']) / 10**30:.2f}x")
            print(f"  Unrealized PnL: {int(pos['unrealizedPnl']) / 10**30:.2f}")
            print()
    else:
        print("No positions found for this account")


def test_pnl_summary_query(account: str):
    """Test fetching PnL summary for an account."""
    print("=" * 80)
    print(f"Fetching PnL summary for account: {account}")
    print("=" * 80)

    response = requests.post(GRAPHQL_URL, json={"query": PNL_SUMMARY_QUERY, "variables": {"account": account}}, headers={"Content-Type": "application/json"})

    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        print(response.text)
        return

    data = response.json()

    if "errors" in data:
        print("GraphQL Errors:")
        for error in data["errors"]:
            print(f"  - {error['message']}")
        return

    stats = data.get("data", {}).get("accountPnlSummaryStats", [])
    print(f"\nFound {len(stats)} summary periods\n")

    for stat in stats:
        print(f"Period: {stat['bucketLabel']}")
        print(f"  PnL: ${int(stat['pnlUsd']) / 10**30:.2f}")
        print(f"  Wins: {stat['wins']}, Losses: {stat['losses']}")
        print(f"  Volume: ${int(stat['volume']) / 10**30:.2f}")
        print()


if __name__ == "__main__":
    # Test with a known account (you can replace with your test account)
    test_account = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"  # Default anvil account

    test_positions_query(test_account)
    print("\n" + "=" * 80 + "\n")
    test_pnl_summary_query(test_account)
