"""Event reader test coverage."""
import os

import pytest
import requests
from requests.adapters import HTTPAdapter
from web3 import Web3, HTTPProvider

from eth_defi.abi import get_contract
from eth_defi.chain import install_chain_middleware
from eth_defi.event_reader.lazy_timestamp_reader import extract_timestamps_json_rpc_lazy, LazyTimestampContainer
from eth_defi.event_reader.reader import read_events, BadTimestampValueReturned, TimestampNotFound, read_events_concurrent
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.event_reader.web3worker import create_thread_pool_executor


JSON_RPC_POLYGON = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")


@pytest.fixture()
def web3():
    """Live Polygon web3 instance."""

    # HTTP 1.1 keep-alive
    session = requests.Session()

    web3 = Web3(HTTPProvider(JSON_RPC_POLYGON, session=session))

    web3.middleware_onion.clear()

    # Enable faster ujson reads
    install_chain_middleware(web3)

    return web3


def test_read_events_bad_timestamps(web3):
    """Reading fails with a bad timestamp provider."""

    # Get contracts
    Factory = get_contract(web3, "sushi/UniswapV2Factory.json")

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


def test_read_events_two_blocks(web3):
    """Read events exactly for two blocks to check off by one errors.

    Read live node over exact 2 blocks range.
    """

    # Get contracts
    Pair = get_contract(web3, "sushi/UniswapV2Pair.json")

    events = [
        Pair.events.Swap,
    ]

    start_block = 37898275
    end_block = 37898276

    swaps = list(
        read_events(
            web3,
            start_block,
            end_block,
            events,
            chunk_size=1000,
        )
    )

    # Check that we get 3 events over 2 blocks
    blocks = [s["blockNumber"] for s in swaps]
    assert len(blocks) == 3
    assert min(blocks) == 37898275
    assert max(blocks) == 37898276


def test_read_events_concurrent_two_blocks_concurrent(web3):
    """Read events exactly for two blocks to check off by one errors using the concurrent reader.

    Read live node over exact 2 blocks range.
    """

    # Get contracts
    Pair = get_contract(web3, "sushi/UniswapV2Pair.json")

    events = [
        Pair.events.Swap,
    ]

    start_block = 37898275
    end_block = 37898276

    threads = 16
    http_adapter = HTTPAdapter(pool_connections=threads, pool_maxsize=threads)
    web3_factory = TunedWeb3Factory(web3.provider.endpoint_uri, http_adapter)
    executor = create_thread_pool_executor(web3_factory, context=None, max_workers=threads)

    swaps = list(
        read_events_concurrent(
            executor,
            start_block,
            end_block,
            events,
            chunk_size=1000,
        )
    )

    # Check that we get 3 events over 2 blocks
    blocks = [s["blockNumber"] for s in swaps]
    assert len(blocks) == 3
    assert min(blocks) == 37898275
    assert max(blocks) == 37898276


def test_read_events_lazy_timestamp(web3):
    """Read events but extract timestamps only for events, not whole block ranges."""

    # Get contracts
    Pair = get_contract(web3, "sushi/UniswapV2Pair.json")

    events = [
        Pair.events.Swap,
    ]

    start_block = 37898275
    end_block = start_block + 100
    lazy_timestamp_container: LazyTimestampContainer = None

    def wrapper(web3, start_block, end_block):
        nonlocal lazy_timestamp_container
        lazy_timestamp_container = extract_timestamps_json_rpc_lazy(web3, start_block, end_block)
        return lazy_timestamp_container

    swaps = list(
        read_events(
            web3,
            start_block,
            end_block,
            events,
            chunk_size=1000,
            extract_timestamps=wrapper,
        )
    )

    # API calls are less often than blocks we read
    assert lazy_timestamp_container.api_call_counter == 80
    assert len(swaps) == 206

    for s in swaps:
        assert s["timestamp"] > 0
