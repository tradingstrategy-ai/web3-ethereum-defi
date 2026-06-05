"""Test that HyperSync timestamp gap healing and backfill work correctly.

Simulates HyperSync silently dropping blocks (as observed on fast chains
like Monad) and verifies the gap-healing loop detects and re-fetches them.

Also tests head-backfill scenarios where the caller requests blocks before
an existing cache, verifying that partial progress followed by retry does
not leave permanent holes.

No live HyperSync connection needed — uses mocked async generators.
"""

import asyncio
import datetime
from unittest.mock import patch, MagicMock

import pytest

from eth_defi.event_reader.block_header import BlockHeader
from eth_defi.hypersync.hypersync_timestamp import fetch_block_timestamps_using_hypersync_cached_async


def _make_block_header(block_number: int) -> BlockHeader:
    """Create a synthetic block header with deterministic timestamp."""
    return BlockHeader(
        block_number=block_number,
        block_hash=f"0x{block_number:064x}",
        timestamp=1_700_000_000 + block_number,
    )


def test_hypersync_gap_healing(tmp_path):
    """Verify that silently dropped HyperSync batches are detected and healed.

    Simulates HyperSync dropping blocks 1020-1029 and 1050-1059 on the
    initial stream, then returning complete data on the healing re-fetch.
    """

    chain_id = 1
    start_block = 1000
    end_block = 1099

    # Block ranges the first (broken) stream will skip (inclusive start, exclusive end)
    dropped_ranges = [(1020, 1030), (1050, 1060)]

    call_count = 0

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000, validate_chain_id=True, reason=None):
        nonlocal call_count
        call_count += 1
        current_call = call_count

        for block_num in range(start_block, end_block + 1):
            # On first call only, simulate dropped batches
            if current_call == 1:
                if any(lo <= block_num < hi for lo, hi in dropped_ranges):
                    continue

            yield _make_block_header(block_num)

    async def _run():
        mock_client = MagicMock()

        with patch(
            "eth_defi.hypersync.hypersync_timestamp.get_block_timestamps_using_hypersync_async",
            side_effect=mock_get_timestamps,
        ):
            slicer = await fetch_block_timestamps_using_hypersync_cached_async(
                client=mock_client,
                chain_id=chain_id,
                start_block=start_block,
                end_block=end_block,
                cache_path=tmp_path,
                display_progress=False,
            )

        return slicer

    slicer = asyncio.run(_run())

    # All 100 blocks should be present after healing
    assert len(slicer) == 100

    # Verify every block is accessible, including those that were initially dropped
    for block_num in range(start_block, end_block + 1):
        ts = slicer[block_num]
        expected = datetime.datetime(2023, 11, 14, 22, 13, 20) + datetime.timedelta(seconds=block_num)
        assert ts == expected, f"Block {block_num}: expected {expected}, got {ts}"

    # Initial fetch + 2 healing fetches (one per gap)
    assert call_count == 3

    slicer.close()


def test_hypersync_gap_healing_no_gaps(tmp_path):
    """Verify that when HyperSync returns all blocks, no healing is needed."""

    chain_id = 1
    start_block = 1000
    end_block = 1049

    call_count = 0

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000, validate_chain_id=True, reason=None):
        nonlocal call_count
        call_count += 1

        for block_num in range(start_block, end_block + 1):
            yield _make_block_header(block_num)

    async def _run():
        mock_client = MagicMock()

        with patch(
            "eth_defi.hypersync.hypersync_timestamp.get_block_timestamps_using_hypersync_async",
            side_effect=mock_get_timestamps,
        ):
            slicer = await fetch_block_timestamps_using_hypersync_cached_async(
                client=mock_client,
                chain_id=chain_id,
                start_block=start_block,
                end_block=end_block,
                cache_path=tmp_path,
                display_progress=False,
            )

        return slicer

    slicer = asyncio.run(_run())

    assert len(slicer) == 50

    # Only the initial fetch, no healing needed
    assert call_count == 1

    slicer.close()


def test_hypersync_gap_healing_persistent_gap(tmp_path):
    """Verify behaviour when a gap persists across all healing attempts.

    If HyperSync consistently drops the same blocks, the healing loop
    should retry up to max_heal_attempts (3) and then continue without
    crashing.
    """

    chain_id = 1
    start_block = 1000
    end_block = 1049

    # Always drop blocks 1020-1024
    always_dropped = set(range(1020, 1025))

    call_count = 0

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000, validate_chain_id=True, reason=None):
        nonlocal call_count
        call_count += 1

        for block_num in range(start_block, end_block + 1):
            if block_num in always_dropped:
                continue
            yield _make_block_header(block_num)

    async def _run():
        mock_client = MagicMock()

        with (
            patch(
                "eth_defi.hypersync.hypersync_timestamp.get_block_timestamps_using_hypersync_async",
                side_effect=mock_get_timestamps,
            ),
            patch(
                "eth_defi.hypersync.hypersync_timestamp.asyncio.sleep",
            ),
        ):
            slicer = await fetch_block_timestamps_using_hypersync_cached_async(
                client=mock_client,
                chain_id=chain_id,
                start_block=start_block,
                end_block=end_block,
                cache_path=tmp_path,
                display_progress=False,
            )

        return slicer

    slicer = asyncio.run(_run())

    # 50 blocks total minus 5 persistently dropped = 45
    assert len(slicer) == 45

    # 1 initial fetch + 3 heal attempts (max_heal_attempts)
    assert call_count == 4

    slicer.close()


