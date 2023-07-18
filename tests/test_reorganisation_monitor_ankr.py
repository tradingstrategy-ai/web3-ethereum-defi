"""Test chain reogranisation monitor and connect to live Ankr network."""

import os

import pytest
from web3 import HTTPProvider

from eth_defi.chain import has_ankr_support
from eth_defi.event_reader.reorganisation_monitor import AnkrReogranisationMonitor, AnkrSupportedBlockchain

pytestmark = pytest.mark.skipif(
    os.environ.get("JSON_RPC_ANKR_PRIVATE") is None,
    reason="Set JSON_RPC_ANKR_PRIVATE environment variable to a privately configured Polygon node with GraphQL turned on",
)


def test_ankr_last_block():
    """Get last block num using Ankr API."""

    provider = HTTPProvider(os.environ["JSON_RPC_ANKR_PRIVATE"])
    assert has_ankr_support(provider)

    reorg_mon = AnkrReogranisationMonitor(provider=provider, blockchain=AnkrSupportedBlockchain.arbitrum)
    block_number = reorg_mon.get_last_block_live()
    assert block_number > 30_000_000


def test_ankr_block_headers():
    """Download block headers using Ankr API."""

    provider = HTTPProvider(os.environ["JSON_RPC_ANKR_PRIVATE"])
    assert has_ankr_support(provider)

    reorg_mon = AnkrReogranisationMonitor(provider=provider, blockchain=AnkrSupportedBlockchain.arbitrum)

    start_block, end_block = reorg_mon.load_initial_block_headers(block_count=5)

    block = reorg_mon.get_block_by_number(start_block)
    assert block.block_number > 0
    assert block.block_hash.startswith("0x")
    assert block.timestamp > 0
