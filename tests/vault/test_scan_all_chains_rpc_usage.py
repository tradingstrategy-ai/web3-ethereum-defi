"""Tests for JSON-RPC accounting in the all-chain vault scanner."""

import datetime
from pathlib import Path

import pytest
from eth_typing import HexAddress

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
    monkeypatch.setattr(scan_all_chains, "verify_rpc_provider_capabilities", lambda rpc_url, chain_name: (rpc_url, 100))

    def fake_scan_vaults_for_chain(*args, rpc_request_stats: RPCRequestStats, **kwargs) -> tuple[bool, dict]:
        """Return deterministic lead accounting."""

        assert kwargs["greylist"] == greylist

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

        assert kwargs["greylist"] == greylist

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

    greylist = frozenset({HexAddress("0x0000000000000000000000000000000000000001")})
    cycle_started = datetime.date(2026, 7, 20)
    result = scan_all_chains.scan_chain(
        ChainConfig("Test", "JSON_RPC_TEST", True, greylist=greylist),
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
    monkeypatch.setattr(scan_all_chains, "verify_rpc_provider_capabilities", lambda rpc_url, chain_name: (rpc_url, 100))

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


def test_tick_retains_configured_greylist_on_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed tick and its retry consume the same sole chain policy source.

    The real coordinator runs with isolated accounting files. Replace the chain
    phase boundary to simulate one transient failure without network requests;
    both invocations must receive the exact configured chain object.

    :param tmp_path: Private state, counters and backup directory.
    :param monkeypatch: Replace only phase execution and dashboard output.
    :return: None; assert policy identity on initial and retry invocations.
    """
    policy = frozenset({HexAddress("0x0000000000000000000000000000000000000001")})
    chain = ChainConfig("Test", "JSON_RPC_TEST", greylist=policy)
    attempts = []

    def scan(config: ChainConfig, *args: object, **kwargs: object) -> scan_all_chains.ChainResult:
        """A first transient failure requests the coordinator's retry path."""
        assert config is chain and config.greylist is policy
        attempt = args[3]
        attempts.append(attempt)
        return scan_all_chains.ChainResult(name="Test", status="failed" if attempt == 0 else "success", error_category="transient", retry_attempt=attempt)

    monkeypatch.setattr(scan_all_chains, "scan_chain", scan)
    monkeypatch.setattr(scan_all_chains, "print_dashboard", lambda *_, **__: None)
    options = dict(
        chains=[chain],
        active_protocols=[],
        scan_prices=True,
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_apex=False,
        scan_core3=False,
        scan_currency_rates=False,
        max_workers=1,
        core3_max_workers=1,
        currency_api_max_workers=1,
        frequency="1h",
        retry_count=1,
        skip_post_processing=True,
        skip_cleaning=True,
        skip_top_vaults=True,
        skip_sparklines=True,
        skip_metadata=True,
        skip_data=True,
        skip_samples=True,
        vault_db_path=tmp_path / "metadata.pickle",
        uncleaned_price_path=tmp_path / "prices.parquet",
        reader_state_path=tmp_path / "reader.pickle",
        hyperliquid_db_path=tmp_path / "hyperliquid.duckdb",
        hyperliquid_hf_db_path=tmp_path / "hf.duckdb",
        grvt_db_path=tmp_path / "grvt.duckdb",
        lighter_db_path=tmp_path / "lighter.duckdb",
        hibachi_db_path=tmp_path / "hibachi.duckdb",
        apex_db_path=tmp_path / "apex.duckdb",
        bkp_files=[],
        bkp_dir=tmp_path / "backups",
        scan_vault_settlements=False,
        rpc_tracking_database_path=tmp_path / "rpc-tracking.duckdb",
    )
    results = scan_all_chains.run_scan_tick(**options)
    assert attempts == [0, 1]
    assert results["Test"].status == "success"
