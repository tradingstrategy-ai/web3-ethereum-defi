"""Exercise real Multicall encoding with a deterministic transport recorder.

Anvil cannot reproduce HyperCore's provider-specific precompile accounting. These
fixtures therefore control the transport while using the actual ABI encoder,
request planner, retry code and result reconstruction. A separate guarded manual
script checks the configured real providers without modifying scanner state.
"""

import datetime
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from eth_abi import decode, encode
from eth_typing import HexAddress
from requests import Response
from requests.exceptions import HTTPError, ReadTimeout
from web3 import HTTPProvider, Web3

from eth_defi.erc_4626 import classification
from eth_defi.event_reader import multicall_batcher
from eth_defi.event_reader.multicall_batcher import (
    EncodedCall,
    MulticallHistoricalDataUnavailable,
    MulticallRetryable,
    MulticallRetryExhausted,
    MultiprocessMulticallReader,
    plan_multicall_batches,
)
from eth_defi.hyperliquid.constants import HYPEREVM_MULTICALL_GREYLIST
from eth_defi.provider.rpcdb import RPCOperationRecorder, RPCRequestStats
from eth_defi.vault.rpc_scan_state import classify_rpc_scan_failure

HYPED = HexAddress("0x4d0fF6a0DD9f7316b674Fb37993A3Ce28BEA340e")
REGULAR = HexAddress("0x0000000000000000000000000000000000000001")
BLOCK = 47_561_964


@pytest.fixture
def recording_reader(monkeypatch: pytest.MonkeyPatch) -> Callable:
    """Use actual ABI bindings with provider-controlled per-request failures.

    The returned factory creates a reader and a chronological record of encoded
    physical requests. Response values identify input selectors, allowing tests
    to detect dropped, duplicated or incorrectly reordered results.
    """
    monkeypatch.setattr("eth_defi.event_reader.multicall_batcher.time.sleep", lambda _seconds: None)

    def create(chain: int = 999, batch_size: int = 3, greylist_batch_size: int = 1, greylist: frozenset[HexAddress] | None = None, fail: Callable | None = None) -> tuple[MultiprocessMulticallReader, list, RPCRequestStats]:
        """Create an isolated reader; the optional callback rejects a request."""
        stats = RPCRequestStats(operation="historical_multicall")
        requests = []
        provider = HTTPProvider("https://rpc.example")
        provider.rpc_request_stats = stats

        def request(method: str, params: list) -> dict:
            """Decode real payloads and emulate provider results at source block."""
            provider.rpc_request_stats.record_call("rpc.example", method)
            if method == "eth_chainId":
                result = hex(chain)
            elif method == "eth_blockNumber":
                result = hex(BLOCK)
            elif method == "eth_getCode":
                result = "0x01"
            elif method == "eth_call":
                _, calls = decode(["bool", "(address,bytes)[]"], bytes.fromhex(params[0]["data"][10:]))
                requests.append((calls, params[1]))
                if fail:
                    fail(calls, len(requests))
                outputs = [(True, data[-1:].rjust(32, b"\0")) for _target, data in calls]
                result = "0x" + encode(["uint256", "bytes32", "(bool,bytes)[]"], [BLOCK, b"\0" * 32, outputs]).hex()
            else:
                raise AssertionError(method)
            return {"jsonrpc": "2.0", "id": 1, "result": result}

        monkeypatch.setattr(provider, "make_request", request)
        policy = greylist if greylist is not None else (HYPEREVM_MULTICALL_GREYLIST if chain == 999 else frozenset())
        reader = MultiprocessMulticallReader(Web3(provider), batch_size=batch_size, greylist_batch_size=greylist_batch_size, greylist=policy)
        return reader, requests, stats

    return create


def calls_for(addresses: list[HexAddress]) -> list[EncodedCall]:
    """Build calls with distinct outputs despite repeated target addresses.

    Positions, not address keys, identify results. The extra_data value stands in
    for the historical reader's vault/function routing metadata.

    :param addresses: Targets in deliberately interleaved input order.
    :return: Calls whose final calldata byte identifies their original position.
    """
    return [EncodedCall("test", address, b"\x01\xe1\xd1\x14" + bytes([index]), extra_data={"index": index}) for index, address in enumerate(addresses)]


