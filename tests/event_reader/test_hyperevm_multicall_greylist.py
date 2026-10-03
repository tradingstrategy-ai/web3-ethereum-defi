"""Exercise real Multicall encoding with a deterministic transport recorder.

Anvil cannot reproduce HyperCore's provider-specific precompile accounting. These
fixtures therefore control the transport while using the actual ABI encoder,
request planner, retry code and result reconstruction. A separate guarded manual
script checks the configured real providers without modifying scanner state.
"""

import datetime
import pickle
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from eth_abi import decode, encode
from eth_typing import HexAddress
from requests.exceptions import ReadTimeout
from web3 import HTTPProvider, Web3

from eth_defi.event_reader import multicall_batcher
from eth_defi.event_reader.multicall_batcher import (
    HYPEREVM_MULTICALL_GREYLIST,
    EncodedCall,
    MulticallHistoricalDataUnavailable,
    MulticallRetryable,
    MulticallRetryExhausted,
    MultiprocessMulticallReader,
    plan_multicall_batches,
)
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.vault.rpc_scan_state import classify_rpc_scan_failure

HYPED = HexAddress("0x4d0fF6a0DD9f7316b674Fb37993A3Ce28BEA340e")
REGULAR = HexAddress("0x0000000000000000000000000000000000000001")
BLOCK = 47_561_964


@pytest.fixture
def recording_reader(monkeypatch: pytest.MonkeyPatch):
    """Use actual ABI bindings with provider-controlled per-request failures.

    The returned factory creates a reader and a chronological record of encoded
    physical requests. Response values identify input selectors, allowing tests
    to detect dropped, duplicated or incorrectly reordered results.
    """
    monkeypatch.setattr("eth_defi.event_reader.multicall_batcher.time.sleep", lambda _seconds: None)

    def create(chain: int = 999, batch_size: int = 3, greylist_batch_size: int = 1, fail: Callable | None = None):
        """Create an isolated reader; the optional callback rejects a request."""
        stats = RPCRequestStats(operation="historical_multicall")
        requests = []
        provider = HTTPProvider("https://rpc.example")
        provider.rpc_request_stats = stats

        def request(method: str, params: list) -> dict:
            """Decode real payloads and emulate provider results at source block."""
            stats.record_call("rpc.example", method)
            if method == "eth_chainId":
                result = hex(chain)
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
        reader = MultiprocessMulticallReader(Web3(provider), batch_size=batch_size, greylist_batch_size=greylist_batch_size)
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
def test_transport_lane_composition_and_result_identity(recording_reader, chain: int, small: int) -> None:
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
    batches = list(plan_multicall_batches(999, encoded, 40, 2))
    assert [len(indexes) for _grey, indexes in batches] == [2, 1, 2, 1]
    assert all(grey and len({encoded[index][0] for index in indexes}) == 1 for grey, indexes in batches)
    assert list(plan_multicall_batches(999, [], 40)) == []


def test_retry_resumes_after_successful_fragment(recording_reader) -> None:
    """A reduced retry may fail later; neither regular prefix gets replayed."""

    def fail(batch: tuple, attempt: int) -> None:
        """Reject the second full batch, then its second one-call fragment."""
        if attempt in (2, 4):
            raise ValueError({"code": -32003, "message": "out of gas"})

    reader, requests, _stats = recording_reader(chain=1, batch_size=3, fail=fail)
    outputs = list(reader.process_calls(BLOCK, calls_for([REGULAR] * 6)))
    assert [int.from_bytes(output.result, "big") for output in outputs] == list(range(6))
    assert [[data[-1] for _address, data in batch] for batch, _block in requests] == [[0, 1, 2], [3, 4, 5], [3], [4], [4], [5]]


def test_greylist_gas_failure_is_unavailable_without_replaying_regular(recording_reader) -> None:
    """A sole provider's gas rejection does not warrant repeated one-call probes."""

    def fail(batch: tuple, _attempt: int) -> None:
        """Reject only the reviewed target with the documented provider symptom."""
        if batch[0][0] == HYPED.lower():
            raise ValueError({"code": -32003, "message": "out of gas"})

    reader, requests, stats = recording_reader(fail=fail)
    outputs = list(reader.process_calls(BLOCK, calls_for([HYPED, REGULAR]), allow_greylist_unavailable=True))
    assert outputs[0].unavailable_error and not outputs[0].success
    assert outputs[0].result == b"" and outputs[0].revert_exception is None
    assert outputs[1].success
    assert len(requests) == 2
    assert stats.operation_calls["historical_multicall_greylist", "rpc.example", "eth_call"] == 1


