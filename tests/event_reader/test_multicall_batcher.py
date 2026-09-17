"""Regression tests for historical multicall timestamp handling."""

import datetime
import json
from collections.abc import Callable, Iterable, Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from web3 import HTTPProvider, Web3

from eth_defi.event_reader import multicall_batcher
from eth_defi.provider.rpcdb import RPCRequestStats

ParallelExecutor = Callable[[Iterable[object]], Iterator[object]]
HTTP_OK = 200


def test_historical_state_error_classification() -> None:
    """Classify archive-retention errors separately from vault call failures."""

    missing_trie = "missing trie node deadbeef state is not available"
    missing_metadata = "metadata is not found, 227823446"
    stale_layer = "layer stale"

    assert multicall_batcher.is_historical_state_unavailable_error(missing_trie)
    assert multicall_batcher.is_historical_state_unavailable_error(missing_metadata)
    assert multicall_batcher.is_historical_state_unavailable_error(stale_layer)
    assert not multicall_batcher.is_historical_state_unavailable_error("execution reverted")


def test_historical_state_exception_is_not_same_provider_retryable() -> None:
    """Allow callers to rotate archives without entering batch-size retries."""

    error = multicall_batcher.MulticallHistoricalDataUnavailable("layer stale", status_code=HTTP_OK, headers={"endpoint_uri": "alchemy"})

    assert isinstance(error, multicall_batcher.MulticallNonRetryable)
    assert not isinstance(error, multicall_batcher.MulticallRetryable)
    assert error.status_code == HTTP_OK
    assert error.headers == {"endpoint_uri": "alchemy"}


def test_encoded_call_retries_allowlisted_timeout_when_other_errors_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry an opted-in transport timeout while preserving Solidity-revert handling.

    A metadata probe uses ``ignore_error`` because a non-ERC-4626 vault may
    revert its ``asset()`` method. It must still retry explicitly allowlisted
    transport errors instead of treating a temporary gateway outage as a
    conclusive missing method.
    """

    attempts = 0

    class FakeEth:
        """Return after one selected transient failure."""

        chain_id = 8453

        def call(self, transaction: dict, block_identifier: int) -> bytes:
            """Fail once without serialising Python exception classes."""

            nonlocal attempts
            assert block_identifier == 50_395_218
            assert "retry_exceptions" not in transaction
            json.dumps(transaction)
            attempts += 1
            if attempts == 1:
                raise multicall_batcher.ReadTimeout("dRPC timed out")
            return b"reply"

    monkeypatch.setattr(multicall_batcher.time, "sleep", lambda _seconds: None)
    call = multicall_batcher.EncodedCall.from_keccak_signature(
        address="0x0000000000000000000000000000000000000001",
        signature=b"\x00\x00\x00\x00",
        function="asset",
        data=b"",
        extra_data=None,
    )

    result = call.call(
        SimpleNamespace(eth=FakeEth()),
        block_identifier=50_395_218,
        ignore_error=True,
        attempts=1,
        retry_sleep=0,
        retry_exceptions={multicall_batcher.ReadTimeout},
    )

    assert result == b"reply"
    assert attempts == 2


def test_encoded_call_allowlist_is_not_sent_to_plain_http_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry exception classes out of a JSON-RPC transaction payload.

    A direct ``HTTPProvider`` has no fallback layer to remove internal retry
    hints. The transaction must therefore remain JSON-serialisable.
    """

    provider = HTTPProvider("https://plain-provider.example")
    web3 = Web3(provider)
    requests: list[tuple[str, list]] = []

    def capture_request(method: str, params: list) -> dict:
        """Record the formatted request without making a network call."""

        json.dumps({"method": method, "params": params})
        requests.append((method, params))
        if method == "eth_chainId":
            return {"jsonrpc": "2.0", "id": 1, "result": hex(8453)}
        return {"jsonrpc": "2.0", "id": 1, "result": "0x"}

    monkeypatch.setattr(provider, "make_request", capture_request)
    call = multicall_batcher.EncodedCall.from_keccak_signature(
        address="0x0000000000000000000000000000000000000001",
        signature=b"\x00\x00\x00\x00",
        function="asset",
        data=b"",
        extra_data=None,
    )

    assert (
        call.call(
            web3,
            block_identifier=50_395_218,
            gas=1_000_000,
            ignore_error=True,
            retry_exceptions={multicall_batcher.ReadTimeout},
        )
        == b""
    )
    eth_call_params = next(params for method, params in requests if method == "eth_call")
    assert "retry_exceptions" not in eth_call_params[0]


