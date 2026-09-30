"""Scheduling sidecars and denominator-safe counter detail tests."""

import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from requests.exceptions import ConnectionError
from web3 import HTTPProvider, Web3
from web3.exceptions import ContractLogicError, Web3RPCError

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626 import vault_token
from eth_defi.erc_4626.vault import DENOMINATION_UNAVAILABLE_EXCHANGE_RATE, UNKNOWN_EXCHANGE_RATE, VaultReaderState
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.provider.anvil import invalidate_anvil_detection, is_anvil
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.provider.rpcdb import RPCRequestStats, RPCUsageDatabase
from eth_defi.token import TokenDetails, TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.rpc_scan_state import classify_rpc_scan_failure, fetch_reader_publication_progress, fetch_remaining_state_budget, is_contract_read_failure, is_metadata_due, is_probe_due, load_rpc_scan_state, record_chain_backoff, record_metadata_failure, record_probe_result, save_reader_publication_journal


def test_unavailable_denomination_preserves_qualified_history() -> None:
    """Missing token data cannot erase prior verified qualification or TVL."""
    vault = SimpleNamespace(spec=VaultSpec(1, "0x" + "1" * 40), vault_address="0x" + "1" * 40, first_seen_at_block=1, denomination_token=None)
    state = VaultReaderState(vault)
    state.token_symbol = "USDC"
    state.last_tvl = state.max_tvl = Decimal(2_000)
    assert state.exchange_rate == DENOMINATION_UNAVAILABLE_EXCHANGE_RATE
    assert state.exchange_rate != UNKNOWN_EXCHANGE_RATE
    assert state.freshness_qualified
    state.on_called(SimpleNamespace(timestamp=datetime.datetime(2026, 9, 30), block_identifier=10), Decimal(100), Decimal(1))
    assert state.last_tvl == Decimal(2_000)
    assert state.max_tvl == Decimal(2_000)
    assert state.last_block == 10
    assert set(state.save()) == set(VaultReaderState.SERIALISABLE_ATTRIBUTES)


