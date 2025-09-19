"""Fetch block timestamp from hypersync."""

import datetime

import pytest

from eth_defi.hypersync.server import get_hypersync_server

from hypersync import HypersyncClient, ClientConfig

from eth_defi.hypersync.timestamp import get_block_timestamps_using_hypersync


@pytest.fixture()
def hypersync_client() -> HypersyncClient:
    hypersync_url = get_hypersync_server(1)  # Mainnet
    client = HypersyncClient(ClientConfig(url=hypersync_url))
    return client


def test_get_block_timestamps_using_hypersync(hypersync_client: HypersyncClient):
    blocks = get_block_timestamps_using_hypersync(
        hypersync_client,
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
    )

    # Blocks missing if they do not contain transactions
    # E.g https://etherscan.io/block/10000007
    assert len(blocks) == 101

    block = blocks[10_000_100]

    assert block.block_number == 10_000_100
    assert block.block_hash == "0x427b4ae39316c0df7ba6cd61a96bf668eff6e3ec01213b0fbc74f9b7a0726e7b"
    assert block.timestamp_as_datetime == datetime.datetime(2020, 5, 4, 13, 45, 31)
