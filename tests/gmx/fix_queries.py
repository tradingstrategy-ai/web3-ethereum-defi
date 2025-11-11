"""Test to fix the failing GraphQL queries."""

import requests

GRAPHQL_URL = "https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql"

# Test position changes query
POSITION_CHANGES_QUERY = """
query GetPositionChanges($account: String!, $limit: Int!) {
  positionChanges(
    limit: $limit
    where: {
      account_eq: $account
    }
  ) {
    id
    account
    market
    collateralToken
    isLong
    sizeInUsdDelta
    sizeInTokensDelta
    collateralAmountDelta
  }
}
"""

# Test account stats query
ACCOUNT_STATS_QUERY = """
query GetAccountStats($account: String!) {
  accountStats(
    where: {
      account_eq: $account
    }
    limit: 1
  ) {
    id
    account
    volume
    closedPositionsCount
    openPositionsCount
  }
}
"""


def test_query(query_name, query, variables):
    """Test a GraphQL query."""
    print(f"\nTesting {query_name}...")
    print(f"Variables: {variables}")

    response = requests.post(GRAPHQL_URL, json={"query": query, "variables": variables}, headers={"Content-Type": "application/json"})

    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        if "errors" in data:
            print("Errors:")
            for err in data["errors"]:
                print(f"  - {err['message']}")
        else:
            print("Success!")
            print(data)
    else:
        print(f"HTTP Error: {response.text}")


if __name__ == "__main__":
    test_account = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"

    test_query("PositionChanges", POSITION_CHANGES_QUERY, {"account": test_account, "limit": 10})

    test_query("AccountStats", ACCOUNT_STATS_QUERY, {"account": test_account})
