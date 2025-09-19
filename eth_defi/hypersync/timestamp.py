import asyncio
from typing import Iterable
import logging

from eth_typing import BlockNumber

import hypersync
from hypersync import BlockField

from eth_defi.event_reader.block_header import BlockHeader
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