def test_encoded_call_requires_ignore_error_for_retry_allowlist() -> None:
    """Reject an allow-list whose retry semantics would otherwise be a no-op."""

    call = multicall_batcher.EncodedCall.from_keccak_signature(
        address="0x0000000000000000000000000000000000000001",
        signature=b"\x00\x00\x00\x00",
        function="asset",
        data=b"",
        extra_data=None,
    )

    with pytest.raises(AssertionError, match="retry_exceptions requires ignore_error=True"):
        call.call(
            SimpleNamespace(eth=SimpleNamespace(chain_id=8453)),
            block_identifier=50_395_218,
            gas=1_000_000,
            retry_exceptions={multicall_batcher.ReadTimeout},
        )


def test_encoded_call_does_not_retry_solidity_revert_with_timeout_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep an ignored Solidity-level revert outside a timeout retry allow-list."""

    attempts = 0

    class FakeEth:
        """Always simulate a contract revert."""

        chain_id = 8453

        def call(self, transaction: dict, block_identifier: int) -> bytes:
            """Raise a non-allowlisted EVM error."""

            nonlocal attempts
            attempts += 1
            raise ValueError({"code": 3, "message": "execution reverted"})

    monkeypatch.setattr(multicall_batcher.time, "sleep", lambda _seconds: None)
    call = multicall_batcher.EncodedCall.from_keccak_signature(
        address="0x0000000000000000000000000000000000000001",
        signature=b"\x00\x00\x00\x00",
        function="asset",
        data=b"",
        extra_data=None,
    )

    with pytest.raises(ValueError, match="execution reverted"):
        call.call(
            SimpleNamespace(eth=FakeEth()),
            block_identifier=50_395_218,
            ignore_error=True,
            attempts=2,
            retry_sleep=0,
            retry_exceptions={multicall_batcher.ReadTimeout},
        )

    assert attempts == 1


def test_historical_state_rotation_wraps_from_last_provider_to_first_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry a failed third provider at providers one and two exactly once."""

    class FakeFallbackProvider:
        def __init__(self) -> None:
            self.providers = [SimpleNamespace(endpoint_uri="https://first.example"), SimpleNamespace(endpoint_uri="https://second.example"), SimpleNamespace(endpoint_uri="https://third.example")]
            self.currently_active_provider = 2
            self.switched_to: list[int] = []

        def get_active_provider(self) -> SimpleNamespace:
            return self.providers[self.currently_active_provider]

        def switch_to_provider_index(self, index: int, **_kwargs: object) -> None:
            self.currently_active_provider = index
            self.switched_to.append(index)

    fallback_provider = FakeFallbackProvider()
    reader = object.__new__(multicall_batcher.MultiprocessMulticallReader)
    reader.web3 = SimpleNamespace(provider=fallback_provider, eth=SimpleNamespace(chain_id=42_161))
    attempted_indices: list[int] = []

    def fake_call_multicall_with_batch_size(_reader: object, **_kwargs: object) -> list[tuple[bool, bytes]]:
        attempted_indices.append(fallback_provider.currently_active_provider)
        if fallback_provider.currently_active_provider == 0:
            message = "missing trie node"
            raise multicall_batcher.MulticallHistoricalDataUnavailable(message)
        return [(True, b"result")]

    monkeypatch.setattr(multicall_batcher, "FallbackProvider", FakeFallbackProvider)
    monkeypatch.setattr(multicall_batcher, "get_multicall_contract", lambda *_args, **_kwargs: object())
    reader.call_multicall_with_batch_size = fake_call_multicall_with_batch_size

    result = reader.retry_historical_state_with_provider_rotation(
        block_identifier=421_460_233,
        batch_size=40,
        encoded_calls=[],
        require_multicall_result=False,
        error=multicall_batcher.MulticallHistoricalDataUnavailable("layer stale"),
    )

    assert fallback_provider.switched_to == [0, 1]
    assert attempted_indices == [0, 1]
    assert result == [(True, b"result")]


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

    timestamp = datetime.datetime.fromisoformat("2026-01-01")
    timestamps = MagicMock()
    timestamps.get_last_block.return_value = 101
    timestamps.__getitem__.side_effect = lambda _block_number: timestamp
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

    timestamp = datetime.datetime.fromisoformat("2026-01-01")
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
        """Provide a worker factory without an attached accumulator."""

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

        task_stats = RPCRequestStats() if task.collect_rpc_request_stats else task.rpc_request_stats
        assert task_stats is not None
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