@pytest.mark.parametrize("chain,small", [(999, 1), (999, 2), (1, 1)])
def test_transport_lane_composition_and_result_identity(recording_reader: Callable, chain: int, small: int) -> None:
    """Restore interleaved duplicate inputs after separate physical lane requests."""
    reader, requests, stats = recording_reader(chain=chain, greylist_batch_size=small)
    inputs = calls_for([HYPED, REGULAR, HYPED, REGULAR, HYPED])
    timestamp = datetime.datetime(2026, 10, 3)
    outputs = list(reader.process_calls(BLOCK, inputs, timestamp=timestamp))
    assert [output.call for output in outputs] == inputs
    assert [int.from_bytes(output.result, "big") for output in outputs] == list(range(5))
    assert all(output.block_identifier == BLOCK and output.timestamp == timestamp for output in outputs)
    assert all(block == hex(BLOCK) for _calls, block in requests)
    if chain == 999:
        assert all(target == REGULAR.lower() for target, _data in requests[0][0])
        assert all(len(batch) <= small and all(target == HYPED.lower() for target, _data in batch) for batch, _block in requests[1:])
        expected = 1 + (3 + small - 1) // small
        assert stats.operation_calls["historical_multicall", "rpc.example", "eth_call"] == 1
        assert stats.operation_calls["historical_multicall_greylist", "rpc.example", "eth_call"] == expected - 1
    else:
        expected = 2
    assert len(requests) == expected
    assert stats.calls["rpc.example", "eth_call"] == expected
    assert "eth_getBlockByNumber" not in {method for _provider, method in stats.calls}


def test_planner_never_combines_greylisted_targets() -> None:
    """Even an explicit larger isolated limit cannot mix independent Core targets."""
    targets = sorted(HYPEREVM_MULTICALL_GREYLIST)[:2]
    encoded = [(target, b"selector") for target in targets for _ in range(3)]
    batches = list(plan_multicall_batches(encoded, 40, 2, greylist=HYPEREVM_MULTICALL_GREYLIST))
    assert [len(indexes) for _grey, indexes in batches] == [2, 1, 2, 1]
    assert all(grey and len({encoded[index][0] for index in indexes}) == 1 for grey, indexes in batches)
    assert list(plan_multicall_batches([], 40)) == []


def test_retry_resumes_after_successful_fragment(recording_reader: Callable) -> None:
    """A reduced retry may fail later; neither regular prefix gets replayed."""

    def fail(batch: tuple, attempt: int) -> None:
        """Reject the second full batch, then its second one-call fragment."""
        if attempt in (2, 4):
            raise ValueError({"code": -32003, "message": "out of gas"})

    reader, requests, _stats = recording_reader(chain=1, batch_size=3, fail=fail)
    outputs = list(reader.process_calls(BLOCK, calls_for([REGULAR] * 6)))
    assert [int.from_bytes(output.result, "big") for output in outputs] == list(range(6))
    assert [[data[-1] for _address, data in batch] for batch, _block in requests] == [[0, 1, 2], [3, 4, 5], [3], [4], [4], [5]]


@pytest.mark.parametrize("gas_message", ["out of gas", "BasicOutOfGas"])
def test_greylist_gas_failure_is_unavailable_without_replaying_regular(recording_reader: Callable, gas_message: str) -> None:
    """A sole provider's gas rejection does not warrant repeated one-call probes."""

    def fail(batch: tuple, _attempt: int) -> None:
        """Reject only the reviewed target with the documented provider symptom."""
        if batch[0][0] == HYPED.lower():
            raise ValueError({"code": -32003, "message": gas_message})

    reader, requests, stats = recording_reader(fail=fail)
    outputs = list(reader.process_calls(BLOCK, calls_for([HYPED, REGULAR]), allow_greylist_unavailable=True))
    assert outputs[0].unavailable_error and not outputs[0].success
    assert outputs[0].result == b"" and outputs[0].revert_exception is None
    assert outputs[1].success
    assert len(requests) == 2
    assert stats.operation_calls["historical_multicall_greylist", "rpc.example", "eth_call"] == 1


