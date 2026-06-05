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
from eth_defi.hypersync.session import is_hypersync_client
from eth_defi.utils import from_unix_timestamp

logger = logging.getLogger(__name__)


class HypersyncFlaky(Exception):
    """Hypersync stream flaky error, e.g. timeout or rate limit."""


def _is_hypersync_rate_limit_error(e: Exception) -> bool:
    """Check if a Hypersync RuntimeError is a 429 rate limit error.

    The Rust client raises ``RuntimeError`` with the HTTP status in the message
    after exhausting its internal retries.
    """
    return isinstance(e, RuntimeError) and "429" in str(e)


async def _validate_hypersync_chain_id_async(
    client: hypersync.HypersyncClient,
    expected_chain_id: int,
    reason: str | None = None,
) -> None:
    """Validate that the Hypersync client is connected to the expected chain.

    Guards against poisoning persistent caches with wrong-chain data.
    Wraps 429 errors as :py:class:`HypersyncFlaky` so the caller's
    retry/backoff loop handles rate limits.
    """
    reason_suffix = f" [{reason}]" if reason else ""
    try:
        connected = await client.get_chain_id()
    except RuntimeError as e:
        if _is_hypersync_rate_limit_error(e):
            raise HypersyncFlaky(f"Hypersync rate limited during chain_id validation{reason_suffix}: {e}") from e
        raise
    assert connected == expected_chain_id, f"Hypersync client connected to chain {connected}, but expected {expected_chain_id}"


async def get_block_timestamps_using_hypersync_async(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    timeout: float = 120.0,
    display_progress: bool = True,
    progress_throttle=10_000,
    validate_chain_id: bool = True,
    reason: str | None = None,
) -> AsyncIterable[BlockHeader]:
    """Read block timestamps using Hypersync API.

    Instead of hammering ``eth_getBlockByNumber`` JSON-RPC endpoint, we can
    get block timestamps using Hypersync API 1000x faster.

    :param chain_id:
        Expected chain ID. Validated against the client unless
        ``validate_chain_id`` is ``False``.

    :param start_block:
        Start block, inclusive

    :param end_block:
        End block, inclusive

    :param client:
        Hypersync client to use

    :param validate_chain_id:
        When ``True`` (default), verify the client is connected to
        the expected chain before streaming. Set to ``False`` when the
        caller has already validated (e.g. the cached path).

    :param reason:
        Human-readable label for this request, included in log and
        error messages to help track which caller is consuming API quota.

    """

    assert is_hypersync_client(client), f"Expected HypersyncClient or ThrottledHypersyncClient, got {type(client)}"
    assert type(chain_id) == int
    assert start_block >= 0
    assert end_block >= start_block, f"end_block {end_block} must be >= start_block {start_block}"

    reason_suffix = f" [{reason}]" if reason else ""

    if validate_chain_id:
        await _validate_hypersync_chain_id_async(client, chain_id, reason=reason)

    logger.info(
        "Hypersync stream open: chain %d, blocks %d-%d (%d blocks)%s",
        chain_id,
        start_block,
        end_block,
        end_block - start_block,
        reason_suffix,
    )

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
        logs=[hypersync.LogSelection()],  # Empty log selection to ensure we get block data
        include_all_blocks=True,
        field_selection=hypersync.FieldSelection(
            block=[
                BlockField.NUMBER,
                BlockField.TIMESTAMP,
                BlockField.HASH,
            ],
        ),
    )

    try:
        receiver = await client.stream(query, hypersync.StreamConfig())
    except RuntimeError as e:
        if _is_hypersync_rate_limit_error(e):
            raise HypersyncFlaky(f"Hypersync rate limited during stream setup{reason_suffix}: {e}") from e
        raise

    while True:
        try:
            res = await asyncio.wait_for(receiver.recv(), timeout=timeout)
        except asyncio.TimeoutError as e:
            logger.error("HyperSync receiver timed out%s, cannot recover", reason_suffix)
            raise HypersyncFlaky(f"Cannot recover from HyperSync stream timeout after {timeout} seconds{reason_suffix}") from e
        except RuntimeError as e:
            if _is_hypersync_rate_limit_error(e):
                raise HypersyncFlaky(f"Hypersync rate limited during streaming{reason_suffix}: {e}") from e
            raise

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
        logger.info("Hypersync API call: get_height [block-height-check]")
        try:
            return await client.get_height()
        except RuntimeError as e:
            if _is_hypersync_rate_limit_error(e):
                raise HypersyncFlaky(f"Hypersync rate limited [block-height-check]: {e}") from e
            raise

    return asyncio.run(_hypersync_asyncio_wrapper())


