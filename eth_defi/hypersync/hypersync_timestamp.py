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
import logging
import time
from typing import AsyncIterable

import hypersync
import pandas as pd
from eth_typing import BlockNumber
from hypersync import BlockField
from tqdm_loggable.auto import tqdm

from eth_defi.event_reader.block_header import BlockHeader
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER, BlockTimestampDatabase, BlockTimestampSlicer, load_timestamp_cache
from eth_defi.hypersync.session import is_hypersync_client, open_hypersync_stream
from eth_defi.utils import from_unix_timestamp

logger = logging.getLogger(__name__)


class HypersyncFlaky(Exception):
    """Hypersync stream flaky error, e.g. timeout or rate limit."""


def is_hypersync_rate_limit_error(e: Exception) -> bool:
    """Check if a Hypersync RuntimeError is a rate limit error.

    The Rust client raises ``RuntimeError`` after exhausting its internal
    retries. The rate limit can surface in two different textual forms
    depending on where in the client it is detected:

    - As an HTTP status, e.g. ``... 429 Too Many Requests ...``.

    - As a server-side budget message wrapped inside a stream ``inner
      receiver`` error, e.g.::

          inner receiver
          Caused by:
            0: get initial data
            1: rate limited by server (remaining=0/100 reqs, resets_in=15s).
               To increase your rate limits, upgrade your plan at https://envio.dev/app/api-tokens

      This second form contains no ``429`` token, so we also match the
      ``rate limited by server`` wording. See the ``resets_in`` note in the
      retry callers: we deliberately do not parse it and instead rely on the
      caller's fixed backoff (typically longer than the reset window).
    """
    if not isinstance(e, RuntimeError):
        return False
    message = str(e).lower()
    return "429" in message or "rate limited" in message or "too many requests" in message


def is_hypersync_next_block_range_error(e: Exception) -> bool:
    """Check if a Hypersync stream failed on an internal pagination boundary.

    Some Hypersync backends can fail near the indexed chain head with an
    ``inner receiver`` error where ``next_block`` is at the lower boundary of
    the server-side subrange. Treat this as a flaky stream error so the caller
    can retry or wait for the backend to index more blocks.
    """
    if not isinstance(e, RuntimeError):
        return False
    message = str(e)
    return "inner receiver" in message and "server returned next_block" in message and "outside the requested range" in message


def is_hypersync_retryable_runtime_error(e: Exception) -> bool:
    """Check if a Hypersync ``RuntimeError`` should be handled by retry logic."""
    return is_hypersync_rate_limit_error(e) or is_hypersync_next_block_range_error(e)


def raise_if_recoverable_hypersync_flaky(e: RuntimeError, context: str) -> None:
    """Re-raise a recoverable Hypersync ``RuntimeError`` as :py:class:`HypersyncFlaky`.

    The Rust Hypersync client raises a bare ``RuntimeError`` for both server-side
    rate limiting and near-head pagination glitches. Wrapping them as
    :py:class:`HypersyncFlaky` lets the caller's retry/backoff loop recover
    instead of crashing the whole scan. Non-recoverable errors are left
    untouched so the caller can re-raise them with a bare ``raise``.

    :param context:
        Where the error happened, included in the wrapped message,
        e.g. ``"stream setup [vault-prices]"``.
    """
    if is_hypersync_rate_limit_error(e):
        raise HypersyncFlaky(f"Hypersync rate limited during {context}: {e}") from e
    if is_hypersync_next_block_range_error(e):
        raise HypersyncFlaky(f"Hypersync stream pagination failed during {context}: {e}") from e


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
        raise_if_recoverable_hypersync_flaky(e, f"chain_id validation{reason_suffix}")
        raise
    assert connected == expected_chain_id, f"Hypersync client connected to chain {connected}, but expected {expected_chain_id}"


