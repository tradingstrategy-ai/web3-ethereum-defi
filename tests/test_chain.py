"""Chain / node feature tests."""

import os

import pytest
from web3 import HTTPProvider

from eth_defi.chain import has_graphql_support


def test_has_not_graphql_support():
    """Check if a GoEthereum node has GraphQL support turned on."""

    # Does not provide /graphql
    provider = HTTPProvider("https://polygon-rpc.com/")
    assert not has_graphql_support(provider)


@pytest.mark.skipif(
    os.environ.get("JSON_RPC_POLYGON_PRIVATE") is None,
    reason="Set JSON_RPC_POLYGON_PRIVATE environment variable to a privately configured Polygon node with GraphQL turned on",
)
def test_has_graphql_support():
    """Check if a GoEthereum node has GraphQL support turned on."""

    # A specially set up server to test this
    # Does provide /graphql
    provider = HTTPProvider(os.environ["JSON_RPC_POLYGON_PRIVATE"])
    assert has_graphql_support(provider)