async def fetch_block_timestamps_using_hypersync_cached_async(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    cache_path=DEFAULT_TIMESTAMP_CACHE_FOLDER,
    display_progress: bool = True,
    checkpoint_freq: int = 1_250_000_000,
    chunk_size: int = 100_000,
) -> BlockTimestampSlicer:
    """Quickly get block timestamps using Hypersync API and a local cache file.

    - Ultra fast, used optimised Hypersync streaming and DuckDB local cache.
    - Large ranges are split into chunks of *chunk_size* blocks so that
      each chunk opens a separate Hypersync ``stream()`` call.  This keeps
      individual requests small, lets the Python-side rate limiter pace
      them, and — crucially — saves progress after each chunk so that a
      429 failure only loses the current chunk, not all prior work.

    :param chunk_size:
        Maximum number of blocks per Hypersync streaming request.
        Defaults to 100 000 (~2 days on Polygon, ~3 days on Binance).

    :return:
        Block number -> datetime mapping
    """

    logger.info(
        "Timestamp cache fill requested for chain %d: caller wants blocks %d - %d, cache_path=%s, chunk_size=%d",
        chain_id,
        start_block,
        end_block,
        cache_path,
        chunk_size,
    )

    if cache_path.exists():
        timestamp_db = load_timestamp_cache(chain_id, cache_path)
    else:
        logger.info("Timestamp cache does not exist yet for chain %d, creating new database at %s", chain_id, cache_path)
        timestamp_db = BlockTimestampDatabase.create(chain_id, cache_path)

    first_read_block, last_read_block = timestamp_db.get_first_and_last_block()
    cached_count = timestamp_db.get_count()

    logger.info(
        "Timestamp cache state for chain %d: cached blocks %s - %s (%s entries in DB)",
        chain_id,
        f"{first_read_block:,}" if first_read_block else "None",
        f"{last_read_block:,}" if last_read_block else "None",
        f"{cached_count:,}" if cached_count else "0",
    )

    # Build a list of (range_start, range_end) pairs to fetch.
    # We compute head and tail ranges separately so that a partial
    # backfill before an existing cache doesn't leave permanent holes.
    # Using a single scan_start derived from MIN/MAX would miss interior
    # gaps created when a head-backfill chunk saves, then a 429 kills
    # the stream — on retry MIN/MAX would look fully covered.
    fetch_ranges: list[tuple[int, int]] = []

    if last_read_block:
        needs_head = start_block < first_read_block
        needs_tail = end_block > last_read_block

        if needs_head:
            head_end = first_read_block - 1
            fetch_ranges.append((start_block, head_end))
            logger.info(
                "Chain %d: head backfill needed — blocks %s - %s (%s blocks before existing cache)",
                chain_id,
                f"{start_block:,}",
                f"{head_end:,}",
                f"{head_end - start_block + 1:,}",
            )

        if needs_tail:
            tail_start = last_read_block + 1
            fetch_ranges.append((tail_start, end_block))
            logger.info(
                "Chain %d: tail append needed — blocks %s - %s (%s new blocks after cache)",
                chain_id,
                f"{tail_start:,}",
                f"{end_block:,}",
                f"{end_block - tail_start + 1:,}",
            )

        # Check for interior gaps that overlap the requested range.
        # A partial head-backfill followed by a 429 can leave holes
        # (e.g. cache has 1-99 + 1000-2000, blocks 100-999 are missing).
        # MIN/MAX look fully covered but find_gaps() detects the holes.
        # Gaps may only partially overlap the requested range, so we clip
        # them to the intersection rather than requiring full containment.
        interior_gaps = timestamp_db.find_gaps()
        for gap_start, gap_end, gap_size in interior_gaps:
            # gap_start/gap_end are the boundary blocks that DO exist;
            # the missing blocks are gap_start+1 .. gap_end-1
            clip_start = max(start_block, gap_start + 1)
            clip_end = min(end_block, gap_end - 1)
            if clip_start <= clip_end:
                fetch_ranges.append((clip_start, clip_end))
                logger.info(
                    "Chain %d: interior gap detected — blocks %s - %s (%s missing blocks, clipped from full gap %s - %s)",
                    chain_id,
                    f"{clip_start:,}",
                    f"{clip_end:,}",
                    f"{clip_end - clip_start + 1:,}",
                    f"{gap_start + 1:,}",
                    f"{gap_end - 1:,}",
                )
        if not fetch_ranges:
            logger.info(
                "Chain %d: cache fully covers requested range %s - %s (cache has %s - %s), no gaps",
                chain_id,
                f"{start_block:,}",
                f"{end_block:,}",
                f"{first_read_block:,}",
                f"{last_read_block:,}",
            )
    else:
        fetch_ranges.append((start_block, end_block))
        logger.info("Chain %d: empty cache, fetching full range %s - %s", chain_id, f"{start_block:,}", f"{end_block:,}")

    total_blocks_to_fetch = sum(e - s + 1 for s, e in fetch_ranges)

    logger.info(
        "Timestamp fetch plan for chain %d: %d range(s), %s total blocks to fetch",
        chain_id,
        len(fetch_ranges),
        f"{total_blocks_to_fetch:,}",
    )

    if fetch_ranges:
        # Validate chain_id only when we actually need to fetch from Hypersync.
        # Skipped on warm cache hits to avoid wasting API quota.
        if is_hypersync_client(client):
            await _validate_hypersync_chain_id_async(client, chain_id, reason="timestamp-cache-validate")

        # Build chunks across all ranges
        all_chunks: list[tuple[int, int]] = []
        for range_start, range_end in fetch_ranges:
            for cs in range(range_start, range_end + 1, chunk_size):
                ce = min(cs + chunk_size - 1, range_end)
                all_chunks.append((cs, ce))

        n_chunks = len(all_chunks)
        logger.info(
            "Chain %d: streaming %s blocks from Hypersync in %d chunk(s) of up to %s blocks each",
            chain_id,
            f"{total_blocks_to_fetch:,}",
            n_chunks,
            f"{chunk_size:,}",
        )

        for chunk_idx, (chunk_start, chunk_end) in enumerate(all_chunks):
            chunk_block_count = chunk_end - chunk_start + 1

            logger.info(
                "Chain %d: starting chunk %d/%d — requesting blocks %s - %s (%s blocks)",
                chain_id,
                chunk_idx + 1,
                n_chunks,
                f"{chunk_start:,}",
                f"{chunk_end:,}",
                f"{chunk_block_count:,}",
            )

            index = []
            values = []

            iter = get_block_timestamps_using_hypersync_async(
                client,
                chain_id,
                start_block=chunk_start,
                end_block=chunk_end,
                display_progress=display_progress,
                validate_chain_id=False,  # Already validated above
                reason=f"timestamp-cache-fill chunk {chunk_idx + 1}/{n_chunks}",
            )

            async for block_header in iter:
                index.append(block_header.block_number)
                values.append(block_header.timestamp)

            # Save after each chunk so progress is durable
            if index:
                series = pd.Series(data=values, index=index)
                timestamp_db.import_chain_data(chain_id, series)
                logger.info(
                    "Chain %d: chunk %d/%d complete — saved %s blocks (%s - %s) to cache",
                    chain_id,
                    chunk_idx + 1,
                    n_chunks,
                    f"{len(index):,}",
                    f"{chunk_start:,}",
                    f"{chunk_end:,}",
                )
            else:
                logger.info(
                    "Chain %d: chunk %d/%d returned 0 blocks for range %s - %s",
                    chain_id,
                    chunk_idx + 1,
                    n_chunks,
                    f"{chunk_start:,}",
                    f"{chunk_end:,}",
                )

        # Compute the full scan extent for gap healing below
        scan_start = min(s for s, _ in fetch_ranges)
        scan_end = max(e for _, e in fetch_ranges)

        logger.info(
            "Chain %d: all %d chunk(s) streamed, checking for gaps in range %s - %s",
            chain_id,
            n_chunks,
            f"{scan_start:,}",
            f"{scan_end:,}",
        )

        # Detect and heal any gaps left by silently dropped HyperSync batches.
        # On fast chains like Monad, HyperSync can skip entire ~9,000-block
        # streaming batches without raising errors.
        max_heal_attempts = 3
        for heal_attempt in range(max_heal_attempts):
            gaps = timestamp_db.find_gaps()
            # Only heal gaps that overlap our scan range.
            # find_gaps() returns (boundary_start, boundary_end, count) where
            # boundary blocks exist but everything between them is missing.
            # Filter to gaps that overlap scan_start..scan_end.
            gaps = [(s, e, n) for s, e, n in gaps if s + 1 <= scan_end and e - 1 >= scan_start]
            if not gaps:
                logger.info(
                    "Chain %d: no gaps found in scan range (heal check %d/%d)",
                    chain_id,
                    heal_attempt + 1,
                    max_heal_attempts,
                )
                break

            # Back off before each healing pass to let Hypersync rate limits
            # recover.  Without this, gap healing immediately re-hammers the
            # API after a 429-induced drop, turning a transient rate limit
            # into a cascading failure.
            if heal_attempt > 0:
                heal_backoff = 30 * (2**heal_attempt)  # 60s, 120s
                logger.info("Backing off %d seconds before gap-heal attempt %d/%d", heal_backoff, heal_attempt + 1, max_heal_attempts)
                await asyncio.sleep(heal_backoff)

            total_missing = sum(g[2] for g in gaps)
            logger.warning(
                "Chain %d: HyperSync dropped %d blocks across %d gaps in range %d-%d (heal attempt %d/%d), re-fetching",
                chain_id,
                total_missing,
                len(gaps),
                scan_start,
                scan_end,
                heal_attempt + 1,
                max_heal_attempts,
            )
            for gap_idx, (gap_start, gap_end, gap_size) in enumerate(gaps):
                logger.info(
                    "Chain %d: healing gap %d/%d — blocks %d - %d (%d missing blocks)",
                    chain_id,
                    gap_idx + 1,
                    len(gaps),
                    gap_start + 1,
                    gap_end - 1,
                    gap_size,
                )
                heal_index = []
                heal_values = []
                async for bh in get_block_timestamps_using_hypersync_async(
                    client,
                    chain_id,
                    start_block=gap_start + 1,
                    end_block=gap_end - 1,
                    display_progress=False,
                    validate_chain_id=False,  # Already validated above
                    reason=f"gap-heal-{heal_attempt + 1}/{max_heal_attempts}",
                ):
                    heal_index.append(bh.block_number)
                    heal_values.append(bh.timestamp)
                if heal_index:
                    heal_series = pd.Series(data=heal_values, index=heal_index)
                    timestamp_db.import_chain_data(chain_id, heal_series)
                    logger.info(
                        "Chain %d: healed gap %d/%d — inserted %d timestamps for blocks %d - %d",
                        chain_id,
                        gap_idx + 1,
                        len(gaps),
                        len(heal_index),
                        gap_start + 1,
                        gap_end - 1,
                    )
                else:
                    logger.warning(
                        "Chain %d: gap heal %d/%d returned 0 blocks for %d - %d",
                        chain_id,
                        gap_idx + 1,
                        len(gaps),
                        gap_start + 1,
                        gap_end - 1,
                    )
    else:
        logger.info(
            "Chain %d: timestamp cache fully covers requested range %s - %s (cache has %s - %s), no Hypersync fetch needed",
            chain_id,
            f"{start_block:,}",
            f"{end_block:,}",
            f"{first_read_block:,}" if first_read_block else "None",
            f"{last_read_block:,}" if last_read_block else "None",
        )

    # Drop unnecessary blocks from memory
    return timestamp_db.get_slicer()