async def _fetch_hypersync_block_height_async(
    client: hypersync.HypersyncClient,
    reason: str | None = None,
) -> int:
    """Fetch the latest indexed Hypersync block height."""
    reason_suffix = f" [{reason}]" if reason else ""
    try:
        return await client.get_height()
    except RuntimeError as e:
        raise_if_recoverable_hypersync_flaky(e, f"block height check{reason_suffix}")
        raise


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
        receiver = await open_hypersync_stream(client, query)
    except RuntimeError as e:
        raise_if_recoverable_hypersync_flaky(e, f"stream setup{reason_suffix}")
        raise

    while True:
        try:
            res = await asyncio.wait_for(receiver.recv(), timeout=timeout)
        except asyncio.TimeoutError as e:
            logger.error("HyperSync receiver timed out%s, cannot recover", reason_suffix)
            raise HypersyncFlaky(f"Cannot recover from HyperSync stream timeout after {timeout} seconds{reason_suffix}") from e
        except RuntimeError as e:
            raise_if_recoverable_hypersync_flaky(e, f"streaming{reason_suffix}")
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
            raise_if_recoverable_hypersync_flaky(e, "block-height-check")
            raise

    return asyncio.run(_hypersync_asyncio_wrapper())


def get_hypersync_block_height_with_retries(
    client: hypersync.HypersyncClient,
    attempts: int = 3,
    retry_sleep: int = 30,
    reason: str = "block-height-check",
) -> int:
    """Get latest Hypersync block height with retry/backoff.

    Hypersync height checks are one-shot API calls and can hit the same 429
    rate limits as streams. Use this helper when a caller needs a height check
    before opening a stream.

    :param client:
        Hypersync client.

    :param attempts:
        Maximum number of attempts before raising the last
        :py:class:`HypersyncFlaky`.

    :param retry_sleep:
        Sleep time between attempts, in seconds.

    :param reason:
        Human-readable operation label for logs.

    :return:
        Latest block number known to Hypersync.
    """
    assert attempts > 0, "attempts must be at least 1"

    last_exception = None
    for attempt in range(attempts):
        try:
            return get_hypersync_block_height(client)
        except HypersyncFlaky as e:
            last_exception = e
            logger.error("Hypersync height check %s attempt %d/%d failed: %s", reason, attempt + 1, attempts, e)
            if attempt + 1 >= attempts:
                raise
            logger.info("Retrying Hypersync height check %s after %d seconds backoff", reason, retry_sleep)
            time.sleep(retry_sleep)

    raise last_exception or RuntimeError("Hypersync height check failed with no exception recorded")


