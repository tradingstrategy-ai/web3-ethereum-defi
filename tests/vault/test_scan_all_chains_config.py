"""Test all-chain vault scanner configuration."""

import datetime
import json
from pathlib import Path

import pytest

from eth_defi.chain import POA_MIDDLEWARE_NEEDED_CHAIN_IDS
from eth_defi.lighter.constants import LIGHTER_DEPLOYMENTS
from eth_defi.vault import scan_all_chains
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


def test_arc_chain_is_scheduled_for_metadata_only_vault_scans() -> None:
    """Arc discovery is scheduled without enabling historical prices."""

    configs = {config.name: config for config in build_chain_configs()}

    arc = configs["Arc"]
    assert arc.env_var == "JSON_RPC_ARC"
    assert arc.scan_vaults is True
    assert arc.scan_prices is False


def test_chain_price_opt_out_overrides_global_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A staged chain rollout cannot inherit the production price switch.

    Arc discovery is safe to schedule before its dense timestamp cache is
    populated. The per-chain setting must therefore prevent the globally
    enabled production price scan from reaching the historical price reader.
    """
    monkeypatch.setenv("JSON_RPC_ARC", "https://arc.example")

    def fake_verify_archive_node(rpc_url: str, _chain_name: str) -> tuple[str, int]:
        """Return the configured Arc endpoint without a network read."""

        return rpc_url, 100

    def fake_scan_vaults_for_chain(*_args: object, **_kwargs: object) -> tuple[bool, dict[str, int]]:
        """Return a successful Arc discovery result."""

        return True, {
            "chain_id": 5042,
            "start_block": 1,
            "end_block": 100,
            "vault_count": 0,
            "new_vaults": 0,
            "items_scanned": 0,
        }

    def fail_price_scan(*_args: object, **_kwargs: object) -> None:
        """Fail if the disabled Arc price phase is invoked."""

        pytest.fail("Arc price scanning must remain disabled")

    monkeypatch.setattr(scan_all_chains, "verify_archive_node", fake_verify_archive_node)
    monkeypatch.setattr(scan_all_chains, "scan_vaults_for_chain", fake_scan_vaults_for_chain)
    monkeypatch.setattr(scan_all_chains, "scan_prices_for_chain", fail_price_scan)

    result = scan_all_chains.scan_chain(
        scan_all_chains.ChainConfig("Arc", "JSON_RPC_ARC", scan_prices=False),
        scan_prices=True,
        max_workers=1,
        frequency="1h",
        retry_attempt=0,
    )

    assert result.status == "success"
    assert result.vault_scan_ok is True
    assert result.price_scan_ok is None


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