def fetch_block_timestamps_using_hypersync_cached(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    cache_path=DEFAULT_TIMESTAMP_CACHE_FOLDER,
    display_progress: bool = True,
    attempts=5,
) -> BlockTimestampSlicer:
    """Sync wrapper.

    See :py:func:`fetch_block_timestamps_using_hypersync_cached_async` for documentation.

    :param attempts:
        Work around Hypersync timeout issues
    """

    logger.info(
        "Timestamp cache sync wrapper: chain %d, blocks %d - %d, max %d attempts",
        chain_id,
        start_block,
        end_block,
        attempts,
    )

    async def _hypersync_asyncio_wrapper():
        for attempt in range(attempts):
            try:
                logger.info(
                    "Chain %d: timestamp cache attempt %d/%d starting (blocks %d - %d)",
                    chain_id,
                    attempt + 1,
                    attempts,
                    start_block,
                    end_block,
                )
                return await fetch_block_timestamps_using_hypersync_cached_async(
                    client=client,
                    chain_id=chain_id,
                    start_block=start_block,
                    end_block=end_block,
                    cache_path=cache_path,
                    display_progress=display_progress,
                )
            except HypersyncFlaky as e:
                logger.warning(
                    "Chain %d: Hypersync flaky error on attempt %d/%d: %s",
                    chain_id,
                    attempt + 1,
                    attempts,
                    e,
                )
                if attempt + 1 >= attempts:
                    logger.error(
                        "Chain %d: exceeded maximum %d Hypersync attempts, giving up: %s",
                        chain_id,
                        attempts,
                        e,
                    )
                    raise
                # Exponential backoff: 30s, 60s, 120s, 240s to avoid hammering
                # a rate-limited Hypersync endpoint
                backoff = 30 * (2**attempt)
                logger.info(
                    "Chain %d: backing off %d seconds before retry attempt %d/%d",
                    chain_id,
                    backoff,
                    attempt + 2,
                    attempts,
                )
                await asyncio.sleep(backoff)

    return asyncio.run(_hypersync_asyncio_wrapper())
