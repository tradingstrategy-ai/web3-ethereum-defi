"""Tests for reusable JSON-RPC request accounting."""

import datetime
import pickle
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import pytest
import requests
from web3 import HTTPProvider, Web3

from eth_defi.provider.fallback import FallbackProvider
from eth_defi.provider.rpcdb import (
    RPCRequestStats,
    RPCUsageDatabase,
    format_rpc_usage_report,
    normalise_rpc_error,
    resolve_rpc_tracking_database_path,
)


@pytest.fixture()
def rpc_usage_database(tmp_path: Path):
    """Create a tracking database that is always explicitly closed."""

    database = RPCUsageDatabase(tmp_path / "rpc-tracking.duckdb")
    try:
        yield database
    finally:
        database.close()


def test_rpc_request_stats_threaded_and_pickle_safe() -> None:
    """Thread workers share counters and subprocess transport recreates them."""

    stats = RPCRequestStats()

    def record_batch(_: int) -> None:
        """Record one deterministic worker batch."""

        stats.record_call("rpc.example.com", "eth_call", 10)
        stats.record_error("rpc.example.com", "http_429", "rate limited")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(record_batch, range(100)))

    restored = pickle.loads(pickle.dumps(stats))
    calls, errors = restored.export()
    assert calls == {("rpc.example.com", "eth_call"): 1_000}
    assert errors == {("rpc.example.com", "http_429", "rate limited"): 100}

    restored.record_call("rpc.example.com", "eth_blockNumber")
    calls, _ = restored.export()
    assert calls[("rpc.example.com", "eth_blockNumber")] == 1


def test_rpc_error_normalisation() -> None:
    """Stored errors retain provider diagnostics and stable error codes."""

    code, message = normalise_rpc_error({"code": -32005, "message": "limit trace_id=abc123"})
    assert code == "-32005"
    assert message == "limit trace_id=abc123"

    response = requests.Response()
    response.status_code = 429
    http_error = requests.HTTPError("429 from https://user:pass@rpc.example.com/private/api-key?token=secret", response=response)
    code, message = normalise_rpc_error(http_error)
    assert code == "http_429"
    assert message == "429 from https://user:pass@rpc.example.com/private/api-key?token=secret"


def test_rpc_tracking_database_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared resolver expands a configured home-relative path."""

    monkeypatch.setenv("RPC_TRACKING_DATABASE_PATH", "~/custom-rpc.duckdb")
    assert resolve_rpc_tracking_database_path() == Path.home() / "custom-rpc.duckdb"


def test_fallback_provider_accepts_endpointless_custom_provider() -> None:
    """Accounting support does not tighten the base provider interface."""

    provider = HTTPProvider("https://rpc.example", exception_retry_configuration=None)
    del provider.endpoint_uri

    fallback = FallbackProvider([provider])

    assert fallback.rpc_provider_domains[id(provider)] == "unknown"


def test_fallback_provider_tracks_physical_attempt_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recovered fallback records failed and successful physical attempts."""

    primary = HTTPProvider("https://primary.example/private-key", exception_retry_configuration=None)
    fallback = HTTPProvider("https://fallback.example/another-key", exception_retry_configuration=None)

    def primary_request(method: str, params: list) -> dict:
        """Fail the requested method but support switchover verification."""

        if method == "eth_chainId":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        raise requests.ConnectionError("primary unavailable")

    def fallback_request(method: str, params: list) -> dict:
        """Return deterministic chain and block responses."""

        result = "0x1" if method == "eth_chainId" else "0x10"
        return {"jsonrpc": "2.0", "id": 1, "result": result}

    monkeypatch.setattr(primary, "make_request", primary_request)
    monkeypatch.setattr(fallback, "make_request", fallback_request)

    stats = RPCRequestStats()
    provider = FallbackProvider([primary, fallback], retries=1, sleep=0, rpc_request_stats=stats)
    web3 = Web3(provider)

    assert web3.eth.block_number == 16
    calls, errors = stats.export()
    assert calls[("primary.example", "eth_blockNumber")] == 1
    assert calls[("primary.example", "eth_chainId")] == 1
    assert calls[("fallback.example", "eth_chainId")] == 1
    assert calls[("fallback.example", "eth_blockNumber")] == 1
    assert errors[("primary.example", "ConnectionError", "primary unavailable")] == 1
    assert provider.api_call_counts[1]["eth_blockNumber"] == 1