def test_greylist_archive_gap_remains_hard_error(recording_reader) -> None:
    """Isolation must not conceal provider archive outages as target-local gas."""

    def fail(_batch: tuple, _attempt: int) -> None:
        """Emulate a chain-wide state retention failure."""
        raise ValueError("missing trie node")

    reader, requests, _stats = recording_reader(fail=fail)
    with pytest.raises(MulticallHistoricalDataUnavailable):
        list(reader.process_calls(BLOCK, calls_for([HYPED])))
    assert len(requests) == 1


def test_operation_scope_is_thread_local_and_pickleable() -> None:
    """Lane labels partition physical requests without leaking across workers."""
    stats = RPCRequestStats(operation="historical_multicall")

    def record(lane: str) -> None:
        """Exercise each worker's label while updating one shared accumulator."""
        with stats.operation_scope(lane):
            for _ in range(100):
                stats.record_call("rpc.example", "eth_call")
        stats.record_call("rpc.example", "eth_chainId")

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(record, ["regular", "greylist"]))
    restored = pickle.loads(pickle.dumps(stats))
    assert restored.calls["rpc.example", "eth_call"] == 200
    assert restored.operation_calls["regular", "rpc.example", "eth_call"] == 100
    assert restored.operation_calls["greylist", "rpc.example", "eth_call"] == 100
    assert restored.operation_calls["historical_multicall", "rpc.example", "eth_chainId"] == 2


@pytest.mark.parametrize("restore_failure", [False, True])
def test_greylist_failover_restores_regular_provider(monkeypatch: pytest.MonkeyPatch, restore_failure: bool) -> None:
    """A successful isolated fallback cannot steer later normal tasks to backups."""

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
            if restore_failure:
                raise multicall_batcher.ChainIdMismatch("original endpoint cannot be verified")
            self.currently_active_provider = index

    switches = []
    provider = Provider()
    reader = object.__new__(MultiprocessMulticallReader)
    reader.web3 = SimpleNamespace(provider=provider, eth=SimpleNamespace(chain_id=999))
    reader.chain_id = 999
    reader.batch_size = 40
    reader.greylist_batch_size = 1
    reader.calls = reader.last_switch = 0
    reader.backswitch_threshold = 100
    reader.too_many_requets_sleep = 0
    monkeypatch.setattr(multicall_batcher, "FallbackProvider", Provider)
    monkeypatch.setattr(multicall_batcher, "get_provider_name", lambda _provider: "rpc.example")
    monkeypatch.setattr(multicall_batcher, "get_multicall_contract", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(multicall_batcher, "resolve_hyperevm_consensus_failover", lambda *_args: None)

    def invoke(_contract: object, _block: int, _size: int, encoded: list, _strict: bool) -> list:
        """Fail the isolated target on primary; succeed on the alternate."""
        if encoded[0][0].lower() == HYPED.lower() and provider.currently_active_provider == 0:
            raise MulticallRetryable("out of gas")
        return [(True, b"result")] * len(encoded)

    monkeypatch.setattr(reader, "call_multicall_with_batch_size", invoke)
    results = list(reader.process_calls(BLOCK, calls_for([REGULAR, HYPED])))
    assert all(result.success for result in results)
    assert switches == [1, 0]
    assert provider.currently_active_provider == (1 if restore_failure else 0)


def test_metadata_callers_keep_strict_gas_errors(recording_reader) -> None:
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
    reader.too_many_requets_sleep = 0
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

    monkeypatch.setattr(reader, "call_multicall_with_batch_size", invoke)
    results = reader.fetch_multicall_batch_with_retries(object(), BLOCK, 3, inputs, False, 5)
    assert results == [(True, b"0"), (True, b"1"), (True, b"2")]
    assert attempted == [[0, 1, 2], [1, 2], [2]]
