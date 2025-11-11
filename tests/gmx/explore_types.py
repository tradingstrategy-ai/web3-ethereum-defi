"""Explore specific types in GMX Subsquid GraphQL schema."""

import requests
import json

GRAPHQL_URL = "https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql"

# Query to get type details
TYPE_QUERY = """
query TypeDetails {
  __type(name: "Position") {
    name
    kind
    description
    fields {
      name
      description
      type {
        name
        kind
        ofType {
          name
          kind
        }
      }
    }
  }
}
"""


def explore_position_type():
    """Fetch and display Position type fields."""
    response = requests.post(GRAPHQL_URL, json={"query": TYPE_QUERY}, headers={"Content-Type": "application/json"})

    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        print(response.text)
        return

    data = response.json()
    position_type = data["data"]["__type"]

    print("=" * 80)
    print(f"TYPE: {position_type['name']}")
    print("=" * 80)

    if position_type["description"]:
        print(f"\nDescription: {position_type['description']}\n")

    print("Fields:")
    for field in position_type["fields"]:
        field_type = field["type"]
        type_name = field_type.get("name") or (field_type.get("ofType", {}).get("name", "Unknown"))
        print(f"  {field['name']}: {type_name}")
        if field["description"]:
            print(f"    -> {field['description']}")


# Also test a live query to see what data looks like
TEST_QUERY = """
query TestPositionsQuery {
  positions(limit: 1, where: {status_eq: OPEN}) {
    id
    account
    marketAddress
    collateralTokenAddress
    sizeInUsd
    sizeInTokens
    collateralAmount
    borrowingFactor
    fundingFactor
    longTokenClaimableFundingAmountPerSize
    shortTokenClaimableFundingAmountPerSize
    increasedAtBlock
    decreasedAtBlock
    isLong
    status
  }
}
"""


def test_query():
    """Test querying positions."""
    print("\n" + "=" * 80)
    print("TEST QUERY: Fetching one open position")
    print("=" * 80)

    response = requests.post(GRAPHQL_URL, json={"query": TEST_QUERY}, headers={"Content-Type": "application/json"})

    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        print(response.text)
        return

    data = response.json()
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    explore_position_type()
    test_query()
