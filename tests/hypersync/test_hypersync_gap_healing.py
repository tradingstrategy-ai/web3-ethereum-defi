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
from unittest.mock import AsyncMock, MagicMock, patch

from eth_defi.event_reader.block_header import BlockHeader
from eth_defi.hypersync.hypersync_timestamp import (
    HypersyncFlaky,
    fetch_block_timestamps_using_hypersync_cached,
    fetch_block_timestamps_using_hypersync_cached_async,
    get_hypersync_block_height_with_retries,
    is_hypersync_next_block_range_error,
    is_hypersync_rate_limit_error,
    raise_if_recoverable_hypersync_flaky,
)


def test_is_hypersync_rate_limit_error_matches_textual_form():
    """Verify the rate limit classifier matches both HTTP and textual server forms.

    The Envio/HyperSync Rust client surfaces an exhausted request budget in two
    shapes: as an HTTP ``429`` status, or as a textual ``rate limited by server``
    message wrapped inside an ``inner receiver`` error. The textual form
    contains no ``429`` and previously slipped past the classifier, so the whole
    chain scan crashed instead of being retried.

    1. Match the exact production textual rate limit message.
    2. Match the legacy HTTP 429 form.
    3. Reject unrelated runtime errors and non-RuntimeError exceptions.
    """

    # 1. Match the exact production textual rate limit message.
    production_message = "inner receiver\n\nCaused by:\n    0: get initial data\n    1: rate limited by server (remaining=0/100 reqs, resets_in=15s). To increase your rate limits, upgrade your plan at https://envio.dev/app/api-tokens\n    2: "
    assert is_hypersync_rate_limit_error(RuntimeError(production_message)) is True

    # 2. Match the legacy HTTP 429 form and a "Too Many Requests" phrasing.
    assert is_hypersync_rate_limit_error(RuntimeError("HTTP 429 Too Many Requests")) is True

    # 3. Reject unrelated runtime errors and non-RuntimeError exceptions.
    assert is_hypersync_rate_limit_error(RuntimeError("connection reset")) is False
    assert is_hypersync_rate_limit_error(ValueError("rate limited")) is False


def test_raise_if_recoverable_hypersync_flaky_wraps_and_passes_through():
    """Verify the shared wrapper converts recoverable errors and ignores others.

    The consolidated helper replaces four near-identical ``except RuntimeError``
    blocks. It must wrap rate-limit and pagination errors as
    :py:class:`HypersyncFlaky` so retry loops recover, while leaving unrelated
    errors untouched for the caller's bare ``raise``.

    1. A textual rate limit error becomes a HypersyncFlaky mentioning "rate limited".
    2. A next_block_range pagination error becomes a HypersyncFlaky mentioning "stream pagination failed".
    3. An unrelated RuntimeError is left untouched (helper returns None).
    """

    # 1. A textual rate limit error becomes a HypersyncFlaky mentioning "rate limited".
    try:
        raise_if_recoverable_hypersync_flaky(RuntimeError("rate limited by server"), "stream setup [unit-test]")
    except HypersyncFlaky as e:
        assert "rate limited" in str(e)
        assert "stream setup [unit-test]" in str(e)
    else:
        assert False, "Expected HypersyncFlaky for rate limit error"

    # 2. A next_block_range pagination error becomes a HypersyncFlaky mentioning "stream pagination failed".
    pagination_message = "inner receiver\n\nCaused by:\n    server returned next_block 100 outside the requested range [100..200)"
    try:
        raise_if_recoverable_hypersync_flaky(RuntimeError(pagination_message), "streaming [unit-test]")
    except HypersyncFlaky as e:
        assert "stream pagination failed" in str(e)
    else:
        assert False, "Expected HypersyncFlaky for pagination error"

    # 3. An unrelated RuntimeError is left untouched (helper returns None).
    assert raise_if_recoverable_hypersync_flaky(RuntimeError("connection reset"), "streaming [unit-test]") is None


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


def test_hypersync_next_block_range_error_is_flaky():
    """Verify that Hypersync near-head pagination failures are retryable."""
    err = RuntimeError("inner receiver\n\nCaused by:\n    server returned next_block 24535975 outside the requested range [24535975..24535985)")

    assert is_hypersync_next_block_range_error(err)


def test_hypersync_block_height_retry_helper_retries_flaky_error():
    """Verify that transient Hypersync height failures are retried."""
    mock_client = MagicMock()

    with (
        patch(
            "eth_defi.hypersync.hypersync_timestamp.get_hypersync_block_height",
            side_effect=[
                HypersyncFlaky("rate limited"),
                1234,
            ],
        ) as height_check,
        patch(
            "eth_defi.hypersync.hypersync_timestamp.time.sleep",
        ) as sleep_mock,
    ):
        height = get_hypersync_block_height_with_retries(
            mock_client,
            attempts=2,
            retry_sleep=1,
            reason="test-height",
        )

    assert height == 1234
    assert height_check.call_count == 2
    sleep_mock.assert_called_once_with(1)


