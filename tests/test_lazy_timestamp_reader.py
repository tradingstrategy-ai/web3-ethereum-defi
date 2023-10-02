"""Tests for lazy_timestamp_reader.py

"""
import pytest
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
    provider.middlewares = (
        #    attrdict_middleware,
        # default_transaction_fields_middleware,
        # ethereum_tester_middleware,
    )

    web3 = Web3(provider)
    # Get rid of attributeddict slow down
    web3.middleware_onion.clear()
    install_chain_middleware(web3)
    return web3


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
