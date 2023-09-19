"""Chain / node feature tests."""

import os

import pytest
from web3 import HTTPProvider, Web3

from eth_defi.chain import has_graphql_support
from eth_defi.provider.broken_provider import get_block_tip_latency


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


def test_block_tip_latency():
    """Check for the block tip latency by a provider."""

    # Does not provide /graphql
    provider = HTTPProvider("https://polygon-rpc.com/")
    web3 = Web3(provider)
    assert get_block_tip_latency(web3) == 0
