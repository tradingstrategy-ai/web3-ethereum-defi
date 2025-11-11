"""Explore GMX Subsquid GraphQL endpoint to understand available queries."""

import requests
import json

GRAPHQL_URL = "https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql"

# Introspection query to get schema
INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    types {
      name
      kind
      description
      fields {
        name
        description
        args {
          name
          type {
            name
            kind
            ofType {
              name
              kind
            }
          }
        }
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
}
"""


def introspect_schema():
    """Fetch and display GraphQL schema."""
    response = requests.post(GRAPHQL_URL, json={"query": INTROSPECTION_QUERY}, headers={"Content-Type": "application/json"})

    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        print(response.text)
        return

    data = response.json()

    # Find Query type
    query_type = None
    for type_info in data["data"]["__schema"]["types"]:
        if type_info["name"] == data["data"]["__schema"]["queryType"]["name"]:
            query_type = type_info
            break

    if not query_type:
        print("No Query type found")
        return

    print("=" * 80)
    print("AVAILABLE QUERIES")
    print("=" * 80)

    for field in query_type["fields"]:
        print(f"\n{field['name']}")
        if field["description"]:
            print(f"  Description: {field['description']}")

        # Show arguments
        if field["args"]:
            print("  Arguments:")
            for arg in field["args"]:
                arg_type = arg["type"]
                type_name = arg_type.get("name") or (arg_type.get("ofType", {}).get("name", "Unknown"))
                print(f"    - {arg['name']}: {type_name}")

        # Show return type
        return_type = field["type"]
        type_name = return_type.get("name") or (return_type.get("ofType", {}).get("name", "Unknown"))
        print(f"  Returns: {type_name}")


if __name__ == "__main__":
    introspect_schema()
