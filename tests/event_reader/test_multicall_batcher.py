"""Regression tests for historical multicall timestamp handling."""

import datetime
from collections.abc import Callable, Iterable, Iterator
from unittest.mock import MagicMock

import pytest

from eth_defi.event_reader import multicall_batcher


ParallelExecutor = Callable[[Iterable[object]], Iterator[object]]


def make_empty_parallel(*_args: object, **_kwargs: object) -> ParallelExecutor:
    """Create a synchronous stand-in for :class:`joblib.Parallel`."""

    def execute(tasks: Iterable[object]) -> Iterator[object]:
        list(tasks)
        return iter(())

    return execute


def make_one_result_parallel(result: object) -> Callable[..., ParallelExecutor]:
    """Create a synchronous stand-in yielding one completed task result."""

    def create_parallel(*_args: object, **_kwargs: object) -> ParallelExecutor:
        def execute(tasks: Iterable[object]) -> Iterator[object]:
            list(tasks)
            return iter((result,))

        return execute

    return create_parallel


def test_historical_multicall_without_hypersync_keeps_inline_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid a new cache-backed timestamp pass for callers without HyperSync."""

    def unexpected_timestamp_prefetch(*_args: object, **_kwargs: object) -> None:
        message = "Timestamp prefetch must require a HyperSync client"
        raise AssertionError(message)

    monkeypatch.setattr(multicall_batcher, "Parallel", make_empty_parallel)
    monkeypatch.setattr(multicall_batcher, "fetch_block_timestamps_multiprocess_auto_backend", unexpected_timestamp_prefetch)

    results = list(
        multicall_batcher.read_multicall_historical(
            chain_id=1,
            web3factory=lambda: None,
            calls=[],
            start_block=100,
            end_block=101,
            step=1,
            display_progress=False,
        )
    )

    assert results == []


def test_historical_multicall_closes_hypersync_timestamps_on_interruption(monkeypatch: pytest.MonkeyPatch) -> None:
    """Close the timestamp cache when a caller stops reading early."""

    timestamp = datetime.datetime(2026, 1, 1)
    timestamps = MagicMock()
    timestamps.get_last_block.return_value = 101
    timestamps.__getitem__.side_effect = lambda block_number: timestamp
    result = object()
    hypersync_client = object()

    def fetch_timestamps(*_args: object, **kwargs: object) -> MagicMock:
        assert kwargs["hypersync_client"] is hypersync_client
        return timestamps

    monkeypatch.setattr(multicall_batcher, "Parallel", make_one_result_parallel(result))
    monkeypatch.setattr(multicall_batcher, "fetch_block_timestamps_multiprocess_auto_backend", fetch_timestamps)

    reader = multicall_batcher.read_multicall_historical(
        chain_id=1,
        web3factory=lambda: None,
        calls=[],
        start_block=100,
        end_block=101,
        step=1,
        display_progress=False,
        hypersync_client=hypersync_client,
    )

    assert next(reader) is result
    reader.close()
    timestamps.close.assert_called_once_with()
