"""Fetch block timestamp from hypersync."""

import datetime
import os

import pandas as pd
import pytest
import duckdb


from eth_defi.event_reader.multicall_timestamp import fetch_block_timestamps_multiprocess_auto_backend
from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
from eth_defi.event_reader.web3factory import SimpleWeb3Factory
from eth_defi.hypersync.server import get_hypersync_server

from hypersync import HypersyncClient, ClientConfig

from eth_defi.hypersync.hypersync_timestamp import get_block_timestamps_using_hypersync, get_hypersync_block_height, fetch_block_timestamps_using_hypersync_cached
from eth_defi.hypersync.session import create_throttled_hypersync_client, ThrottledHypersyncClient

HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")

pytestmark = pytest.mark.skipif(not HYPERSYNC_API_KEY, reason="Set HYPERSYNC_API_KEY environment variable to run this test")


@pytest.fixture()
def hypersync_client() -> HypersyncClient:
    hypersync_url = get_hypersync_server(1)  # Mainnet
    client = HypersyncClient(ClientConfig(url=hypersync_url, api_token=HYPERSYNC_API_KEY))
    return client


@pytest.fixture()
def hypersync_polygon_client() -> HypersyncClient:
    hypersync_url = get_hypersync_server(137)
    client = HypersyncClient(ClientConfig(url=hypersync_url, api_token=HYPERSYNC_API_KEY))
    return client


def test_get_block_timestamps_using_hypersync(hypersync_client: HypersyncClient):
    """We get 100 historical blocks from Hypersync.

    - Do not use cache
    """

    assert get_hypersync_block_height(hypersync_client) > 10_000_000
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