def test_hypersync_cached_sync_retries_next_block_range_error(tmp_path):
    """Verify that the sync cached wrapper retries a flaky stream failure."""

    chain_id = 1
    start_block = 1000
    end_block = 1009
    call_count = 0

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000, validate_chain_id=True, reason=None):
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            raise HypersyncFlaky("Hypersync stream pagination failed: inner receiver")

        for block_num in range(start_block, end_block + 1):
            yield _make_block_header(block_num)

    with (
        patch(
            "eth_defi.hypersync.hypersync_timestamp.get_block_timestamps_using_hypersync_async",
            side_effect=mock_get_timestamps,
        ),
        patch(
            "eth_defi.hypersync.hypersync_timestamp.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        slicer = fetch_block_timestamps_using_hypersync_cached(
            client=MagicMock(),
            chain_id=chain_id,
            start_block=start_block,
            end_block=end_block,
            cache_path=tmp_path,
            display_progress=False,
            attempts=2,
        )

    assert call_count == 2
    assert len(slicer) == 10
    assert slicer[start_block] == datetime.datetime(2023, 11, 14, 22, 30)
    assert slicer[end_block] == datetime.datetime(2023, 11, 14, 22, 30, 9)

    slicer.close()


def test_hypersync_timestamp_fetch_clips_to_indexed_height(tmp_path):
    """Verify that timestamp fetch does not stream past Hypersync indexed height."""

    chain_id = 1
    fetch_calls = []

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000, validate_chain_id=True, reason=None):
        fetch_calls.append((start_block, end_block))
        for block_num in range(start_block, end_block + 1):
            yield _make_block_header(block_num)

    async def _run():
        with (
            patch(
                "eth_defi.hypersync.hypersync_timestamp.is_hypersync_client",
                return_value=True,
            ),
            patch(
                "eth_defi.hypersync.hypersync_timestamp._validate_hypersync_chain_id_async",
                new_callable=AsyncMock,
            ),
            patch(
                "eth_defi.hypersync.hypersync_timestamp._fetch_hypersync_block_height_async",
                new_callable=AsyncMock,
                return_value=1050,
            ),
            patch(
                "eth_defi.hypersync.hypersync_timestamp.get_block_timestamps_using_hypersync_async",
                side_effect=mock_get_timestamps,
            ),
        ):
            slicer = await fetch_block_timestamps_using_hypersync_cached_async(
                client=MagicMock(),
                chain_id=chain_id,
                start_block=1000,
                end_block=1099,
                cache_path=tmp_path,
                display_progress=False,
            )

        return slicer

    slicer = asyncio.run(_run())

    assert fetch_calls == [(1000, 1050)]
    assert len(slicer) == 51
    assert slicer.get_last_block() == 1050

    slicer.close()


def test_hypersync_timestamp_fetch_uses_warm_cache_without_height_check(tmp_path):
    """Verify that a fully cached range does not call Hypersync."""

    chain_id = 1

    import pandas as pd

    from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase

    db = BlockTimestampDatabase.create(chain_id, tmp_path)
    existing_index = list(range(1000, 1100))
    existing_values = [1_700_000_000 + b for b in existing_index]
    db.import_chain_data(chain_id, pd.Series(data=existing_values, index=existing_index))
    db.close()

    async def _run():
        height_check = AsyncMock(return_value=1099)
        validate_chain = AsyncMock()

        with (
            patch(
                "eth_defi.hypersync.hypersync_timestamp.is_hypersync_client",
                return_value=True,
            ),
            patch(
                "eth_defi.hypersync.hypersync_timestamp._validate_hypersync_chain_id_async",
                validate_chain,
            ),
            patch(
                "eth_defi.hypersync.hypersync_timestamp._fetch_hypersync_block_height_async",
                height_check,
            ),
        ):
            slicer = await fetch_block_timestamps_using_hypersync_cached_async(
                client=MagicMock(),
                chain_id=chain_id,
                start_block=1000,
                end_block=1099,
                cache_path=tmp_path,
                display_progress=False,
            )

        return slicer, validate_chain, height_check

    slicer, validate_chain, height_check = asyncio.run(_run())

    assert len(slicer) == 100
    validate_chain.assert_not_awaited()
    height_check.assert_not_awaited()

    slicer.close()


def test_hypersync_head_backfill_respects_clipped_indexed_height(tmp_path):
    """Verify that head backfills do not fetch past clipped Hypersync height."""

    chain_id = 1

    import pandas as pd

    from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase

    db = BlockTimestampDatabase.create(chain_id, tmp_path)
    existing_index = list(range(1000, 1100))
    existing_values = [1_700_000_000 + b for b in existing_index]
    db.import_chain_data(chain_id, pd.Series(data=existing_values, index=existing_index))
    db.close()

    fetch_calls = []

    async def mock_get_timestamps(client, chain_id, start_block, end_block, timeout=120.0, display_progress=True, progress_throttle=10_000, validate_chain_id=True, reason=None):
        fetch_calls.append((start_block, end_block))
        for block_num in range(start_block, end_block + 1):
            yield _make_block_header(block_num)

    async def _run():
        with (
            patch(
                "eth_defi.hypersync.hypersync_timestamp.is_hypersync_client",
                return_value=True,
            ),
            patch(
                "eth_defi.hypersync.hypersync_timestamp._validate_hypersync_chain_id_async",
                new_callable=AsyncMock,
            ),
            patch(
                "eth_defi.hypersync.hypersync_timestamp._fetch_hypersync_block_height_async",
                new_callable=AsyncMock,
                return_value=500,
            ),
            patch(
                "eth_defi.hypersync.hypersync_timestamp.get_block_timestamps_using_hypersync_async",
                side_effect=mock_get_timestamps,
            ),
        ):
            slicer = await fetch_block_timestamps_using_hypersync_cached_async(
                client=MagicMock(),
                chain_id=chain_id,
                start_block=1,
                end_block=1200,
                cache_path=tmp_path,
                display_progress=False,
            )

        return slicer

    slicer = asyncio.run(_run())

    assert fetch_calls == [(1, 500)]
    assert len(slicer) == 600
    assert slicer.get_last_block() == 1099

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
    import pandas as pd

    from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase

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

    import pandas as pd

    from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase

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
