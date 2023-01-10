"""Event reader test coverage."""
import os

import pytest
import requests
from web3 import Web3, HTTPProvider

from eth_defi.abi import get_contract
from eth_defi.chain import install_chain_middleware
from eth_defi.event_reader.reader import read_events, BadTimestampValueReturned, TimestampNotFound


JSON_RPC_POLYGON = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")


def test_read_events_bad_timestamps():
    """Reading fails with a bad timestamp provider."""

    # HTTP 1.1 keep-alive
    session = requests.Session()

    web3 = Web3(HTTPProvider(JSON_RPC_POLYGON, session=session))

    web3.middleware_onion.clear()

    # Enable faster ujson reads
    install_chain_middleware(web3)

    # Get contracts
    Factory = get_contract(web3, "UniswapV2Factory.json")

    events = [
        Factory.events.PairCreated,
    ]

    # Randomly deployed pair
    # https://polygonscan.com/tx/0x476c5eb54ca14908cd06a987150eed9fe8d3c5992db09657a4e5bd35e0acb03b
    start_block = 37898275
    end_block = 37898278

    # Corrupted timestamp provider returning None
    def _extract_timestamps(web3, start_block, end_block):
        return {}

    def _extract_timestamps_2(web3, start_block, end_block):
        return None

    # Read through the blog ran
    out = []

    with pytest.raises(TimestampNotFound):
        for log_result in read_events(
            web3,
            start_block,
            end_block,
            events,
            None,
            chunk_size=1000,
            context=None,
            extract_timestamps=_extract_timestamps,
        ):
            out.append(log_result)

    with pytest.raises(BadTimestampValueReturned):
        for log_result in read_events(
            web3,
            start_block,
            end_block,
            events,
            None,
            chunk_size=1000,
            context=None,
            extract_timestamps=_extract_timestamps_2,
        ):
            out.append(log_result)