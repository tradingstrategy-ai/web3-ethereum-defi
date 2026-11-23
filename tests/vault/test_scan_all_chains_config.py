"""Test all-chain vault scanner configuration."""

import datetime
import json
from dataclasses import replace
from pathlib import Path

import pytest

from eth_defi.chain import POA_MIDDLEWARE_NEEDED_CHAIN_IDS
from eth_defi.lighter.constants import LIGHTER_DEPLOYMENTS
from eth_defi.vault import post_processing, scan_all_chains
from eth_defi.vault.scan_all_chains import build_chain_configs
from eth_defi.version_info import VersionInfo

LINEA_CHAIN_ID = 59144


def test_robinhood_chain_is_scheduled_for_vault_scans() -> None:
    """Robinhood is available as an EVM vault scanner target."""

    configs = {config.name: config for config in build_chain_configs()}

    robinhood = configs["Robinhood"]
    assert robinhood.env_var == "JSON_RPC_ROBINHOOD"
    assert robinhood.scan_vaults is True


def test_both_lighter_deployments_are_scheduled() -> None:
    """``SCAN_LIGHTER`` expands into independently resumable deployment scans."""
    protocols = scan_all_chains.build_active_protocols(
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=True,
        scan_hibachi=False,
        scan_apex=False,
        scan_core3=False,
        scan_currency_rates=False,
    )

    assert protocols == [deployment.name for deployment in LIGHTER_DEPLOYMENTS]


def test_apex_is_scheduled_as_a_native_protocol() -> None:
    """``SCAN_APEX`` adds one independently resumable four-hour item."""
    protocols = scan_all_chains.build_active_protocols(
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_apex=True,
        scan_core3=False,
        scan_currency_rates=False,
    )

    assert protocols == ["ApeX"]
    cycles = scan_all_chains.parse_scan_cycles("ApeX=4h")
    assert cycles["ApeX"] == datetime.timedelta(hours=4)


def test_legacy_lighter_cycle_override_applies_to_both_deployments() -> None:
    """Keep existing ``Lighter=4h`` operator configuration working."""
    cycle = datetime.timedelta(hours=4)
    overrides = scan_all_chains.ensure_default_scan_cycles({"Lighter": cycle})

    assert all(overrides[deployment.name] == cycle for deployment in LIGHTER_DEPLOYMENTS)
    assert "Lighter" not in overrides


def test_tempo_chain_is_scheduled_for_vault_scans() -> None:
    """Tempo is available as an EVM vault scanner target."""

    configs = {config.name: config for config in build_chain_configs()}

    tempo = configs["Tempo"]
    assert tempo.env_var == "JSON_RPC_TEMPO"
    assert tempo.scan_vaults is True


@pytest.mark.parametrize("name,chain_id", [("Arc", 5042), ("Worldchain", 480), ("Plume", 98866)])
def test_nest_chains_are_default_scanner_targets(name: str, chain_id: int) -> None:
    """Nest chains enter the normal discovery and price schedule by default."""
    configs = {config.name: config for config in build_chain_configs()}
    config = configs[name]
    assert config.env_var == f"JSON_RPC_{name.upper()}"
    assert config.scan_vaults is True
    assert config.scan_prices is True
    assert scan_all_chains.get_chain_id_by_name(name) == chain_id


