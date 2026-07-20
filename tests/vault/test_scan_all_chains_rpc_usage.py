"""Tests for JSON-RPC accounting in the all-chain vault scanner."""

import datetime
from pathlib import Path

import pytest

from eth_defi.provider.rpcdb import RPCRequestStats, RPCUsageDatabase
from eth_defi.vault import scan_all_chains
from eth_defi.vault.scan_all_chains import ChainConfig


@pytest.fixture()
def rpc_usage_database(tmp_path: Path):
    """Create an explicitly closed scanner accounting database."""

    database = RPCUsageDatabase(tmp_path / "rpc-tracking.duckdb")
    try:
        yield database
    finally:
        database.close()


def test_scan_chain_records_lead_and_price_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rpc_usage_database: RPCUsageDatabase,
) -> None:
    """A chain attempt persists lead and price statistics separately."""

    monkeypatch.setenv("JSON_RPC_TEST", "https://rpc.example")
    monkeypatch.setattr(scan_all_chains, "verify_archive_node", lambda rpc_url, chain_name: (rpc_url, 100))

    def fake_scan_vaults_for_chain(*args, rpc_request_stats: RPCRequestStats, **kwargs) -> tuple[bool, dict]:
        """Return deterministic lead accounting."""

        rpc_request_stats.record_call("rpc.example", "eth_call", 3)
        return True, {
            "chain_id": 1,
            "start_block": 1,
            "end_block": 100,
            "vault_count": 2,
            "new_vaults": 1,
            "items_scanned": 5,
        }

    def fake_scan_prices_for_chain(*args, rpc_request_stats: RPCRequestStats, **kwargs) -> tuple[bool, dict]:
        """Return deterministic price accounting."""

        rpc_request_stats.record_call("fallback.example", "eth_getBlockByNumber", 4)
        return True, {
            "chain_id": 1,
            "rows_written": 8,
            "start_block": 1,
            "end_block": 100,
            "items_scanned": 2,
        }

    monkeypatch.setattr(scan_all_chains, "scan_vaults_for_chain", fake_scan_vaults_for_chain)
    monkeypatch.setattr(scan_all_chains, "scan_prices_for_chain", fake_scan_prices_for_chain)

    cycle_started = datetime.date(2026, 7, 20)
    result = scan_all_chains.scan_chain(
        ChainConfig("Test", "JSON_RPC_TEST", True),
        scan_prices=True,
        max_workers=1,
        frequency="1h",
        retry_attempt=0,
        vault_db_path=tmp_path / "vaults.pickle",
        uncleaned_price_path=tmp_path / "prices.parquet",
        reader_state_path=tmp_path / "reader-state.pickle",
        rpc_usage_database=rpc_usage_database,
        rpc_cycle_started=cycle_started,
        rpc_cycle_number=1,
    )

    assert result.status == "success"
    assert result.chain_id == 1
    assert rpc_usage_database.fetch_cycle_calls(1, cycle_started, 1) == [
        ("lead_discovery", "rpc.example", "eth_call", 3, 5),
        ("price_scan", "fallback.example", "eth_getBlockByNumber", 4, 2),
    ]


@pytest.mark.parametrize(
    "failure_type",
    [scan_all_chains.duckdb.IOException, RuntimeError, AssertionError],
)
def test_scan_chain_keeps_scan_success_when_accounting_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rpc_usage_database: RPCUsageDatabase,
    failure_type: type[BaseException],
) -> None:
    """An observability write failure does not trigger an expensive re-scan."""

    monkeypatch.setenv("JSON_RPC_TEST", "https://rpc.example")
    monkeypatch.setattr(scan_all_chains, "verify_archive_node", lambda rpc_url, chain_name: (rpc_url, 100))

    def fake_scan_vaults_for_chain(*args, rpc_request_stats: RPCRequestStats, **kwargs) -> tuple[bool, dict]:
        """Return one successful phase."""

        return True, {
            "chain_id": 1,
            "start_block": 1,
            "end_block": 100,
            "vault_count": 0,
            "new_vaults": 0,
            "items_scanned": 0,
        }

    monkeypatch.setattr(scan_all_chains, "scan_vaults_for_chain", fake_scan_vaults_for_chain)
    monkeypatch.setattr(rpc_usage_database, "record_scan", lambda **kwargs: (_ for _ in ()).throw(failure_type("write failed")))

    result = scan_all_chains.scan_chain(
        ChainConfig("Test", "JSON_RPC_TEST", True),
        scan_prices=False,
        max_workers=1,
        frequency="1h",
        retry_attempt=0,
        vault_db_path=tmp_path / "vaults.pickle",
        rpc_usage_database=rpc_usage_database,
        rpc_cycle_started=datetime.date(2026, 7, 20),
        rpc_cycle_number=1,
    )

    assert result.status == "success"