def test_greylist_archive_gap_remains_hard_error(recording_reader: Callable) -> None:
    """Isolation must not conceal provider archive outages as target-local gas."""

    def fail(_batch: tuple, _attempt: int) -> None:
        """Emulate a chain-wide state retention failure."""
        raise ValueError("missing trie node")

    reader, requests, _stats = recording_reader(fail=fail)
    with pytest.raises(MulticallHistoricalDataUnavailable):
        list(reader.process_calls(BLOCK, calls_for([HYPED])))
    assert len(requests) == 1


@pytest.mark.parametrize("stateful", [False, True])
@pytest.mark.parametrize("allow_unavailable", [False, True])
def test_historical_consumers_must_opt_in_to_unavailable_results(recording_reader: Callable, monkeypatch: pytest.MonkeyPatch, stateful: bool, allow_unavailable: bool) -> None:
    """Generic historical readers cannot silently inherit price-only deferral.

    Exercise both public generators through real ABI requests and the actual
    worker executor. Synchronous scheduling keeps this transport fixture local;
    the API boundary still determines whether a gas failure raises or survives
    as explicit unavailable data for a preservation-aware caller.
    """

    def reject_core(_batch: tuple, _attempt: int) -> None:
        """Reproduce the documented provider gas rejection with one endpoint."""
        raise ValueError({"code": -32003, "message": "out of gas"})

    reader, requests, _stats = recording_reader(fail=reject_core)
    timestamp = datetime.datetime(2026, 10, 3)
    monkeypatch.setattr(reader, "fetch_block_timestamp", lambda _block: timestamp)

    def create_worker_reader(*_args: object, greylist: frozenset[HexAddress], **_kwargs: object) -> MultiprocessMulticallReader:
        """Check policy survives the public generator and task payload."""
        assert greylist == HYPEREVM_MULTICALL_GREYLIST
        return reader

    monkeypatch.setattr(multicall_batcher, "MultiprocessMulticallReader", create_worker_reader)
    monkeypatch.setattr(multicall_batcher, "_reader_instance", threading.local())

    def execute_locally(*_args: object, **_kwargs: object) -> Callable:
        """Keep normal delayed-task execution while replacing process dispatch."""
        return lambda tasks: (function(*args, **kwargs) for function, args, kwargs in tasks)

    monkeypatch.setattr(multicall_batcher, "Parallel", execute_locally)
    timestamps = MagicMock()
    timestamps.get_last_block.return_value = BLOCK
    timestamps.__getitem__.return_value = timestamp
    monkeypatch.setattr(multicall_batcher, "fetch_block_timestamps_multiprocess_auto_backend", lambda **_: timestamps)
    calls = calls_for([HYPED])
    if stateful:
        states = {call: SimpleNamespace(should_invoke=lambda *_: True, vault_poll_frequency="test", unsupported_token=False) for call in calls}
        generator = multicall_batcher.read_multicall_historical_stateful
    else:
        states = calls
        generator = multicall_batcher.read_multicall_historical
    options = {"allow_greylist_unavailable": True} if allow_unavailable else {}
    outputs = generator(chain_id=999, web3factory=lambda: None, calls=states, start_block=BLOCK, end_block=BLOCK + 1, step=1, max_workers=1, display_progress=False, greylist=HYPEREVM_MULTICALL_GREYLIST, **options)
    if allow_unavailable:
        served = list(outputs)
        assert len(served) == 1 and served[0].results[0].unavailable_error
    else:
        with pytest.raises(MulticallRetryExhausted):
            list(outputs)
    assert len(requests) == 1


@pytest.mark.parametrize("failure", ["timeout", "rate_limit", "consensus"])
def test_greylisted_transient_errors_keep_normal_recovery(recording_reader: Callable, failure: str) -> None:
    """A known Core target must still recover ordinary provider outages."""

    def fail_once(_batch: tuple, attempt: int) -> None:
        """Fail the first physical request with a genuine non-gas symptom."""
        if attempt != 1:
            return
        if failure == "timeout":
            raise ReadTimeout("provider timeout")
        if failure == "rate_limit":
            response = Response()
            response.status_code = 429
            raise HTTPError("rate limited", response=response)
        raise ValueError({"code": -32090, "message": "not enough agreement among responses"})

    reader, requests, _stats = recording_reader(fail=fail_once)
    results = list(reader.process_calls(BLOCK, calls_for([HYPED]), allow_greylist_unavailable=True))
    assert len(results) == 1 and results[0].success
    assert results[0].unavailable_error is None
    assert len(requests) == 2


