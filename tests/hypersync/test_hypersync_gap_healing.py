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
    """Verify that head-backfill correctly fetches only the missing head range.

    1. Cache already has blocks 1000-2000
    2. Caller requests blocks 1-1200 (needs head backfill)
    3. The function should fetch only blocks 1-999 (the head gap)
    4. All requested blocks 1-1200 must be accessible
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

    fetch_calls = []

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000, validate_chain_id=True, reason=None):
        fetch_calls.append((start_block, end_block))
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
                start_block=1,
                end_block=1200,
                cache_path=tmp_path,
                display_progress=False,
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

    # Should have fetched head range only (possibly as multiple chunks)
    assert len(fetch_calls) >= 1
    # First fetch must start at 1, last fetch must end at or before 999
    assert fetch_calls[0][0] == 1
    assert all(end <= 999 for _, end in fetch_calls)

    slicer.close()


def test_hypersync_interior_gap_detected_on_retry(tmp_path):
    """Verify that interior gaps from partial backfill are detected and filled on retry.

    Simulates the state after a failed partial head-backfill:

    1. Cache has blocks 1-99 + 1000-2000 (partial backfill left a hole at 100-999)
    2. Caller requests blocks 1-1200
    3. MIN=1, MAX=2000 look fully covered, but blocks 100-999 are missing
    4. The function must detect the interior gap and fetch it
    """

    chain_id = 1

    from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase

    import pandas as pd

    # Pre-populate cache simulating partial backfill state:
    # blocks 1-99 (saved before 429) + blocks 1000-2000 (original cache)
    db = BlockTimestampDatabase.create(chain_id, tmp_path)
    partial_index = list(range(1, 100)) + list(range(1000, 2001))
    partial_values = [1_700_000_000 + b for b in partial_index]
    db.import_chain_data(chain_id, pd.Series(data=partial_values, index=partial_index))
    first, last = db.get_first_and_last_block()
    assert first == 1
    assert last == 2000
    assert db.get_count() == 1100  # 99 + 1001, gap at 100-999
    db.close()

    fetch_calls = []

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000, validate_chain_id=True, reason=None):
        fetch_calls.append((start_block, end_block))
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
                start_block=1,
                end_block=1200,
                cache_path=tmp_path,
                display_progress=False,
            )

        return slicer

    slicer = asyncio.run(_run())

    # All blocks 1-2000 must be present — no holes
    assert len(slicer) == 2000

    # Verify the previously-missing range is filled
    for block_num in range(100, 1000):
        ts = slicer[block_num]
        expected = datetime.datetime(2023, 11, 14, 22, 13, 20) + datetime.timedelta(seconds=block_num)
        assert ts == expected, f"Block {block_num}: expected {expected}, got {ts}"

    # Must have fetched the gap range (100-999)
    assert len(fetch_calls) >= 1
    fetched_blocks = set()
    for s, e in fetch_calls:
        fetched_blocks.update(range(s, e + 1))
    # All gap blocks must have been requested
    assert all(b in fetched_blocks for b in range(100, 1000))

    slicer.close()