@pytest.mark.parametrize("name,chain_id", [("Arc", 5042), ("Worldchain", 480), ("Plume", 98866)])
@pytest.mark.parametrize("chain_prices,global_prices", [(True, True), (True, False), (False, True)])
def test_nest_chains_follow_global_price_switch(name: str, chain_id: int, chain_prices: bool, global_prices: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Require both global and chain price switches while retaining discovery."""
    config = next(config for config in build_chain_configs() if config.name == name)
    config = replace(config, scan_prices=chain_prices)
    monkeypatch.setenv(config.env_var, f"https://{name.lower()}.example")
    phases: list[str] = []

    def fake_verify_rpc_provider_capabilities(rpc_url: str, _chain_name: str) -> tuple[str, int]:
        """Return the configured endpoint without a network read."""

        return rpc_url, 100

    def fake_scan_vaults_for_chain(*_args: object, **_kwargs: object) -> tuple[bool, dict[str, int]]:
        """Return a successful discovery result."""

        phases.append("discovery")
        return True, {
            "chain_id": chain_id,
            "start_block": 1,
            "end_block": 100,
            "vault_count": 0,
            "new_vaults": 0,
            "items_scanned": 0,
        }

    def fake_scan_prices_for_chain(*_args: object, **_kwargs: object) -> tuple[bool, dict[str, int]]:
        """Return a successful historical price result."""

        phases.append("prices")
        return True, {"chain_id": chain_id, "rows_written": 1}

    monkeypatch.setattr(scan_all_chains, "verify_rpc_provider_capabilities", fake_verify_rpc_provider_capabilities)
    monkeypatch.setattr(scan_all_chains, "scan_vaults_for_chain", fake_scan_vaults_for_chain)
    monkeypatch.setattr(scan_all_chains, "scan_prices_for_chain", fake_scan_prices_for_chain)
    monkeypatch.setattr(scan_all_chains, "record_chain_backoff", lambda *_args, **_kwargs: None)

    result = scan_all_chains.scan_chain(
        config,
        scan_prices=global_prices,
        max_workers=1,
        frequency="1h",
        retry_attempt=0,
        vault_db_path=tmp_path / "vault-metadata-db.pickle",
    )

    assert result.status == "success"
    assert result.vault_scan_ok is True
    assert result.price_scan_ok is (True if chain_prices and global_prices else None)
    assert phases == (["discovery", "prices"] if chain_prices and global_prices else ["discovery"])


def test_linea_uses_poa_middleware_for_historical_settlement_reads():
    """Linea historical settlement backfills need PoA extra-data handling."""

    assert LINEA_CHAIN_ID in POA_MIDDLEWARE_NEEDED_CHAIN_IDS


def test_cycle_state_is_provenance_stamped_and_reads_legacy_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cycle-state saves include a timestamp and Docker commit hash.

    The loader must continue to accept the mapping-only format written by
    scanner versions before provenance metadata was added.
    """
    path = tmp_path / "scan-cycle-state.json"
    state = {"Ethereum": "2026-07-11T12:00:00"}
    version = VersionInfo(tag="v0.31", commit_message="fix: stamp JSON", commit_hash="4cea3aa3deadbeef")
    monkeypatch.setattr(scan_all_chains.VersionInfo, "read_docker_version", lambda: version)

    scan_all_chains.save_cycle_state(state, path)

    document = json.loads(path.read_text())
    assert document["generated_at"].endswith("Z")
    assert document["metadata"]["version"] == version.as_dict()
    assert document["items"] == state
    assert scan_all_chains.load_cycle_state(path) == state

    path.write_text(json.dumps(state))
    assert scan_all_chains.load_cycle_state(path) == state


@pytest.mark.parametrize("name", ["Katana"])
def test_missing_hypersync_chain_keeps_price_scanning(name: str) -> None:
    """Chains without a configured event indexer explicitly retain price scans."""
    config = next(config for config in build_chain_configs() if config.name == name)
    assert config.scan_vaults is False
    assert config.scan_prices is True


def test_loop_retries_publication_with_no_collectors_due(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Service durable pending work in an otherwise idle one-chain cycle."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("INCREMENTAL_POST_PROCESSING", raising=False)
    monkeypatch.setenv("LOOP_INTERVAL_SECONDS", "3600")
    monkeypatch.setenv("MAX_CYCLES", "1")
    monkeypatch.setenv("SKIP_TOP_VAULTS", "true")
    monkeypatch.setenv("SKIP_POST_PROCESSING", "false")
    monkeypatch.setenv("SKIP_CLEANING", "false")
    monkeypatch.setenv("FORCE_RESCAN", "false")
    monkeypatch.setenv("FORCE_LEAD_DISCOVERY", "false")
    monkeypatch.delenv("CHAIN_ORDER", raising=False)
    monkeypatch.delenv("DISABLE_CHAINS", raising=False)
    monkeypatch.setenv("TEST_CHAINS", "Base")
    monkeypatch.setattr(scan_all_chains, "build_active_protocols", lambda **_kwargs: ["Hypercore"])
    monkeypatch.setattr(scan_all_chains, "clear_sqlite_rate_limit_databases", lambda: [])
    monkeypatch.setattr(scan_all_chains, "refresh_stale_proxy_state", lambda: None)
    monkeypatch.setattr(scan_all_chains, "setup_console_logging", lambda **_kwargs: None)
    monkeypatch.setattr(scan_all_chains.logging.getLogger(), "addHandler", lambda handler: handler.close())
    pending = tmp_path / "post-processing-pending.json"
    pending.write_text("{}")
    now = scan_all_chains.native_datetime_utc_now().isoformat()
    scan_all_chains.save_cycle_state({"Base": now, "Hypercore": now}, tmp_path / "scan-cycle-state.json")

    monkeypatch.setenv("SKIP_SPARKLINES", "false")
    for option in ("SKIP_METADATA", "SKIP_DATA", "SKIP_SAMPLES"):
        monkeypatch.setenv(option, "true")
    monkeypatch.setenv("SCAN_HYPERCORE", "true")
    monkeypatch.delenv("INCREMENTAL_CLEANING", raising=False)
    monkeypatch.delenv("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME", raising=False)
    monkeypatch.setattr(scan_all_chains, "scan_chain", lambda *args, **kwargs: pytest.fail("Base collector is not due"))
    monkeypatch.setattr(scan_all_chains, "backup_pipeline_files", lambda **kwargs: None)
    monkeypatch.setattr(scan_all_chains, "refresh_withdrawal_capacity_for_chains", lambda *args, **kwargs: 0)
    merge_outcomes = iter([False, True])

    def merge(**kwargs: object) -> dict[str, bool]:
        assert kwargs["incremental"] is True
        return {"hypercore-price-merge": next(merge_outcomes)}

    monkeypatch.setattr(post_processing, "merge_native_protocols", merge)
    calls = []
    monkeypatch.setattr(post_processing, "_clean_incrementally", lambda *args: calls.append("clean") or (True, True))

    def export_sparklines(*, queue_snapshot: bool, **_: object) -> bool:
        assert queue_snapshot
        calls.append("stage")
        return True

    monkeypatch.setattr(post_processing, "export_sparklines", export_sparklines)
    monkeypatch.setattr(post_processing, "calculate_crypto_vault_metadata", lambda **kwargs: None)
    monkeypatch.setattr(post_processing, "export_crypto_exchange_rate_parquet", lambda path: False)

    scan_all_chains.main()
    receipt = json.loads(pending.read_text())
    assert receipt["attempts"] == 1
    assert receipt["failed"] == ["hypercore-price-merge"]
    assert calls == []

    scan_all_chains.main()
    assert calls == ["clean", "stage"]
    assert not pending.exists()

    # Only an export is outstanding. Leave it pending for the scheduled
    # publication without repeating local transforms or native builders.
    receipt["failed"] = ["export-data-files"]
    pending.write_text(json.dumps(receipt))
    scan_all_chains.main()
    assert json.loads(pending.read_text()) == receipt
    assert calls == ["clean", "stage"]
