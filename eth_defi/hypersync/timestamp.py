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
from typing import AsyncIterable
import logging

import pandas as pd
from eth_typing import BlockNumber

import hypersync
from hypersync import BlockField

from tqdm_loggable.auto import tqdm

from eth_defi.event_reader.block_header import BlockHeader
from eth_defi.event_reader.timestamp_cache import load_timestamp_cache, BlockTimestampDatabase, DEFAULT_TIMESTAMP_CACHE_FOLDER, BlockTimestampSlicer
from eth_defi.utils import from_unix_timestamp

logger = logging.getLogger(__name__)


async def get_block_timestamps_using_hypersync_async(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    timeout: float = 30.0,
    display_progress: bool = True,
    progress_throttle=10_000,
) -> AsyncIterable[BlockHeader]:
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
    assert end_block >= start_block, f"end_block {end_block} must be >= start_block {start_block}"

    connected_chain_id = await client.get_chain_id()
    assert chain_id == connected_chain_id, f"Connected to chain {connected_chain_id}, but expected {chain_id}"

    if display_progress:
        progress_bar = tqdm(
            total=(end_block - start_block),
            desc=f"Reading timestamps (hypersync) on {chain_id}: {start_block:,} - {end_block:,}",
            unit_scale=True,  # enable k/M formatting for {n_fmt}/{total_fmt}
            unit_divisor=1000,  # use 1000-based units
        )
    else:
        progress_bar = None

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
        except asyncio.TimeoutError as e:
            logger.error("HyperSync receiver timed out, cannot recover")
            raise RuntimeError(f"Cannot recover from HyperSync stream timeout after {timeout} seconds") from e

        # exit if the stream finished
        if res is None:
            break

        for progress_update_idx, block in enumerate(res.data.blocks):
            timestamp = int(block.timestamp, 16)
            yield BlockHeader(
                block_number=block.number,
                block_hash=block.hash,
                timestamp=timestamp,
            )

            if progress_bar:
                if progress_update_idx % progress_throttle == 0:
                    progress_bar.update(len(res.data.blocks))
                    utc_timestamp = from_unix_timestamp(timestamp)
                    progress_bar.set_postfix(
                        {
                            "timestamp": utc_timestamp,
                            "block": f"{block.number:,}",
                        }
                    )

    if progress_bar:
        progress_bar.close()


def get_block_timestamps_using_hypersync(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    display_progress: bool = True,
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
            display_progress=display_progress,
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


async def fetch_block_timestamps_using_hypersync_cached_async(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    cache_path=DEFAULT_TIMESTAMP_CACHE_FOLDER,
    display_progress: bool = True,
    checkpoint_freq: int = 1_250_000_000,
) -> BlockTimestampSlicer:
    """Quickly get block timestamps using Hypersync API and a local cache file.

    - Ultra fast, used optimised Hypersync streaming and DuckDB local cache.

    :return:
        Block number -> datetime mapping
    """

    if cache_path.exists():
        timestamp_db = load_timestamp_cache(chain_id, cache_path)
    else:
        timestamp_db = BlockTimestampDatabase.create(chain_id, cache_path)

    first_read_block, last_read_block = timestamp_db.get_first_and_last_block()

    logger.info(f"Timestamp cache {cache_path} for chain {chain_id}: blocks {first_read_block} - {last_read_block}")

    if last_read_block:
        # Check the range we need to map out, we might ask earlier blocks than before
        if start_block < first_read_block:
            scan_start = start_block
        else:
            scan_start = last_read_block
    else:
        scan_start = start_block

    logger.info(f"Adjusted timestamp scan range for chain {chain_id}: blocks {scan_start} - {end_block}")

    def _save():
        series = pd.Series(data=values, index=index)
        timestamp_db.import_chain_data(
            chain_id,
            series,
        )

    # Check if we have anything to read
    checkpoint_count = 0

    index = []
    values = []

    if end_block > last_read_block or start_block < first_read_block:
        iter = get_block_timestamps_using_hypersync_async(
            client,
            chain_id,
            start_block=scan_start,
            end_block=end_block,
            display_progress=display_progress,
        )

        async for block_header in iter:
            index.append(block_header.block_number)
            values.append(block_header.timestamp)

            # result[block_header.block_number] = pd.to_datetime(block_header.timestamp, unit="s")
            checkpoint_count += 1

            if checkpoint_count % checkpoint_freq == 0:
                _save()
                # Reset buffer
                index = []
                values = []

        _save()

    # Drop unnecessary blocks from memory
    return timestamp_db.get_slicer()


def fetch_block_timestamps_using_hypersync_cached(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    cache_path=DEFAULT_TIMESTAMP_CACHE_FOLDER,
    display_progress: bool = True,
) -> BlockTimestampSlicer:
    """Sync wrapper.

    See :py:func:`fetch_block_timestamps_using_hypersync_cached_async` for documentation.
    """

    async def _hypersync_asyncio_wrapper():
        return await fetch_block_timestamps_using_hypersync_cached_async(
            client=client,
            chain_id=chain_id,
            start_block=start_block,
            end_block=end_block,
            cache_path=cache_path,
            display_progress=display_progress,
        )

    return asyncio.run(_hypersync_asyncio_wrapper())
