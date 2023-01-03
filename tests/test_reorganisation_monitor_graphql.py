"""Test chain reorganisation monitor and connect to live Polygon network."""
import os

import pytest
from web3 import HTTPProvider

from eth_defi.chain import has_graphql_support
from eth_defi.event_reader.reorganisation_monitor import GraphQLReorganisationMonitor

pytestmark = pytest.mark.skipif(
    os.environ.get("JSON_RPC_POLYGON_PRIVATE") is None,
    reason="Set JSON_RPC_POLYGON_PRIVATE environment variable to a privately configured Polygon node with GraphQL turned on",
)


def test_graphql_last_block():
    """Get last block num using GoEthereum GraphQL API."""

    # A specially set up server to test this
    # Does provide /graphql
    provider = HTTPProvider(os.environ["JSON_RPC_POLYGON_PRIVATE"])
    assert has_graphql_support(provider)

    reorg_mon = GraphQLReorganisationMonitor(provider=provider)
    block_number = reorg_mon.get_last_block_live()
    assert block_number > 30_000_000


def test_graphql_block_headers():
    """Download block headers using GoEthereum GraphQL API."""

    # A specially set up server to test this
    # Does provide /graphql
    provider = HTTPProvider(os.environ["JSON_RPC_POLYGON_PRIVATE"])
    assert has_graphql_support(provider)

    reorg_mon = GraphQLReorganisationMonitor(provider=provider)

    start_block, end_block = reorg_mon.load_initial_block_headers(block_count=5)

    block = reorg_mon.get_block_by_number(start_block)
    assert block.block_number > 0
    assert block.block_hash.startswith("0x")
    assert block.timestamp > 0
