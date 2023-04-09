"""Test chain reorganisation monitor and connect to live Polygon network."""
import os

import pytest
from web3 import Web3, HTTPProvider

from eth_defi.chain import install_chain_middleware
from eth_defi.event_reader.reorganisation_monitor import JSONRPCReorganisationMonitor, create_reorganisation_monitor

# Allow to override for a private node to run test faster
JSON_RPC_POLYGON = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")


@pytest.mark.skipif(os.environ.get("CI") is not None, reason="Too flaky to run on Github because public Polygon endpoint is crap")
def test_polygon_block_headers():
    """Polygon block header data is downloaded."""
    web3 = Web3(HTTPProvider(JSON_RPC_POLYGON))
    install_chain_middleware(web3)

    assert web3.eth.block_number > 0

    reorg_mon = JSONRPCReorganisationMonitor(web3)
    start_block, end_block = reorg_mon.load_initial_block_headers(5)
    assert start_block > 0
    assert end_block > 0
    assert reorg_mon.get_last_block_read() > 0
    assert reorg_mon.get_last_block_live() > 0


def test_create_reorganisation_monitor():
    """Create reorganisation monitor using the shortcut against a public Polygon node."""
    web3 = Web3(HTTPProvider("https://polygon-rpc.com"))
    mon = create_reorganisation_monitor(web3)
    assert isinstance(mon, JSONRPCReorganisationMonitor)
