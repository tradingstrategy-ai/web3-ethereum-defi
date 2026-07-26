"""Test bounded per-chain vault lead-discovery state."""

import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.discovery_base import LeadScanReport
from eth_defi.erc_4626.lead_discovery_state import (
    LeadDiscoveryState,
    create_lead_discovery_signature,
    get_lead_discovery_state_path,
    hash_function_source,
    load_lead_discovery_state,
    save_lead_discovery_state,
    validate_lead_discovery_state,
)
from eth_defi.vault import scan_all_chains
from eth_defi.vault.scan_all_chains import ChainConfig
from eth_defi.vault.vaultdb import VaultDatabase

LAST_CACHED_BLOCK = 456
FULL_SCAN_BLOCK = 789


def test_function_source_hash_ignores_name_and_docstring() -> None:
    """Equivalent documented functions receive the same source digest."""

    def first(value: int) -> int:
        """First description."""
        return value + 1

    def second(value: int) -> int:
        """Second description."""
        return value + 1

    def changed(value: int) -> int:
        """Same signature but different behaviour."""
        return value + 2

    assert hash_function_source(first) == hash_function_source(second)
    assert hash_function_source(first) != hash_function_source(changed)


def test_signature_changes_only_for_enabled_chain_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The signature includes enabled chains but excludes disabled chains."""

    original_signature, original_configuration = create_lead_discovery_signature([("Ethereum", "JSON_RPC_ETHEREUM")])
    changed_signature, changed_configuration = create_lead_discovery_signature([("Ethereum", "JSON_RPC_ETHEREUM"), ("Base", "JSON_RPC_BASE")])

    assert original_signature != changed_signature
    assert original_configuration["enabled_chains"] == [{"name": "Ethereum", "rpc_environment_variable": "JSON_RPC_ETHEREUM"}]
    assert changed_configuration["enabled_chains"][0]["name"] == "Base"

    monkeypatch.setattr(
        scan_all_chains,
        "build_chain_configs",
        lambda: [
            ChainConfig(name="Ethereum", env_var="JSON_RPC_ETHEREUM", scan_vaults=True),
            ChainConfig(name="Unichain", env_var="JSON_RPC_UNICHAIN", scan_vaults=False),
        ],
    )
    enabled = [(config.name, config.env_var) for config in scan_all_chains.build_chain_configs() if config.scan_vaults]
    assert create_lead_discovery_signature(enabled)[0] == original_signature


def test_state_round_trip_and_timeout_validation(tmp_path: Path) -> None:
    """Persisted state is valid only with matching cache inputs and age."""

    path = get_lead_discovery_state_path(tmp_path, 1)
    signature = "signature"
    now = native_datetime_utc_now()
    state = LeadDiscoveryState(
        chain_id=1,
        signature=signature,
        signature_configuration={"enabled_chains": []},
        completed_at=now - datetime.timedelta(days=6),
        completed_block=123,
    )
    save_lead_discovery_state(state, path)

    loaded, reason = load_lead_discovery_state(path)
    assert reason is None
    assert loaded == state
    assert validate_lead_discovery_state(loaded, 1, signature, now, datetime.timedelta(days=7), has_metadata_cursor=True) is None
    assert "expired" in validate_lead_discovery_state(loaded, 1, signature, now, datetime.timedelta(days=5), has_metadata_cursor=True)
    assert "signature changed" in validate_lead_discovery_state(loaded, 1, "different", now, datetime.timedelta(days=7), has_metadata_cursor=True)
    assert "no discovery cursor" in validate_lead_discovery_state(loaded, 1, signature, now, datetime.timedelta(days=7), has_metadata_cursor=False)


def test_fresh_state_skips_lead_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching fresh state returns without calling the costly scanner."""

    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db = VaultDatabase(last_scanned_block={1: LAST_CACHED_BLOCK})
    vault_db.write(vault_db_path)

    def fake_web3(*_args, **_kwargs):
        """Return a minimal verified Web3 substitute."""
        return SimpleNamespace(eth=SimpleNamespace(chain_id=1))

    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", fake_web3)
    monkeypatch.setattr(
        scan_all_chains,
        "build_chain_configs",
        lambda: [ChainConfig(name="Test", env_var="JSON_RPC_TEST", scan_vaults=True)],
    )
    signature, configuration = create_lead_discovery_signature([("Test", "JSON_RPC_TEST")])
    save_lead_discovery_state(
        LeadDiscoveryState(1, signature, configuration, native_datetime_utc_now(), LAST_CACHED_BLOCK),
        get_lead_discovery_state_path(tmp_path, 1),
    )

    def unexpected_scan_leads(**_kwargs):
        """Fail when a cache hit attempts lead discovery."""
        pytest.fail("cache hit must not call scan_leads")

    monkeypatch.setattr(scan_all_chains, "scan_leads", unexpected_scan_leads)

    success, metrics = scan_all_chains.scan_vaults_for_chain("https://rpc.example", 1, vault_db_path=vault_db_path)

    assert success is True
    assert metrics["lead_discovery_cache_hit"] is True
    assert metrics["items_scanned"] == 0
    assert metrics["end_block"] == LAST_CACHED_BLOCK


def test_signature_change_forces_full_discovery_and_saves_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed configuration invokes full discovery and persists its state."""

    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    received_kwargs = {}

    def fake_web3(*_args, **_kwargs):
        """Return a minimal verified Web3 substitute."""
        return SimpleNamespace(eth=SimpleNamespace(chain_id=1))

    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", fake_web3)
    monkeypatch.setattr(
        scan_all_chains,
        "build_chain_configs",
        lambda: [ChainConfig(name="Test", env_var="JSON_RPC_TEST", scan_vaults=True)],
    )
    save_lead_discovery_state(
        LeadDiscoveryState(
            chain_id=1,
            signature="obsolete-signature",
            signature_configuration={},
            completed_at=native_datetime_utc_now(),
            completed_block=LAST_CACHED_BLOCK,
        ),
        get_lead_discovery_state_path(tmp_path, 1),
    )

    def fake_scan_leads(**kwargs) -> LeadScanReport:
        """Simulate the metadata database write owned by real discovery."""

        received_kwargs.update(kwargs)
        VaultDatabase(last_scanned_block={1: FULL_SCAN_BLOCK}).write(vault_db_path)
        return LeadScanReport(end_block=FULL_SCAN_BLOCK)

    monkeypatch.setattr(scan_all_chains, "scan_leads", fake_scan_leads)

    success, metrics = scan_all_chains.scan_vaults_for_chain("https://rpc.example", 1, vault_db_path=vault_db_path)

    state, reason = load_lead_discovery_state(get_lead_discovery_state_path(tmp_path, 1))
    assert success is True
    assert metrics["lead_discovery_cache_hit"] is False
    assert received_kwargs["force_full_discovery"] is True
    assert reason is None
    assert state.completed_block == FULL_SCAN_BLOCK
