"""Test chain reorganisation monitor and connect to live Polygon network."""
import os

import flaky
from web3 import Web3, HTTPProvider

from eth_defi.chain import install_chain_middleware
from eth_defi.event_reader.reorganisation_monitor import MockChainAndReorganisationMonitor, JSONRPCReorganisationMonitor

JSON_RPC_POLYGON = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")


@flaky.flaky
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
