"""Block timestamp and hash bulk loading using Hypersync API.

Replace slow and expensive ``eth_getBlockByNumber`` calls with Hypersync API.

Example:

.. code-block:: python

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

"""

import asyncio
import datetime
from typing import Iterable
import logging

from eth_typing import BlockNumber

import hypersync
from hypersync import BlockField

from eth_defi.event_reader.block_header import BlockHeader
from eth_defi.event_reader.multicall_timestamp import load_timestamp_cache, DEFAULT_TIMESTAMP_CACHE_FILE, ChainBlockTimestampMap, save_timestamp_cache
from eth_defi.utils import from_unix_timestamp

logger = logging.getLogger(__name__)


async def get_block_timestamps_using_hypersync_async(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    timeout: float = 30.0,
) -> Iterable[BlockHeader]:
    """Read block timestamps using Hypersync API.

    Instead of hammering `eth_getBlockByNumber` JSON-RPC endpoint, we can
    get block timestamps using Hypersync API 1000x faster.

    :param chain_id:
        Verify HyperSync client is connected to the correct chain ID.

        (Not actually used in request because client is per-chain)

    :param start_block:
        Start block, inclusive

    :param end_block:
        End block, inclusive

    :param client:
        Hypersync client to use

    """

    assert isinstance(client, hypersync.HypersyncClient), f"Expected HypersyncClient, got {type(client)}"
    assert type(chain_id) == int
    assert start_block >= 0
    assert end_block >= start_block

    connected_chain_id = await client.get_chain_id()
    assert chain_id == connected_chain_id, f"Connected to chain {connected_chain_id}, but expected {chain_id}"

    # The query to run
    query = hypersync.Query(
        from_block=start_block,
        to_block=end_block + 1,  # Inclusive
        logs=[{}],  # Empty log selection to ensure we get block data
        include_all_blocks=True,
        field_selection=hypersync.FieldSelection(
            block=[
                BlockField.NUMBER,
                BlockField.TIMESTAMP,
                BlockField.HASH,
            ],
        ),
    )

    receiver = await client.stream(query, hypersync.StreamConfig())

    while True:
        try:
            res = await asyncio.wait_for(receiver.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("HyperSync receiver timed out")
            break  # or handle as appropriate

        # exit if the stream finished
        if res is None:
            break

        for block in res.data.blocks:
            assert block.hash.startswith("0x")
            yield BlockHeader(
                block_number=block.number,
                block_hash=block.hash,
                timestamp=int(block.timestamp, 16),
            )


def get_block_timestamps_using_hypersync(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
) -> dict[BlockNumber, BlockHeader]:
    """Quickly get block timestamps using Hypersync API.

    Wraps :py:func:`get_block_timestamps_using_hypersync_async`.

    You want to use :py:func:`fetch_block_timestamps_using_hypersync_cached` cached version.

    :return:
        Block number -> header mapping
    """

    # Don't leak async colored interface, as it is an implementation detail
    async def _hypersync_asyncio_wrapper():
        iter = get_block_timestamps_using_hypersync_async(
            client,
            chain_id,
            start_block,
            end_block,
        )
        return {v.block_number: v async for v in iter}

    result = asyncio.run(_hypersync_asyncio_wrapper())

    # Crash in the case Hypersync is not syncing
    # for i in range(start_block, end_block + 1):
    #    assert i in result, f"Did not get block {i}, we got {result}"

    # assert end_block in result, f"Did not get end block {end_block}, we got {result}"
    return result


def get_hypersync_block_height(
    client: hypersync.HypersyncClient,
) -> int:
    """Get the latest block known to Hypersync.

    Wrapped around the async function.
    """

    async def _hypersync_asyncio_wrapper():
        return await client.get_height()

    return asyncio.run(_hypersync_asyncio_wrapper())


def fetch_block_timestamps_using_hypersync_cached(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    cache_file=DEFAULT_TIMESTAMP_CACHE_FILE,
) -> dict[int, datetime.datetime]:
    """Quickly get block timestamps using Hypersync API and a local cache file.

    :return:
        Block number -> datetime mapping
    """

    existing_data = load_timestamp_cache(cache_file)

    result: ChainBlockTimestampMap = existing_data
    result[chain_id] = result.get(chain_id, {})

    last_read_block = max(result[chain_id].keys(), default=start_block)

    block_to_timestamp = get_block_timestamps_using_hypersync(
        client,
        chain_id,
        start_block=last_read_block,
        end_block=end_block,
    )

    for block_number, block_header in block_to_timestamp.items():
        result[chain_id][block_number] = block_header.timestamp_as_datetime

    save_timestamp_cache(result, cache_file)

    return result[chain_id]