def test_fallback_provider_records_json_rpc_error_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A JSON-RPC error keeps its protocol code without duplicate recording."""

    upstream = HTTPProvider("https://rpc.example/private-key", exception_retry_configuration=None)
    monkeypatch.setattr(
        upstream,
        "make_request",
        lambda method, params: {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32005, "message": "rate limited request_id=secret"},
        },
    )
    stats = RPCRequestStats()
    provider = FallbackProvider([upstream], retries=0, sleep=0, rpc_request_stats=stats)

    with pytest.raises(ValueError):
        provider.make_request("eth_call", [])

    calls, errors = stats.export()
    assert calls == {("rpc.example", "eth_call"): 1}
    assert errors == {("rpc.example", "-32005", "rate limited request_id=secret"): 1}


def test_rpc_usage_database_append_only_aggregates(rpc_usage_database: RPCUsageDatabase) -> None:
    """Retries append while cycle and daily queries avoid item multiplication."""

    cycle_started = datetime.date(2026, 7, 20)
    assert rpc_usage_database.allocate_cycle() == 1

    first_attempt = RPCRequestStats()
    first_attempt.record_call("primary.example", "eth_call", 2)
    first_attempt.record_call("fallback.example", "eth_getBlockByNumber")
    first_attempt.record_error("fallback.example", "http_429", "rate limited")
    rpc_usage_database.record_scan(1, "lead_discovery", cycle_started, 1, first_attempt, 10)

    retry_attempt = RPCRequestStats()
    retry_attempt.record_call("primary.example", "eth_call")
    rpc_usage_database.record_scan(1, "lead_discovery", cycle_started, 1, retry_attempt, 8)

    price_attempt = RPCRequestStats()
    price_attempt.record_call("primary.example", "eth_call", 4)
    rpc_usage_database.record_scan(1, "price_scan", cycle_started, 2, price_attempt, 3)

    assert rpc_usage_database.allocate_cycle() == 3
    assert rpc_usage_database.fetch_cycle_calls(1, cycle_started, 1) == [
        ("lead_discovery", "fallback.example", "eth_getBlockByNumber", 1, 10),
        ("lead_discovery", "primary.example", "eth_call", 3, 10),
    ]
    assert rpc_usage_database.fetch_daily_totals(1, cycle_started) == [
        ("lead_discovery", "fallback.example", 1, 10),
        ("lead_discovery", "primary.example", 3, 10),
        ("price_scan", "primary.example", 4, 3),
    ]
    assert rpc_usage_database.fetch_cycle_errors(1, cycle_started, 1) == [
        ("lead_discovery", "fallback.example", "http_429", "rate limited", 1),
    ]

    report = format_rpc_usage_report(rpc_usage_database, 1, cycle_started, 1)
    assert "lead_discovery" in report
    assert "fallback.example" in report
    assert "UTC daily-to-date totals" in report


def test_rpc_usage_database_zero_call_marker(rpc_usage_database: RPCUsageDatabase) -> None:
    """A completed zero-call phase remains visible with its item count."""

    cycle_started = datetime.date(2026, 7, 20)
    rpc_usage_database.record_scan(8453, "price_scan", cycle_started, 1, RPCRequestStats(), 0)

    assert rpc_usage_database.fetch_cycle_calls(8453, cycle_started, 1) == [
        ("price_scan", "none", "none", 0, 0),
    ]
    report = format_rpc_usage_report(rpc_usage_database, 8453, cycle_started, 1)
    assert "price_scan" in report


def test_rpc_usage_database_rolls_back_both_tables(rpc_usage_database: RPCUsageDatabase) -> None:
    """An error-row failure rolls back call rows from the same attempt."""

    cycle_started = datetime.date(2026, 7, 20)
    stats = RPCRequestStats()
    stats.record_call("rpc.example", "eth_call")
    stats.record_error("rpc.example", "http_429", "rate limited")
    rpc_usage_database.connection.execute("DROP TABLE vault_rpc_api_errors")

    with pytest.raises(duckdb.Error):
        rpc_usage_database.record_scan(1, "price_scan", cycle_started, 1, stats, 1)

    rpc_usage_database._create_schema()
    assert rpc_usage_database.fetch_cycle_calls(1, cycle_started, 1) == []
    assert rpc_usage_database.fetch_cycle_errors(1, cycle_started, 1) == []


def test_rpc_usage_database_cycle_survives_restart(rpc_usage_database: RPCUsageDatabase) -> None:
    """Cycle allocation is persistent and closed connections fail clearly."""

    cycle_started = datetime.date(2026, 7, 20)
    database_path = rpc_usage_database.path
    rpc_usage_database.record_scan(1, "lead_discovery", cycle_started, 1, RPCRequestStats(), 0)
    rpc_usage_database.close()

    with pytest.raises(RuntimeError):
        rpc_usage_database.fetch_cycle_calls(1, cycle_started, 1)

    with RPCUsageDatabase(database_path) as reopened:
        assert reopened.allocate_cycle() == 2
