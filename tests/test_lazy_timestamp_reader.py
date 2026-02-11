"""Tests for lazy_timestamp_reader.py"""

import pytest

from eth_defi.compat import clear_middleware
from eth_defi.provider.anvil import launch_anvil, AnvilLaunch, mine
from eth_defi.chain import install_chain_middleware
from web3 import HTTPProvider, Web3

from eth_defi.event_reader.lazy_timestamp_reader import extract_timestamps_json_rpc_lazy, LazyTimestampContainer, OutOfSpecifiedRangeRead


@pytest.fixture()
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend."""

    anvil = launch_anvil()
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.

    Also perform the Anvil state reset for each test.
    """

    provider = HTTPProvider(anvil.json_rpc_url)

    # Web3 6.0 fixes - force no middlewares
    # provider.middlewares = (
    #     #    attrdict_middleware,
    #     # default_transaction_fields_middleware,
    #     # ethereum_tester_middleware,
    # )
    clear_middleware(provider)

    web3 = Web3(provider)
    # Clear all middleware from web3 instance
    clear_middleware(web3)

    install_chain_middleware(web3)
    return web3


def debug_web3_setup(web3: Web3):
    """Debug what's happening with the web3 setup"""
    pass


def test_lazy_timestamp_reader_block_range_debug(web3: Web3):
    """Debug version of the timestamp reader test."""

    # Debug initial state
    debug_web3_setup(web3)

    # Create some blocks
    for i in range(1, 5 + 1):
        mine(web3)
        print(f"Mined block {i}, current block number: {web3.eth.block_number}")

    assert web3.eth.block_number == 5

    # Debug blocks
    for i in range(1, 6):
        try:
            block = web3.eth.get_block(i)
            print(f"Block {i}: hash={block['hash'].hex()}, timestamp={block['timestamp']}")
        except Exception as e:
            print(f"Error reading block {i}: {e}")

    timestamps = extract_timestamps_json_rpc_lazy(web3, 1, 5)
    assert isinstance(timestamps, LazyTimestampContainer)

    for i in range(1, 5 + 1):
        block_hash = web3.eth.get_block(i)["hash"]
        print(f"Checking timestamp for block {i}, hash: {block_hash.hex()}")
        try:
            timestamp = timestamps[block_hash]
            print(f"Got timestamp: {timestamp}")
            assert timestamp > 0
        except Exception as e:
            print(f"Error getting timestamp for block {i}: {e}")
            raise


def test_lazy_timestamp_reader_block_range(web3: Web3):
    """Read timestamps lazily."""

    # Create some blocks
    for i in range(1, 5 + 1):
        mine(web3)

    assert web3.eth.block_number == 5
    timestamps = extract_timestamps_json_rpc_lazy(web3, 1, 5)
    assert isinstance(timestamps, LazyTimestampContainer)

    for i in range(1, 5 + 1):
        block_hash = web3.eth.get_block(i)["hash"]
        assert timestamps[block_hash] > 0


def test_lazy_timestamp_reader_out_of_block_range(web3: Web3):
    """Read timestamps lazily, but peek out of allowed range."""

    # Create some blocks
    for i in range(1, 5 + 1):
        mine(web3)

    assert web3.eth.block_number == 5
    timestamps = extract_timestamps_json_rpc_lazy(web3, 1, 4)
    assert isinstance(timestamps, LazyTimestampContainer)

    with pytest.raises(OutOfSpecifiedRangeRead):
        block_hash = web3.eth.get_block(5)["hash"]
        timestamps[block_hash]