def test_anvil_detection_connection_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connection/provider replacement never shares a chain-ID cache."""
    calls = []

    def request(method, params):
        calls.append(method)
        return {"jsonrpc": "2.0", "id": 1, "result": "anvil/v1"}

    provider = HTTPProvider("http://localhost:8545")
    monkeypatch.setattr(provider, "make_request", request)
    web3 = Web3(provider)
    assert is_anvil(web3) and is_anvil(web3)
    assert calls == ["web3_clientVersion"]
    replacement = HTTPProvider("https://rpc.example")
    monkeypatch.setattr(replacement, "make_request", lambda method, params: {"jsonrpc": "2.0", "id": 1, "result": "geth/v1"})
    web3.provider = replacement
    assert not is_anvil(web3)
    invalidate_anvil_detection(web3)
    assert not is_anvil(web3)


def test_probe_due_and_near_threshold_cadence() -> None:
    """Cached tiny vaults skip construction, while meaningful vaults keep reads."""
    now = datetime.datetime(2026, 9, 30)
    entries = {}
    record_probe_result(entries, "tiny", now, now - datetime.timedelta(days=30), "100", None)
    assert not is_probe_due(entries["tiny"], now + datetime.timedelta(days=6), False)
    assert is_probe_due(entries["tiny"], now + datetime.timedelta(days=7), False)
    assert not is_probe_due(entries["tiny"], now, True)
    record_probe_result(entries, "near", now, now - datetime.timedelta(days=30), "1499", None)
    assert is_probe_due(entries["near"], now + datetime.timedelta(hours=8), False)


def test_backoff_restart_and_retention_cap(tmp_path: Path) -> None:
    """Persistent internal-failure deferral survives restart and caps Monad delay."""
    now = datetime.datetime(2026, 9, 30)
    path = tmp_path / "backoff.json"
    record_chain_backoff(path, "Base", "internal", now)
    assert load_rpc_scan_state(path)["Base"]["next_retry_at"] == "2026-09-30T01:00:00"
    record_chain_backoff(path, "Base", "internal", now)
    assert load_rpc_scan_state(path)["Base"]["next_retry_at"] == "2026-09-30T02:00:00"
    record_chain_backoff(path, "Monad", "transient", now, retention_seconds=600)
    assert load_rpc_scan_state(path)["Monad"]["next_retry_at"] == "2026-09-30T00:05:00"
    assert classify_rpc_scan_failure(AttributeError("bug")) == "internal"
    assert classify_rpc_scan_failure(ConnectionError("unavailable")) == "transient"


def test_operation_detail_does_not_double_phase_totals(tmp_path: Path) -> None:
    """Child operation counts merge once and stay separate from parent totals."""
    parent = RPCRequestStats(operation="reader_preparation")
    parent.record_call("rpc.example", "eth_call")
    child = RPCRequestStats(operation="tvl_admission")
    child.record_call("rpc.example", "eth_call", 3)
    parent.merge(child)
    with RPCUsageDatabase(tmp_path / "rpc.duckdb") as database:
        database.record_scan(1, "price_scan", datetime.date(2026, 9, 30), 1, parent, 10)
        connection = database._require_connection()
        assert connection.execute("SELECT sum(call_count) FROM vault_rpc_api_calls").fetchone()[0] == 4
        assert connection.execute("SELECT sum(call_count) FROM vault_rpc_operation_calls").fetchone()[0] == 4


def test_reader_publication_journal_recovers_only_committed_file(tmp_path: Path) -> None:
    """Interrupted progress persistence recovers only its published Parquet.

    Simulate process death around rename without deleting or rescanning rows.
    The journal uses the unchanged legacy state shape for rollback compatibility.
    """
    output = tmp_path / "prices.parquet"
    temporary = tmp_path / "prices.tmp"
    journal = tmp_path / "reader.publication.pickle"
    output.write_bytes(b"previous prices")
    temporary.write_bytes(b"new committed prices")
    states = {"vault": {"last_block": 123, "last_tvl": Decimal(2000)}}
    save_reader_publication_journal(journal, temporary, output, states)
    assert fetch_reader_publication_progress(journal, output) == {}
    temporary.replace(output)
    assert fetch_reader_publication_progress(journal, output) == states
    replacement = tmp_path / "replacement.tmp"
    replacement.write_bytes(b"different publication")
    replacement.replace(output)
    assert fetch_reader_publication_progress(journal, output) == {}


def test_metadata_reclassification_force_and_bounded_negatives() -> None:
    """Protocol changes bypass freshness; persistent negatives stop daily replay."""
    now = datetime.datetime(2026, 9, 30)
    address = "0x" + "1" * 40
    pending = {address: {"failure_count": 0, "features": ["morpho_like"]}}
    entries = {}
    for attempt in range(3):
        record_metadata_failure(pending, entries, address, ["morpho_like"], "version", now + datetime.timedelta(days=attempt), "transient")
    assert address not in pending
    assert entries[address]["status"] == "unavailable"
    assert not is_metadata_due(entries[address], ["morpho_like"], "version", now + datetime.timedelta(days=3))
    assert is_metadata_due(entries[address], ["ipor_like"], "version", now + datetime.timedelta(days=3))
    assert is_metadata_due(entries[address], ["morpho_like"], "new-version", now + datetime.timedelta(days=3))
    assert is_metadata_due(entries[address], ["morpho_like"], "version", now + datetime.timedelta(days=3), force=True)
    assert is_metadata_due(entries[address], ["morpho_like"], "version", now + datetime.timedelta(days=9))
    pending[address] = {"failure_count": 0}
    record_metadata_failure(pending, entries, address, [], "version", now, "unsupported")
    assert address not in pending


def test_retention_budget_tracks_unscanned_history_not_new_measurement() -> None:
    """Refreshing provider capability never renews the unread data's lifetime."""
    now = datetime.datetime(2026, 9, 30)
    boundary = {"checked_at": now.isoformat(), "head_block": 48 * 3600, "retention_seconds": 48 * 3600}
    assert fetch_remaining_state_budget(boundary, 24 * 3600, 1, now) == 24 * 3600
    assert fetch_remaining_state_budget(boundary, 24 * 3600, 1, now + datetime.timedelta(hours=15)) == 9 * 3600
    refreshed = {**boundary, "head_block": 63 * 3600, "checked_at": (now + datetime.timedelta(hours=15)).isoformat()}
    assert fetch_remaining_state_budget(refreshed, 24 * 3600, 1, now + datetime.timedelta(hours=15)) == 9 * 3600


@pytest.mark.parametrize("message", ["execution reverted", "invalid opcode", "VM execution error", "out of gas"])
def test_plain_rpc_vm_failures_are_contract_outcomes(message: str) -> None:
    """Provider-specific contract errors are isolated without masking code bugs."""
    assert is_contract_read_failure(Web3RPCError(message))
    assert not is_contract_read_failure(ValueError("programming bug"))
    assert classify_rpc_scan_failure(Web3RPCError("execution reverted")) == "internal"
    assert classify_rpc_scan_failure(Web3RPCError("rate limit exceeded")) == "transient"