def test_exhausted_greylist_timeout_is_hard_error(recording_reader: Callable) -> None:
    """All normal retries run before an outage aborts; it cannot become missing NAV."""

    def fail(_batch: tuple, _attempt: int) -> None:
        """Keep the sole endpoint unavailable for every bounded attempt."""
        raise ReadTimeout("provider timeout")

    reader, requests, _stats = recording_reader(fail=fail)
    with pytest.raises(MulticallRetryExhausted):
        list(reader.process_calls(BLOCK, calls_for([HYPED]), allow_greylist_unavailable=True))
    assert len(requests) == 6


@pytest.mark.parametrize("first_timeout,gas_message", [(False, "out of gas"), (False, "BasicOutOfGas"), (True, "out of gas")])
def test_three_provider_gas_recovery_is_bounded(monkeypatch: pytest.MonkeyPatch, first_timeout: bool, gas_message: str) -> None:
    """Three endpoints receive at most three aggregate attempts before deferral.

    A preceding timeout consumes a recovery round rather than resetting the
    budget when the next provider reports gas. This test pins the shared-round
    semantics and ensures gas retries do not add backoff sleeps.
    """
    provider = SimpleNamespace(providers=[object(), object(), object()], currently_active_provider=0)
    attempts = []
    sleeps = []

    def switch(**_: object) -> None:
        """Rotate deterministically through configured provider identities."""
        provider.currently_active_provider = (provider.currently_active_provider + 1) % 3

    def restore(index: int, **_: object) -> None:
        """Restore the regular lane once after all isolated recovery rounds."""
        provider.currently_active_provider = index

    provider.switch_provider = switch
    provider.switch_to_provider_index = restore
    reader = object.__new__(MultiprocessMulticallReader)
    reader.web3 = SimpleNamespace(provider=provider)
    reader.chain_id = 999
    reader.batch_size = 40
    reader.greylist_batch_size = 1
    reader.greylist = HYPEREVM_MULTICALL_GREYLIST
    reader.calls = reader.last_switch = 0
    reader.backswitch_threshold = 100
    reader.rate_limit_sleep = 0
    monkeypatch.setattr(multicall_batcher, "FallbackProvider", SimpleNamespace)
    monkeypatch.setattr(multicall_batcher, "get_provider_name", lambda _: "rpc.example")
    monkeypatch.setattr(multicall_batcher, "get_multicall_contract", lambda *_, **__: object())
    monkeypatch.setattr(multicall_batcher, "resolve_hyperevm_consensus_failover", lambda *_: None)
    monkeypatch.setattr(multicall_batcher.time, "sleep", sleeps.append)

    def reject(_contract: object, _block: int, _size: int, _calls: list, _strict: bool) -> list:
        """Record physical attempts and preserve the underlying provider cause."""
        attempts.append(provider.currently_active_provider)
        cause = ReadTimeout("provider timeout") if first_timeout and len(attempts) == 1 else ValueError(gas_message)
        raise MulticallRetryable(str(cause)) from cause

    monkeypatch.setattr(reader, "fetch_multicall_with_batch_size", reject)
    results = list(reader.process_calls(BLOCK, calls_for([HYPED]), allow_greylist_unavailable=True))
    assert results[0].unavailable_error and not results[0].success
    assert attempts == [0, 1, 2]
    assert sleeps == ([2.0] if first_timeout else [])
    assert provider.currently_active_provider == 0


