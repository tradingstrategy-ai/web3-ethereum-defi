"""Test that HyperSync timestamp gap healing works correctly.

Simulates HyperSync silently dropping blocks (as observed on fast chains
like Monad) and verifies the gap-healing loop detects and re-fetches them.

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

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000):
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

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000):
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

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000):
        nonlocal call_count
        call_count += 1

        for block_num in range(start_block, end_block + 1):
            if block_num in always_dropped:
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

    # 50 blocks total minus 5 persistently dropped = 45
    assert len(slicer) == 45

    # 1 initial fetch + 3 heal attempts (max_heal_attempts)
    assert call_count == 4

    slicer.close()