async def fetch_block_timestamps_using_hypersync_cached_async(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    cache_path=DEFAULT_TIMESTAMP_CACHE_FOLDER,
    display_progress: bool = True,
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

    if cache_path.exists():
        timestamp_db = load_timestamp_cache(chain_id, cache_path)
    else:
        timestamp_db = BlockTimestampDatabase.create(chain_id, cache_path)

    first_read_block, last_read_block = timestamp_db.get_first_and_last_block()

    logger.info(
        "Chain %d: timestamp cache has blocks %s - %s (%s entries), caller wants %s - %s",
        chain_id,
        f"{first_read_block:,}" if first_read_block else "None",
        f"{last_read_block:,}" if last_read_block else "None",
        f"{timestamp_db.get_count():,}",
        f"{start_block:,}",
        f"{end_block:,}",
    )

    def _build_fetch_ranges(range_end_block: int) -> list[tuple[int, int]]:
        """Build (start, end) pairs for blocks we need to fetch."""
        fetch_ranges: list[tuple[int, int]] = []

        if last_read_block:
            if start_block < first_read_block:
                head_end_block = min(first_read_block - 1, range_end_block)
                if start_block <= head_end_block:
                    fetch_ranges.append((start_block, head_end_block))
            if range_end_block > last_read_block:
                fetch_ranges.append((last_read_block + 1, range_end_block))

            # Detect interior gaps (e.g. from a partial backfill that saved
            # blocks 1-99 then got a 429, leaving a hole at 100-999).
            # Clip each gap to the requested range since we only care about
            # the intersection.
            for gap_start, gap_end, _count in timestamp_db.find_gaps():
                clip_start = max(start_block, gap_start + 1)
                clip_end = min(range_end_block, gap_end - 1)
                if clip_start <= clip_end:
                    fetch_ranges.append((clip_start, clip_end))
        else:
            fetch_ranges.append((start_block, range_end_block))

        return fetch_ranges

    fetch_ranges = _build_fetch_ranges(end_block)

    total_to_fetch = sum(e - s + 1 for s, e in fetch_ranges)

    if not fetch_ranges:
        logger.info("Chain %d: cache fully covers requested range, nothing to fetch", chain_id)
        return timestamp_db.get_slicer()

    # Validate chain_id once before streaming, and avoid querying beyond
    # Hypersync's current indexed height. Near-head overreads can surface as
    # Rust receiver pagination errors instead of clean empty responses.
    if is_hypersync_client(client):
        await _validate_hypersync_chain_id_async(client, chain_id, reason="timestamp-cache-validate")
        hypersync_height = await _fetch_hypersync_block_height_async(client, reason="timestamp-cache-height")
        if end_block > hypersync_height:
            logger.warning(
                "Chain %d: clipping timestamp request end block from %s to Hypersync indexed height %s",
                chain_id,
                f"{end_block:,}",
                f"{hypersync_height:,}",
            )
            end_block = hypersync_height
            if end_block < start_block:
                logger.warning(
                    "Chain %d: Hypersync indexed height %s is before requested start block %s, nothing to fetch",
                    chain_id,
                    f"{hypersync_height:,}",
                    f"{start_block:,}",
                )
                return timestamp_db.get_slicer()
            fetch_ranges = _build_fetch_ranges(end_block)
            total_to_fetch = sum(e - s + 1 for s, e in fetch_ranges)
            if not fetch_ranges:
                logger.info("Chain %d: cache fully covers clipped requested range, nothing to fetch", chain_id)
                return timestamp_db.get_slicer()

    # Split ranges into chunks — each opens a separate stream() call
    # so the Python-side throttle can pace requests, and progress is
    # saved after each chunk.
    all_chunks: list[tuple[int, int]] = []
    for range_start, range_end in fetch_ranges:
        for cs in range(range_start, range_end + 1, chunk_size):
            all_chunks.append((cs, min(cs + chunk_size - 1, range_end)))

    n_chunks = len(all_chunks)
    logger.info(
        "Chain %d: fetching %s blocks in %d chunk(s) across %d range(s)",
        chain_id,
        f"{total_to_fetch:,}",
        n_chunks,
        len(fetch_ranges),
    )

    empty_chunks = 0
    for chunk_idx, (chunk_start, chunk_end) in enumerate(all_chunks):
        index = []
        values = []

        async for block_header in get_block_timestamps_using_hypersync_async(
            client,
            chain_id,
            start_block=chunk_start,
            end_block=chunk_end,
            display_progress=display_progress,
            validate_chain_id=False,
            reason=f"timestamp-cache-fill chunk {chunk_idx + 1}/{n_chunks}",
        ):
            index.append(block_header.block_number)
            values.append(block_header.timestamp)

        # Save after each chunk so progress is durable
        if index:
            timestamp_db.import_chain_data(chain_id, pd.Series(data=values, index=index))
            logger.info(
                "Chain %d: chunk %d/%d saved %s blocks (%s - %s)",
                chain_id,
                chunk_idx + 1,
                n_chunks,
                f"{len(index):,}",
                f"{chunk_start:,}",
                f"{chunk_end:,}",
            )
        else:
            empty_chunks += 1

    # If Hypersync returned zero rows for any chunk, the blocks are
    # still missing but find_gaps() cannot detect head/tail gaps
    # (no boundary blocks to define them). Treat this as a flaky
    # error so the retry loop re-enters with a fresh cache check.
    if empty_chunks:
        raise HypersyncFlaky(f"Chain {chain_id}: Hypersync returned 0 rows for {empty_chunks}/{n_chunks} chunks")

    # Heal gaps left by silently dropped HyperSync batches.
    # On fast chains like Monad, HyperSync can skip entire ~9,000-block
    # streaming batches without raising errors.
    scan_start = min(s for s, _ in fetch_ranges)
    scan_end = max(e for _, e in fetch_ranges)
    max_heal_attempts = 3

    for heal_attempt in range(max_heal_attempts):
        gaps = timestamp_db.find_gaps()
        # Clip gaps to our scan range — don't heal the full database
        # gap when we only need a subset.
        clipped_gaps: list[tuple[int, int]] = []
        for s, e, _n in gaps:
            clip_start = max(scan_start, s + 1)
            clip_end = min(scan_end, e - 1)
            if clip_start <= clip_end:
                clipped_gaps.append((clip_start, clip_end))
        if not clipped_gaps:
            break

        # Back off before re-heal to let rate limits recover
        if heal_attempt > 0:
            heal_backoff = 30 * (2**heal_attempt)
            logger.info("Chain %d: backing off %ds before gap-heal attempt %d/%d", chain_id, heal_backoff, heal_attempt + 1, max_heal_attempts)
            await asyncio.sleep(heal_backoff)

        total_missing = sum(e - s + 1 for s, e in clipped_gaps)
        logger.warning(
            "Chain %d: %d blocks dropped across %d gaps (heal attempt %d/%d)",
            chain_id,
            total_missing,
            len(clipped_gaps),
            heal_attempt + 1,
            max_heal_attempts,
        )

        # Chunk heal ranges the same way as initial fetches
        for heal_start, heal_end in clipped_gaps:
            for hs in range(heal_start, heal_end + 1, chunk_size):
                he = min(hs + chunk_size - 1, heal_end)
                heal_index = []
                heal_values = []
                async for bh in get_block_timestamps_using_hypersync_async(
                    client,
                    chain_id,
                    start_block=hs,
                    end_block=he,
                    display_progress=False,
                    validate_chain_id=False,
                    reason=f"gap-heal-{heal_attempt + 1}/{max_heal_attempts}",
                ):
                    heal_index.append(bh.block_number)
                    heal_values.append(bh.timestamp)
                if heal_index:
                    timestamp_db.import_chain_data(chain_id, pd.Series(data=heal_values, index=heal_index))
                    logger.info("Chain %d: healed %d-%d (%d blocks)", chain_id, hs, he, len(heal_index))

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
    """Sync wrapper with retry and exponential backoff.

    See :py:func:`fetch_block_timestamps_using_hypersync_cached_async` for documentation.

    :param attempts:
        Work around Hypersync timeout issues
    """

    async def _hypersync_asyncio_wrapper():
        for attempt in range(attempts):
            try:
                return await fetch_block_timestamps_using_hypersync_cached_async(
                    client=client,
                    chain_id=chain_id,
                    start_block=start_block,
                    end_block=end_block,
                    cache_path=cache_path,
                    display_progress=display_progress,
                )
            except HypersyncFlaky as e:
                logger.warning("Chain %d: Hypersync flaky on attempt %d/%d: %s", chain_id, attempt + 1, attempts, e)
                if attempt + 1 >= attempts:
                    raise
                backoff = 30 * (2**attempt)
                logger.info("Chain %d: backing off %ds before retry %d/%d", chain_id, backoff, attempt + 2, attempts)
                await asyncio.sleep(backoff)

    return asyncio.run(_hypersync_asyncio_wrapper())


async def fetch_sparse_block_timestamps_using_hypersync_cached_async(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    step: int,
    cache_path=DEFAULT_TIMESTAMP_CACHE_FOLDER,
    display_progress: bool = True,
    checkpoint_frequency: int = 25,
    max_concurrency: int = 5,
) -> BlockTimestampSlicer:
    """Fetch only exact sampled timestamps through Hypersync and the shared cache.

    Historical state readers may sample one block per hour or day from chains
    that produce millions of blocks per month. Fetching every intervening
    header wastes Hypersync quota and can make the first backfill impossible
    under a rate-limited API key. This path anti-joins the sampled block
    numbers against the persistent DuckDB cache, fetches only misses, and
    checkpoints them incrementally.

    :param client:
        Hypersync client for the requested chain.
    :param chain_id:
        Expected EVM chain id.
    :param start_block:
        First sampled block, inclusive.
    :param end_block:
        Historical reader end block, exclusive.
    :param step:
        Number of blocks between historical samples.
    :param cache_path:
        Shared per-chain timestamp cache directory.
    :param display_progress:
        Whether to display sampled timestamp progress.
    :param checkpoint_frequency:
        Maximum number of new timestamps held before a durable cache write.
    :param max_concurrency:
        Maximum one-block streams awaited together. The shared client limiter
        still governs total request starts across the batch.
    :return:
        Cache-backed timestamp slicer containing every requested sample.
    """

    assert start_block <= end_block
    assert step > 1
    assert checkpoint_frequency > 0
    assert max_concurrency > 0

    timestamp_db = load_timestamp_cache(chain_id, cache_path) if cache_path.exists() else BlockTimestampDatabase.create(chain_id, cache_path)
    requested_blocks = tuple(range(start_block, end_block, step))
    required_blocks = requested_blocks
    missing_blocks = timestamp_db.get_missing_block_numbers(required_blocks)
    if not missing_blocks:
        logger.info("Chain %d: timestamp cache contains all %d sampled blocks", chain_id, len(requested_blocks))
        return timestamp_db.get_slicer()

    if is_hypersync_client(client):
        await _validate_hypersync_chain_id_async(client, chain_id, reason="sparse-timestamp-cache-validate")
        hypersync_height = await _fetch_hypersync_block_height_async(client, reason="sparse-timestamp-cache-height")
        required_blocks = tuple(block_number for block_number in requested_blocks if block_number <= hypersync_height)
        missing_blocks = [block_number for block_number in missing_blocks if block_number <= hypersync_height]

    logger.info(
        "Chain %d: fetching %d/%d sampled block timestamps through Hypersync (step %d)",
        chain_id,
        len(missing_blocks),
        len(requested_blocks),
        step,
    )
    progress_bar = tqdm(total=len(missing_blocks), desc=f"Reading sampled timestamps (hypersync) on {chain_id}") if display_progress else None
    pending_index: list[int] = []
    pending_values: list[int] = []

    def _checkpoint() -> None:
        """Persist the current sampled timestamp batch."""

        if pending_index:
            timestamp_db.import_chain_data(chain_id, pd.Series(data=pending_values, index=pending_index))
            pending_index.clear()
            pending_values.clear()

    async def _fetch_sample(sample_idx: int, block_number: int) -> BlockHeader:
        """Fetch and validate one exact sampled block header."""

        headers = [
            header
            async for header in get_block_timestamps_using_hypersync_async(
                client,
                chain_id,
                start_block=block_number,
                end_block=block_number,
                display_progress=False,
                validate_chain_id=False,
                reason=f"sampled-timestamp {sample_idx}/{len(missing_blocks)}",
            )
        ]
        if len(headers) != 1 or headers[0].block_number != block_number:
            raise HypersyncFlaky(f"Chain {chain_id}: Hypersync did not return sampled block {block_number:,}")
        return headers[0]

    try:
        for batch_start in range(0, len(missing_blocks), max_concurrency):
            batch = missing_blocks[batch_start : batch_start + max_concurrency]
            headers = await asyncio.gather(*(_fetch_sample(batch_start + batch_offset + 1, block_number) for batch_offset, block_number in enumerate(batch)))
            pending_index.extend(batch)
            pending_values.extend(header.timestamp for header in headers)
            if len(pending_index) >= checkpoint_frequency:
                _checkpoint()
            if progress_bar:
                progress_bar.update(len(batch))
                progress_bar.set_postfix({"block": f"{batch[-1]:,}"})
    finally:
        _checkpoint()
        if progress_bar:
            progress_bar.close()

    remaining = timestamp_db.get_missing_block_numbers(required_blocks)
    if remaining:
        raise HypersyncFlaky(f"Chain {chain_id}: timestamp cache still lacks {len(remaining)} sampled blocks")
    return timestamp_db.get_slicer()


def fetch_sparse_block_timestamps_using_hypersync_cached(
    client: hypersync.HypersyncClient,
    chain_id: int,
    start_block: int,
    end_block: int,
    step: int,
    cache_path=DEFAULT_TIMESTAMP_CACHE_FOLDER,
    display_progress: bool = True,
    attempts: int = 5,
) -> BlockTimestampSlicer:
    """Synchronously fetch sparse sampled timestamps with retry/backoff.

    :param attempts:
        Maximum Hypersync attempts; completed checkpoints survive retries.
    :return:
        Cache-backed timestamp slicer containing every requested sample.
    """

    async def _hypersync_asyncio_wrapper() -> BlockTimestampSlicer:
        for attempt in range(attempts):
            try:
                return await fetch_sparse_block_timestamps_using_hypersync_cached_async(
                    client=client,
                    chain_id=chain_id,
                    start_block=start_block,
                    end_block=end_block,
                    step=step,
                    cache_path=cache_path,
                    display_progress=display_progress,
                )
            except HypersyncFlaky as error:
                logger.warning("Chain %d: sparse Hypersync fetch flaky on attempt %d/%d: %s", chain_id, attempt + 1, attempts, error)
                if attempt + 1 >= attempts:
                    raise
                backoff = 30 * (2**attempt)
                logger.info("Chain %d: backing off %ds before sparse retry %d/%d", chain_id, backoff, attempt + 2, attempts)
                await asyncio.sleep(backoff)
        raise RuntimeError("Sparse Hypersync timestamp fetch exhausted without returning or raising")

    return asyncio.run(_hypersync_asyncio_wrapper())