@pytest.mark.parametrize("restore_failure", [False, True])
@pytest.mark.parametrize("exceptional", [False, True])
def test_greylist_failover_restores_regular_provider(monkeypatch: pytest.MonkeyPatch, restore_failure: bool, exceptional: bool) -> None:
    """Restore selection without masking results or an already propagating error."""

    class Provider:
        """Minimal provider state machine, matching the worker's ownership model."""

        providers = [object(), object()]
        currently_active_provider = 0
        rpc_request_stats = None

        def switch_provider(self, **_: object) -> None:
            """Record one deterministic fallback rotation."""
            self.currently_active_provider = 1
            switches.append(1)

        def switch_to_provider_index(self, index: int, **_: object) -> None:
            """Record restoration after the isolated request succeeds."""
            switches.append(index)
            self.rpc_request_stats.record_call("rpc.example", "eth_chainId")
            if restore_failure:
                raise multicall_batcher.ChainIdMismatch("original endpoint cannot be verified")
            self.currently_active_provider = index

    switches = []
    provider = Provider()
    stats = RPCRequestStats(operation="historical_multicall")
    provider.rpc_request_stats = stats
    reader = object.__new__(MultiprocessMulticallReader)
    reader.web3 = SimpleNamespace(provider=provider, eth=SimpleNamespace(chain_id=999))
    reader.chain_id = 999
    reader.batch_size = 40
    reader.greylist_batch_size = 1
    reader.greylist = HYPEREVM_MULTICALL_GREYLIST
    reader.calls = reader.last_switch = 0
    reader.backswitch_threshold = 100
    reader.rate_limit_sleep = 0
    monkeypatch.setattr(multicall_batcher, "FallbackProvider", Provider)
    monkeypatch.setattr(multicall_batcher, "get_provider_name", lambda _provider: "rpc.example")
    monkeypatch.setattr(multicall_batcher, "get_multicall_contract", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(multicall_batcher, "resolve_hyperevm_consensus_failover", lambda *_args: None)

    def invoke(_contract: object, _block: int, _size: int, encoded: list, _strict: bool) -> list:
        """Fail the isolated target on primary; succeed on the alternate."""
        provider.rpc_request_stats.record_call("rpc.example", "eth_call")
        if encoded[0][0].lower() == HYPED.lower() and provider.currently_active_provider == 0:
            raise MulticallRetryable("out of gas")
        if encoded[0][0].lower() == HYPED.lower() and exceptional:
            raise multicall_batcher.MulticallNonRetryable("invalid request")
        return [(True, b"result")] * len(encoded)

    monkeypatch.setattr(reader, "fetch_multicall_with_batch_size", invoke)
    if exceptional:
        with pytest.raises(multicall_batcher.MulticallNonRetryable, match="invalid request"):
            list(reader.process_calls(BLOCK, calls_for([REGULAR, HYPED])))
    else:
        results = list(reader.process_calls(BLOCK, calls_for([REGULAR, HYPED])))
        assert all(result.success for result in results)
    assert provider.rpc_request_stats is stats
    assert stats.operation == "historical_multicall"
    assert stats.operation_calls["historical_multicall", "rpc.example", "eth_call"] == 1
    assert stats.operation_calls["historical_multicall_greylist", "rpc.example", "eth_call"] == 2
    assert stats.operation_calls["historical_multicall_greylist", "rpc.example", "eth_chainId"] == 1
    assert sum(stats.operation_calls.values()) == sum(stats.calls.values())
    assert switches == [1, 0]
    assert provider.currently_active_provider == (1 if restore_failure else 0)


def test_metadata_callers_keep_strict_gas_errors(recording_reader: Callable) -> None:
    """Token/feature consumers cannot silently cache RPC failures as missing methods."""

    def fail(_batch: tuple, _attempt: int) -> None:
        """Emulate the provider gas response, not a served contract revert."""
        raise ValueError({"code": -32003, "message": "out of gas"})

    reader, _requests, _stats = recording_reader(fail=fail)
    with pytest.raises(RuntimeError, match="physical-batch retries exhausted"):
        list(reader.process_calls(BLOCK, calls_for([HYPED])))


def test_recovery_wrapper_preserves_transport_classification() -> None:
    """The scheduler sees the underlying timeout through both recovery wrappers."""
    failure = MulticallRetryable("physical request failed")
    failure.__cause__ = ReadTimeout("upstream timeout")
    exhausted = MulticallRetryExhausted("Multicall physical-batch retries exhausted")
    exhausted.__cause__ = failure
    assert isinstance(exhausted, RuntimeError)
    assert classify_rpc_scan_failure(exhausted) == "transient"


@pytest.mark.parametrize("gas_after_archive", [False, True])
def test_archive_rotation_preserves_prefix_and_enters_gas_recovery(monkeypatch: pytest.MonkeyPatch, gas_after_archive: bool) -> None:
    """Archive rotation cannot replay served fragments or bypass gas recovery."""

    class Provider:
        """Cycle over three configured providers without performing network I/O."""

        providers = [object(), object(), object()]
        currently_active_provider = 0

        def switch_to_provider_index(self, index: int, **_: object) -> None:
            """Select the next configured archive."""
            self.currently_active_provider = index

        def switch_provider(self, **_: object) -> None:
            """Advance during bounded transport recovery after an archive rotation."""
            self.currently_active_provider = (self.currently_active_provider + 1) % 3

        def get_active_provider(self) -> object:
            """Expose the current identity for existing recovery diagnostics."""
            return self.providers[self.currently_active_provider]

    provider = Provider()
    reader = object.__new__(MultiprocessMulticallReader)
    reader.web3 = SimpleNamespace(provider=provider, eth=SimpleNamespace(chain_id=42161))
    reader.chain_id = 42161
    reader.calls = 0
    reader.rate_limit_sleep = 0
    inputs = [(REGULAR, bytes([index])) for index in range(3)]
    attempted = []
    monkeypatch.setattr(multicall_batcher, "FallbackProvider", Provider)
    monkeypatch.setattr(multicall_batcher, "get_provider_name", lambda _provider: "rpc.example")
    monkeypatch.setattr(multicall_batcher, "get_multicall_contract", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(multicall_batcher.time, "sleep", lambda _seconds: None)

    def invoke(_contract: object, block_identifier: int, batch_size: int, encoded_calls: list, require_multicall_result: bool) -> list:
        """Serve one fragment before each different failure condition."""
        attempted.append([data[0] for _target, data in encoded_calls])
        if provider.currently_active_provider == 0:
            raise MulticallHistoricalDataUnavailable("missing trie node", completed_results=[(True, b"0")])
        if provider.currently_active_provider == 1:
            if gas_after_archive:
                raise MulticallRetryable("out of gas", completed_results=[(True, b"1")])
            raise MulticallHistoricalDataUnavailable("layer stale", completed_results=[(True, b"1")])
        return [(True, b"2")]

    monkeypatch.setattr(reader, "fetch_multicall_with_batch_size", invoke)
    results = reader.fetch_multicall_batch_with_retries(object(), BLOCK, 3, inputs, False, 5)
    assert results == [(True, b"0"), (True, b"1"), (True, b"2")]
    assert attempted == [[0, 1, 2], [1, 2], [2]]


@pytest.mark.parametrize("chain", [1, 999])
@pytest.mark.parametrize("inject_policy", [False, True])
def test_reader_uses_only_supplied_greylist(recording_reader: Callable, chain: int, inject_policy: bool) -> None:
    """Caller policy, rather than chain ID or built-in addresses, determines lanes.

    Use an ordinary address absent from the HyperEVM list as the isolated target.
    The known HYPED address must remain regular when the caller did not select it.

    :param recording_reader: ABI-backed transport recorder.
    :param chain: Connection identity, independent of request routing.
    :param inject_policy: Whether the ordinary target is explicitly isolated.
    :return: None; assert physical request composition and restored input order.
    """
    policy = frozenset({REGULAR}) if inject_policy else frozenset()
    reader, requests, _stats = recording_reader(chain=chain, greylist=policy)
    inputs = calls_for([REGULAR, HYPED, REGULAR])
    outputs = list(reader.process_calls(BLOCK, inputs))
    assert [result.call for result in outputs] == inputs
    assert len(requests) == (3 if inject_policy else 1)
    if inject_policy:
        assert requests[0][0][0][0] == HYPED.lower()
        assert all(batch[0][0] == REGULAR for batch, _block in requests[1:])
    else:
        assert len(requests[0][0]) == 3


def test_worker_cache_separates_greylist_policies(recording_reader: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recycled workers cannot retain a previous task's routing exceptions.

    A shared factory identity reproduces joblib worker reuse across callers. A
    changed policy creates a separate reader, while checksum-only differences
    reuse its canonical policy without opening another provider connection.

    :param recording_reader: ABI-backed transport recorder.
    :param monkeypatch: Reset thread-local worker connections for this test.
    :return: None; assert distinct cached readers and physical batch composition.
    """
    reader, requests, _stats = recording_reader(chain=1, greylist=frozenset())
    monkeypatch.setattr(multicall_batcher, "_reader_instance", threading.local())
    factory = lambda: reader.web3
    inputs = calls_for([REGULAR, HYPED])
    timestamp = datetime.datetime(2026, 10, 5)
    for policy in (frozenset(), frozenset({HYPED}), frozenset({HexAddress(HYPED.lower())})):
        task = multicall_batcher.MulticallHistoricalTask(1, factory, BLOCK, inputs, timestamp=timestamp, greylist=policy)
        result = multicall_batcher._execute_multicall_in_worker(task)
        assert [output.call for output in result.results] == inputs
    assert len(multicall_batcher._reader_instance.per_chain_readers) == 2
    assert [len(batch) for batch, _block in requests] == [2, 1, 1, 1, 1]


def test_chunked_reader_passes_supplied_greylist(recording_reader: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    """Feature-style chunked tasks use caller policy without importing HyperEVM.

    Run the public chunked generator against actual encoded transport requests.
    Synchronous joblib dispatch keeps the deterministic recorder local while
    exercising task construction and the real worker cache and reader together.

    :param recording_reader: ABI-backed transport recorder.
    :param monkeypatch: Replace scheduling and reset worker cache for this test.
    :return: None; assert isolated requests and input identity.
    """
    reader, requests, _stats = recording_reader(chain=1, greylist=frozenset())
    monkeypatch.setattr(multicall_batcher, "_reader_instance", threading.local())

    def execute_locally(*_args: object, **_kwargs: object) -> Callable:
        """Keep delayed worker execution without a background thread."""
        return lambda tasks: (function(*args, **kwargs) for function, args, kwargs in tasks)

    monkeypatch.setattr(multicall_batcher, "Parallel", execute_locally)
    inputs = calls_for([REGULAR, HYPED, REGULAR])
    outputs = list(
        multicall_batcher.read_multicall_chunked(
            chain_id=1,
            web3factory=lambda: reader.web3,
            calls=inputs,
            block_identifier=BLOCK,
            max_workers=1,
            timestamped_results=False,
            greylist=frozenset({REGULAR}),
        )
    )
    assert [output.call for output in outputs] == inputs
    assert [len(batch) for batch, _block in requests] == [1, 1, 1]
    assert requests[0][0][0][0] == HYPED.lower()


@pytest.mark.parametrize("single_vault", [False, True])
def test_feature_detectors_pass_generic_greylist(recording_reader: Callable, monkeypatch: pytest.MonkeyPatch, single_vault: bool) -> None:
    """Both bulk and one-off detectors preserve explicit caller isolation.

    Replace only probe generation and protocol inference, retaining the actual
    transport and Multicall worker stack. An ordinary Ethereum address selected
    by the caller must produce separate subcall requests on either path.

    :param recording_reader: ABI-backed transport recorder.
    :param monkeypatch: Replace protocol fixtures and synchronous task dispatch.
    :param single_vault: Select the private reader or public chunked feature path.
    :return: None; assert three real encoded requests for the isolated target.
    """
    reader, requests, _stats = recording_reader(chain=1, greylist=frozenset())
    inputs = calls_for([REGULAR] * 3)
    monkeypatch.setattr(classification, "create_probe_calls", lambda *_args, **_kwargs: inputs)
    monkeypatch.setattr(classification, "identify_vault_features", lambda *_args, **_kwargs: set())
    monkeypatch.setattr(multicall_batcher, "_reader_instance", threading.local())

    def execute_locally(*_args: object, **_kwargs: object) -> Callable:
        """Retain real workers without a separate scheduling thread."""
        return lambda tasks: (function(*args, **kwargs) for function, args, kwargs in tasks)

    monkeypatch.setattr(multicall_batcher, "Parallel", execute_locally)
    if single_vault:
        assert classification.detect_vault_features(reader.web3, REGULAR, verbose=False, greylist=frozenset({REGULAR})) == set()
    else:
        probes = list(classification.probe_vaults(1, lambda: reader.web3, [REGULAR], BLOCK, max_workers=1, greylist=frozenset({REGULAR})))
        assert len(probes) == 1 and probes[0].address == REGULAR
    assert [len(batch) for batch, _block in requests] == [1, 1, 1]


@pytest.mark.parametrize("allow_unavailable", [False, True])
def test_greylist_restores_callers_labelled_recorder(recording_reader: Callable, allow_unavailable: bool) -> None:
    """Nested caller labels survive isolated failures without losing attempts.

    A caller may bind a fixed operation label before passing its connection to
    Multicall. A greylist failure must retain that exact binding, with attempts
    already in the shared counters even when the reader propagates the error.

    :param recording_reader: ABI-backed transport recorder.
    :param allow_unavailable: Select strict failure or preservation-aware deferral.
    :return: None; assert exact totals, lane attribution and binding restoration.
    """

    def reject_greylist(batch: tuple, _attempt: int) -> None:
        """Reject only the isolated address with the documented gas symptom."""
        if batch[0][0] == HYPED.lower():
            raise ValueError({"code": -32003, "message": "out of gas"})

    reader, requests, stats = recording_reader(fail=reject_greylist)
    recorder = RPCOperationRecorder(stats, "caller")
    reader.web3.provider.rpc_request_stats = recorder
    if allow_unavailable:
        results = list(reader.process_calls(BLOCK, calls_for([REGULAR, HYPED]), allow_greylist_unavailable=True))
        assert results[0].success and results[1].unavailable_error
    else:
        with pytest.raises(MulticallRetryExhausted):
            list(reader.process_calls(BLOCK, calls_for([REGULAR, HYPED])))
    assert reader.web3.provider.rpc_request_stats is recorder
    assert len(requests) == 2
    assert stats.operation_calls["caller", "rpc.example", "eth_call"] == 1
    assert stats.operation_calls["caller_greylist", "rpc.example", "eth_call"] == 1
    assert stats.calls["rpc.example", "eth_call"] == 2
    assert stats.operation == "historical_multicall"


def test_worker_owned_providers_share_counters_with_distinct_lanes(recording_reader: Callable) -> None:
    """A provider's isolated binding cannot relabel another worker's requests.

    Both actual ABI-backed readers write into the same phase accumulator. A
    barrier keeps their transport calls overlapping, exercising the supported
    ownership model: mutable providers are private, while counters are shared.

    :param recording_reader: Factory for separately owned provider connections.
    :return: None; assert one physical attempt in each operation partition.
    """
    barrier = threading.Barrier(2)

    def overlap(_batch: tuple, _attempt: int) -> None:
        """Hold each encoded request until the other worker has entered transport."""
        barrier.wait(timeout=10)

    isolated, _requests, _stats = recording_reader(fail=overlap)
    regular, _requests, _stats = recording_reader(fail=overlap)
    stats = RPCRequestStats(operation="historical_multicall")
    for reader in (isolated, regular):
        reader.web3.provider.rpc_request_stats = stats

    def scan(item: tuple[MultiprocessMulticallReader, HexAddress]) -> None:
        """Read one worker-owned connection with an explicitly selected target.

        :param item: ``(reader, target_address)`` for this worker's isolated task.
        :return: None; assert successful output and restored provider binding.
        """
        reader, target = item
        assert list(reader.process_calls(BLOCK, calls_for([target])))[0].success
        assert reader.web3.provider.rpc_request_stats is stats

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(scan, [(isolated, HYPED), (regular, REGULAR)]))
    assert stats.operation == "historical_multicall"
    assert stats.calls["rpc.example", "eth_call"] == 2
    assert stats.operation_calls["historical_multicall", "rpc.example", "eth_call"] == 1
    assert stats.operation_calls["historical_multicall_greylist", "rpc.example", "eth_call"] == 1