def test_hypersync_head_backfill_no_holes(tmp_path):
    """Verify that head-backfill followed by retry does not leave permanent holes.

    Reproduces the scenario where:

    1. Cache already has blocks 1000-2000
    2. Caller requests blocks 1-1200 (needs head backfill)
    3. First attempt saves chunk 1-99, then fails
    4. On retry, blocks 100-999 must still be fetched despite
       MIN=1 and MAX=2000 looking fully covered

    Without separate head/tail ranges this would leave blocks 100-999
    permanently missing because MIN/MAX suggest full coverage.
    """

    chain_id = 1

    # Pre-populate cache with blocks 1000-2000
    from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase

    import pandas as pd

    db = BlockTimestampDatabase.create(chain_id, tmp_path)
    existing_index = list(range(1000, 2001))
    existing_values = [1_700_000_000 + b for b in existing_index]
    db.import_chain_data(chain_id, pd.Series(data=existing_values, index=existing_index))
    first, last = db.get_first_and_last_block()
    assert first == 1000
    assert last == 2000
    db.close()

    attempt = 0

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000, validate_chain_id=True, reason=None):
        nonlocal attempt
        attempt += 1
        for block_num in range(start_block, end_block + 1):
            yield _make_block_header(block_num)

    async def _run():
        mock_client = MagicMock()

        with patch(
            "eth_defi.hypersync.hypersync_timestamp.get_block_timestamps_using_hypersync_async",
            side_effect=mock_get_timestamps,
        ):
            # Simulate: caller wants blocks 1-1200, cache has 1000-2000.
            # Head range should be 1-999, tail is not needed (1200 <= 2000).
            slicer = await fetch_block_timestamps_using_hypersync_cached_async(
                client=mock_client,
                chain_id=chain_id,
                start_block=1,
                end_block=1200,
                cache_path=tmp_path,
                display_progress=False,
                chunk_size=100_000,
            )

        return slicer

    slicer = asyncio.run(_run())

    # All blocks 1-2000 should be present (head backfill 1-999 + existing 1000-2000)
    assert len(slicer) == 2000

    # Verify no holes — every block from 1 to 1200 (the requested range) is accessible
    for block_num in range(1, 1201):
        ts = slicer[block_num]
        expected = datetime.datetime(2023, 11, 14, 22, 13, 20) + datetime.timedelta(seconds=block_num)
        assert ts == expected, f"Block {block_num}: expected {expected}, got {ts}"

    # Only one fetch range (head: 1-999), served as a single chunk
    assert attempt == 1

    slicer.close()


def test_hypersync_head_backfill_retry_after_partial(tmp_path):
    """Verify that partial head-backfill followed by retry fills the remaining gap.

    1. Cache has blocks 1000-2000
    2. First call: fetch head range 1-999 with chunk_size=100, saves chunk 1-99,
       then 429 kills chunk 100-199
    3. Second call: cache now has 1-99 + 1000-2000. The function must detect
       the gap 100-999 and fetch it.

    This is the exact P1 bug scenario.
    """

    chain_id = 1

    from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
    from eth_defi.hypersync.hypersync_timestamp import HypersyncFlaky

    import pandas as pd

    # Pre-populate cache with blocks 1000-2000
    db = BlockTimestampDatabase.create(chain_id, tmp_path)
    existing_index = list(range(1000, 2001))
    existing_values = [1_700_000_000 + b for b in existing_index]
    db.import_chain_data(chain_id, pd.Series(data=existing_values, index=existing_index))
    db.close()

    call_log = []

    async def mock_get_timestamps_fail_second_chunk(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000, validate_chain_id=True, reason=None):
        call_log.append((start_block, end_block))
        # On the very first call stream succeeds (chunk 1-99).
        # On the second call (chunk 100-199), simulate a 429.
        if len(call_log) == 2:
            raise HypersyncFlaky("Simulated 429 for testing")
        for block_num in range(start_block, end_block + 1):
            yield _make_block_header(block_num)

    async def mock_get_timestamps_ok(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000, validate_chain_id=True, reason=None):
        call_log.append((start_block, end_block))
        for block_num in range(start_block, end_block + 1):
            yield _make_block_header(block_num)

    async def _run():
        mock_client = MagicMock()

        # First attempt: chunk 1-99 saves, chunk 100-199 fails with 429
        with patch(
            "eth_defi.hypersync.hypersync_timestamp.get_block_timestamps_using_hypersync_async",
            side_effect=mock_get_timestamps_fail_second_chunk,
        ):
            with pytest.raises(HypersyncFlaky):
                await fetch_block_timestamps_using_hypersync_cached_async(
                    client=mock_client,
                    chain_id=chain_id,
                    start_block=1,
                    end_block=1200,
                    cache_path=tmp_path,
                    display_progress=False,
                    chunk_size=100,
                )

        # Retry: should detect that 100-999 is still missing and fetch it
        with patch(
            "eth_defi.hypersync.hypersync_timestamp.get_block_timestamps_using_hypersync_async",
            side_effect=mock_get_timestamps_ok,
        ):
            slicer = await fetch_block_timestamps_using_hypersync_cached_async(
                client=mock_client,
                chain_id=chain_id,
                start_block=1,
                end_block=1200,
                cache_path=tmp_path,
                display_progress=False,
                chunk_size=100,
            )

        return slicer

    slicer = asyncio.run(_run())

    # All blocks 1-2000 must be present — no holes
    assert len(slicer) == 2000

    # Verify the previously-vulnerable range is filled
    for block_num in range(100, 1000):
        ts = slicer[block_num]
        expected = datetime.datetime(2023, 11, 14, 22, 13, 20) + datetime.timedelta(seconds=block_num)
        assert ts == expected, f"Block {block_num}: expected {expected}, got {ts}"

    slicer.close()