def test_get_block_timestamps_using_hypersync_cached(hypersync_client: HypersyncClient, tmp_path):
    """We get 100 historical blocks from Hypersync"""

    assert get_hypersync_block_height(hypersync_client) > 10_000_000

    cache_path = tmp_path

    blocks = fetch_block_timestamps_using_hypersync_cached(
        hypersync_client,
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
        cache_path=cache_path,
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
    blocks = fetch_block_timestamps_using_hypersync_cached(
        hypersync_client,
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
        cache_path=cache_path,
    )
    assert len(blocks) == 101
    timestamp = blocks[10_000_100]
    assert timestamp == datetime.datetime(2020, 5, 4, 13, 45, 31)
    blocks.close()

    # One more time with auto endpoint
    blocks = fetch_block_timestamps_multiprocess_auto_backend(
        hypersync_client=hypersync_client,
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
        cache_path=cache_path,
        web3factory=SimpleWeb3Factory(None),
        step=10,
    )
    assert len(blocks) == 101
    timestamp = blocks[10_000_100]
    assert timestamp == datetime.datetime(2020, 5, 4, 13, 45, 31)
    blocks.close()


def test_get_block_timestamps_using_hypersync_cached_multichain(hypersync_client: HypersyncClient, hypersync_polygon_client: HypersyncClient, tmp_path):
    """We get 100 historical blocks from Hypersync, multiple chains"""

    assert get_hypersync_block_height(hypersync_client) > 10_000_000
    cache_path = tmp_path
    blocks_ethereum = fetch_block_timestamps_using_hypersync_cached(
        hypersync_client,
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
        cache_path=cache_path,
    )

    cache_file = BlockTimestampDatabase.get_database_file_chain(1, cache_path)
    assert cache_file.exists()
    # Blocks missing if they do not contain transactions
    # E.g https://etherscan.io/block/10000007
    assert len(blocks_ethereum) == 101
    timestamp = blocks_ethereum[10_000_100]
    assert timestamp == datetime.datetime(2020, 5, 4, 13, 45, 31)
    blocks_ethereum.close()

    # Read cached
    blocks_ethereum_again = fetch_block_timestamps_using_hypersync_cached(
        hypersync_client,
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
        cache_path=cache_path,
    )
    assert len(blocks_ethereum_again) == 101
    timestamp = blocks_ethereum_again[10_000_100]
    assert timestamp == datetime.datetime(2020, 5, 4, 13, 45, 31)
    blocks_ethereum_again.close()

    # Read another chain to the same database
    blocks_polygon = fetch_block_timestamps_using_hypersync_cached(
        hypersync_polygon_client,
        chain_id=137,
        start_block=10_000_000,
        end_block=10_000_100,
        cache_path=cache_path,
    )
    assert len(blocks_polygon) == 101
    timestamp = blocks_polygon[10_000_100]
    assert timestamp == pd.Timestamp("2021-01-24 22:32:30")
    cache_file = BlockTimestampDatabase.get_database_file_chain(137, cache_path)
    assert cache_file.exists()
    blocks_polygon.close()


def test_get_block_timestamps_using_hypersync_continue_cache(hypersync_client: HypersyncClient, hypersync_polygon_client: HypersyncClient, tmp_path):
    """Get blocks and then get some more blocks"""

    assert get_hypersync_block_height(hypersync_client) > 10_000_000

    cache_path = tmp_path

    blocks_ethereum = fetch_block_timestamps_using_hypersync_cached(
        hypersync_client,
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
        cache_path=cache_path,
    )

    cache_file = BlockTimestampDatabase.get_database_file_chain(1, cache_path)

    # CHeck we wrote data
    assert cache_file.exists()
    db = duckdb.connect(cache_file)
    assert len(db.sql("SHOW TABLES")) == 1
    df = db.sql("SELECT * FROM block_timestamps").df()
    assert len(df) == 101  # 101 per chain

    # Blocks missing if they do not contain transactions
    # E.g https://etherscan.io/block/10000007
    assert len(blocks_ethereum) == 101
    blocks_ethereum.close()

    # Read More than we have, after
    blocks_ethereum_again = fetch_block_timestamps_using_hypersync_cached(
        hypersync_client,
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_200,
        cache_path=cache_path,
    )
    assert len(blocks_ethereum_again) == 201
    blocks_ethereum_again.close()

    # Read More than we have, before
    blocks_ethereum_again = fetch_block_timestamps_using_hypersync_cached(
        hypersync_client,
        chain_id=1,
        start_block=9_999_900,
        end_block=10_000_200,
        cache_path=cache_path,
    )
    assert len(blocks_ethereum_again) == 301

    blocks_ethereum_again.close()


def test_timestamp_multi_save(hypersync_client: HypersyncClient, hypersync_polygon_client: HypersyncClient, tmp_path):
    """Get blocks and then get some more blocks, do several saves"""

    assert get_hypersync_block_height(hypersync_client) > 10_000_000

    cache_path = tmp_path

    blocks_ethereum = fetch_block_timestamps_using_hypersync_cached(
        hypersync_client,
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
        cache_path=cache_path,
    )

    # Blocks missing if they do not contain transactions
    # E.g https://etherscan.io/block/10000007
    assert len(blocks_ethereum) == 101
    blocks_ethereum.close()

    # Read More than we have, after
    blocks_ethereum_again = fetch_block_timestamps_using_hypersync_cached(
        hypersync_client,
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_200,
        cache_path=cache_path,
    )
    assert len(blocks_ethereum_again) == 201
    blocks_ethereum_again.close()

    # Read More than we have, before
    blocks_ethereum_again = fetch_block_timestamps_using_hypersync_cached(
        hypersync_client,
        chain_id=1,
        start_block=9_999_900,
        end_block=10_000_200,
        cache_path=cache_path,
    )
    assert len(blocks_ethereum_again) == 301
    blocks_ethereum_again.close()


def test_stream_with_tuning_parameters():
    """Verify that all StreamConfig tuning parameters are accepted by hypersync 1.1.

    1. Create a ThrottledHypersyncClient with all tuning parameters set
    2. Run a small query (100 blocks on Ethereum mainnet)
    3. Assert expected results — verifies no spelling errors or type mismatches
    4. Also test create_stream_config() directly
    """

    # 1. Create client with all tuning parameters
    hypersync_url = get_hypersync_server(1)
    client = create_throttled_hypersync_client(
        ClientConfig(url=hypersync_url, bearer_token=HYPERSYNC_API_KEY),
        concurrency=5,
        batch_size=500,
        response_bytes_ceiling=500_000,
        response_bytes_floor=200_000,
        min_batch_size=100,
        max_batch_size=10_000,
    )

    # 2. Verify create_stream_config() produces correct StreamConfig
    config = client.create_stream_config()
    assert config.concurrency == 5
    assert config.batch_size == 500
    assert config.response_bytes_ceiling == 500_000
    assert config.response_bytes_floor == 200_000
    assert config.min_batch_size == 100
    assert config.max_batch_size == 10_000

    # 3. Verify overrides work
    override_config = client.create_stream_config(concurrency=30)
    assert override_config.concurrency == 30
    assert override_config.batch_size == 500  # still from stored params

    # 4. Run a small query to verify end-to-end
    blocks = get_block_timestamps_using_hypersync(
        client,
        chain_id=1,
        start_block=10_000_000,
        end_block=10_000_100,
    )
    assert len(blocks) == 101

    block = blocks[10_000_100]
    assert block.block_number == 10_000_100
    assert block.timestamp_as_datetime == datetime.datetime(2020, 5, 4, 13, 45, 31)
