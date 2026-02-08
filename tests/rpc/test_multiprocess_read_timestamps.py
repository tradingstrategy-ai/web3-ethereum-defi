import datetime
import os

import flaky
import pytest

from eth_defi.event_reader.multicall_timestamp import fetch_block_timestamps_multiprocess_auto_backend
from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
from eth_defi.event_reader.web3factory import Web3Factory
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory

JSON_RPC_ETHEREUM = os.getenv("JSON_RPC_ETHEREUM")
JSON_RPC_POLYGON = os.getenv("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(not JSON_RPC_ETHEREUM or not JSON_RPC_POLYGON, reason="Set JSON_RPC_ETHEREUM and JSON_RPC_POLYGON environment variables to run this test")


@pytest.fixture()
def web3_ethereum_factory() -> Web3Factory:
    return MultiProviderWeb3Factory(JSON_RPC_ETHEREUM)


@pytest.fixture()
def web3_polygon_factory() -> Web3Factory:
    return MultiProviderWeb3Factory(JSON_RPC_POLYGON)


@flaky.flaky(max_runs=3)
def test_get_block_timestamps_using_multiprocess_cached(web3_ethereum_factory, web3_polygon_factory, tmp_path):
    """We get 100 historical blocks using our poor multiprocess reader"""

    cache_path = tmp_path

    blocks = fetch_block_timestamps_multiprocess_auto_backend(
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
        cache_path=cache_path,
        web3factory=web3_ethereum_factory,
        step=1,
    )
    cache_file = BlockTimestampDatabase.get_database_file_chain(1, cache_path)
    assert cache_file.exists()
    # Blocks missing if they do not contain transactions
    # E.g https://etherscan.io/block/10000007
    assert len(blocks) == 101
    timestamp = blocks[10_000_100]
    assert timestamp == datetime.datetime(2020, 5, 4, 13, 45, 31)
    blocks.close()

    # Run again with warm cache
    blocks = fetch_block_timestamps_multiprocess_auto_backend(
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
        cache_path=cache_path,
        web3factory=web3_ethereum_factory,
        step=1,
    )
    assert len(blocks) == 101
    timestamp = blocks[10_000_100]
    assert timestamp == datetime.datetime(2020, 5, 4, 13, 45, 31)
    blocks.close()

    # One more time with auto endpoint
    blocks = fetch_block_timestamps_multiprocess_auto_backend(
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
        cache_path=cache_path,
        web3factory=web3_polygon_factory,
        step=10,
    )
    assert len(blocks) == 101
    timestamp = blocks[10_000_100]
    assert timestamp == datetime.datetime(2020, 5, 4, 13, 45, 31)
    blocks.close()
