"""Regression tests for historical multicall timestamp handling."""

import datetime
from collections.abc import Callable, Iterable, Iterator
from unittest.mock import MagicMock

import pytest

from eth_defi.event_reader import multicall_batcher
from eth_defi.provider.rpcdb import RPCRequestStats

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


def make_executing_parallel(*_args: object, **_kwargs: object) -> ParallelExecutor:
    """Execute joblib delayed tuples synchronously for propagation tests."""

    def execute(tasks: Iterable[object]) -> Iterator[object]:
        for function, args, kwargs in tasks:
            yield function(*args, **kwargs)

    return execute


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


def test_historical_multicall_merges_worker_rpc_stats_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Merge a successful process task's physical calls into its phase."""

    timestamp = datetime.datetime(2026, 1, 1)
    worker_stats = RPCRequestStats()
    worker_stats.record_call("rpc.example", "eth_call", 2)
    worker_stats.record_error("rpc.example", "http_429", "rate limited")
    completed = multicall_batcher.CombinedEncodedCallResult(
        block_number=100,
        timestamp=timestamp,
        results=[],
        rpc_request_stats=worker_stats,
    )
    parent_stats = RPCRequestStats()

    monkeypatch.setattr(multicall_batcher, "Parallel", make_one_result_parallel(completed))

    results = list(
        multicall_batcher.read_multicall_historical(
            chain_id=1,
            web3factory=lambda: None,
            calls=[],
            start_block=100,
            end_block=101,
            step=1,
            display_progress=False,
            rpc_request_stats=parent_stats,
        )
    )

    calls, errors = parent_stats.export()
    assert results == [completed]
    assert calls == {("rpc.example", "eth_call"): 2}
    assert errors == {("rpc.example", "http_429", "rate limited"): 1}


@pytest.mark.parametrize("backend", ["loky", "threading"])
def test_chunked_multicall_counts_batch_once_without_double_merge(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
) -> None:
    """Five encoded inner calls spend one physical call on either backend."""

    parent_stats = RPCRequestStats()

    class FakeFactory:
        """Carry the shared accumulator expected by the thread path."""

        rpc_request_stats = parent_stats

        def __call__(self) -> None:
            """Satisfy the worker factory protocol; the mocked executor does not call it."""

    calls = [
        multicall_batcher.EncodedCall(
            func_name="probe",
            address="0x0000000000000000000000000000000000000001",
            data=bytes([index]),
            extra_data=None,
        )
        for index in range(5)
    ]

    def execute_task(task: multicall_batcher.MulticallHistoricalTask) -> multicall_batcher.CombinedEncodedCallResult:
        """Model the one outbound Multicall3 request made by a task batch."""

        task_stats = RPCRequestStats() if task.collect_rpc_request_stats else parent_stats
        task_stats.record_call("rpc.example", "eth_call")
        return multicall_batcher.CombinedEncodedCallResult(
            block_number=100,
            timestamp=task.timestamp,
            results=[],
            rpc_request_stats=task_stats if task.collect_rpc_request_stats else None,
        )

    monkeypatch.setattr(multicall_batcher, "Parallel", make_executing_parallel)
    monkeypatch.setattr(multicall_batcher, "_execute_multicall_subprocess", execute_task)

    results = list(
        multicall_batcher.read_multicall_chunked(
            chain_id=1,
            web3factory=FakeFactory(),
            calls=calls,
            block_identifier=100,
            max_workers=2,
            chunk_size=5,
            timestamped_results=False,
            backend=backend,
            rpc_request_stats=parent_stats,
        )
    )

    physical_calls, errors = parent_stats.export()
    assert results == []
    assert physical_calls == {("rpc.example", "eth_call"): 1}
    assert errors == {}