def test_token_mapping_expiry_and_negative_token_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finite caches refresh legacy mappings and stale unsuccessful token reads."""
    monkeypatch.setenv("VAULT_RPC_OPTIMISATIONS", "true")
    address = "0x" + "1" * 40
    asset = "0x" + "2" * 40
    now = datetime.datetime(2026, 9, 30)
    monkeypatch.setattr(vault_token, "native_datetime_utc_now", lambda: now)
    cache = {f"1-vault-denomination-token-{address}": {"address": asset}}
    assert vault_token.get_cached_vault_denomination_token_address(cache, 1, address) is None
    vault_token.set_cached_vault_denomination_token_address(cache, 1, address, asset)
    assert vault_token.get_cached_vault_denomination_token_address(cache, 1, address) == asset
    monkeypatch.setattr(vault_token, "native_datetime_utc_now", lambda: now + datetime.timedelta(days=7))
    assert vault_token.get_cached_vault_denomination_token_address(cache, 1, address) is None
    tokens = TokenDiskCache(tmp_path / "tokens.sqlite")
    try:
        key = TokenDetails.generate_cache_key(1, address)
        tokens[key] = {"symbol": None}
        assert len(list(tokens.generate_calls(1, [address]))) == 4
        tokens[key] = {"symbol": None, "checked_at": native_datetime_utc_now().isoformat()}
        assert list(tokens.generate_calls(1, [address])) == []
        tokens[key] = {"symbol": None, "checked_at": "2026-01-01T00:00:00"}
        assert len(list(tokens.generate_calls(1, [address]))) == 4
        # A previous-release loader reads known keys and ignores provenance.
        tokens[key] = {"symbol": "USDC", "checked_at": "2026-01-01T00:00:00"}
        assert tokens[key]["symbol"] == "USDC"
        assert list(tokens.generate_calls(1, [address])) == []

    finally:
        tokens.close()


@pytest.mark.parametrize("message", ["header not found", "upstream does not have the requested block yet", "not enough agreement"])
def test_known_provider_availability_errors_are_transient(message: str) -> None:
    """Known current-head/provider consensus failures receive bounded retries."""
    assert classify_rpc_scan_failure(Web3RPCError(message)) == "transient"
    exhausted = RuntimeError("Out of multicall retries, bailing out")
    exhausted.__cause__ = Web3RPCError(message)
    assert classify_rpc_scan_failure(exhausted) == "transient"
    exhausted.__cause__ = ContractLogicError("execution reverted")
    assert classify_rpc_scan_failure(exhausted) == "internal"


def test_price_outcome_reports_partial_unavailable_coverage(tmp_path: Path) -> None:
    """A price phase with healthy work and unavailable readers is degraded."""
    with RPCUsageDatabase(tmp_path / "counters.duckdb") as database:
        database.record_scan(1, "price_scan", datetime.date(2026, 9, 30), 1, RPCRequestStats(), 2, metrics={"low_activity_unverified": 1})
        assert database._require_connection().execute("SELECT outcome FROM vault_rpc_operation_calls WHERE operation='outcome'").fetchall() == [("degraded",)]


@pytest.mark.parametrize(
    "payload",
    [
        {"code": -32090, "message": "request rejected"},
        {"code": -32000, "message": "header not found"},
        {"code": -32000, "message": "not enough agreement"},
    ],
)
def test_fallback_provider_payloads_are_transient(payload: dict) -> None:
    """Production fallback errors retain retry classification through wrappers."""
    error = ExtraValueError(payload)
    assert classify_rpc_scan_failure(error) == "transient"
    assert not is_contract_read_failure(error)
    exhausted = RuntimeError("Out of multicall retries")
    exhausted.__cause__ = error
    assert classify_rpc_scan_failure(exhausted) == "transient"


def test_fallback_state_unavailable_and_revert_are_distinct() -> None:
    """Missing node state is retryable; a Solidity revert is candidate-scoped."""
    missing = ProbablyNodeHasNoBlock("Node lacked state")
    assert classify_rpc_scan_failure(missing) == "transient"
    assert not is_contract_read_failure(missing)
    revert = ExtraValueError({"code": 3, "message": "execution reverted"})
    assert is_contract_read_failure(revert)
    assert classify_rpc_scan_failure(revert) == "internal"
    assert not is_contract_read_failure(ValueError("unexpected code error"))
