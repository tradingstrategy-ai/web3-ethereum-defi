"""Test bounded per-chain vault lead-discovery state."""

import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_typing import HexAddress

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.discovery_base import LeadScanReport, PotentialVaultMatch
from eth_defi.erc_4626.lead_discovery_state import (
    LeadDiscoveryState,
    create_lead_discovery_signature,
    get_lead_discovery_state_path,
    hash_function_source,
    load_lead_discovery_state,
    save_lead_discovery_state,
    validate_lead_discovery_state,
)
from eth_defi.erc_4626.lead_scan_core import scan_leads
from eth_defi.vault import scan_all_chains
from eth_defi.vault.base import VaultSpec
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

    def fake_web3(*_args: object, **_kwargs: object) -> SimpleNamespace:
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

    def unexpected_scan_leads(**_kwargs: object) -> None:
        """Fail when a cache hit attempts lead discovery."""
        pytest.fail("cache hit must not call scan_leads")

    monkeypatch.setattr(scan_all_chains, "scan_leads", unexpected_scan_leads)

    success, metrics = scan_all_chains.scan_vaults_for_chain("https://rpc.example", 1, vault_db_path=vault_db_path)

    assert success is True
    assert metrics["lead_discovery_cache_hit"] is True
    assert metrics["items_scanned"] == 0
    assert metrics["end_block"] == LAST_CACHED_BLOCK


def test_signature_change_forces_metadata_refresh_and_saves_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A changed configuration invokes metadata refresh and persists its state."""

    vault_db_path = tmp_path / "vault-metadata-db.pickle"

    def fake_web3(*_args: object, **_kwargs: object) -> SimpleNamespace:
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

    def fake_scan_leads(**_kwargs: object) -> LeadScanReport:
        """Simulate the metadata database write owned by real discovery."""

        VaultDatabase(last_scanned_block={1: FULL_SCAN_BLOCK}).write(vault_db_path)
        return LeadScanReport(end_block=FULL_SCAN_BLOCK)

    monkeypatch.setattr(scan_all_chains, "scan_leads", fake_scan_leads)

    success, metrics = scan_all_chains.scan_vaults_for_chain("https://rpc.example", 1, vault_db_path=vault_db_path)

    state, reason = load_lead_discovery_state(get_lead_discovery_state_path(tmp_path, 1))
    assert success is True
    assert metrics["lead_discovery_cache_hit"] is False
    assert reason is None
    assert state.completed_block == FULL_SCAN_BLOCK


def test_incremental_discovery_keeps_cursor_and_seeds_persisted_leads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incremental discovery classifies saved leads without replaying historical events."""

    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_address = HexAddress("0x0000000000000000000000000000000000000001")
    persisted_lead = PotentialVaultMatch(
        chain=1,
        address=vault_address,
        first_seen_at_block=123,
        first_seen_at=native_datetime_utc_now(),
        deposit_count=1,
    )
    VaultDatabase(
        leads={VaultSpec(1, vault_address): persisted_lead},
        last_scanned_block={1: LAST_CACHED_BLOCK},
    ).write(vault_db_path)

    captured: dict[str, object] = {}

    class FakeHypersyncVaultDiscover:
        """Capture scanner inputs without contacting HyperSync or an RPC endpoint."""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        @staticmethod
        def seed_existing_leads(leads: dict[HexAddress, PotentialVaultMatch]) -> None:
            captured["seeded_leads"] = leads

        @staticmethod
        def scan_vaults(start_block: int, end_block: int) -> LeadScanReport:
            captured["start_block"] = start_block
            captured["end_block"] = end_block
            return LeadScanReport(
                leads=captured["seeded_leads"],  # type: ignore[arg-type]
                start_block=start_block,
                end_block=end_block,
            )

    fake_web3 = SimpleNamespace(
        eth=SimpleNamespace(chain_id=1, block_number=FULL_SCAN_BLOCK),
        provider=object(),
    )
    monkeypatch.setattr("eth_defi.erc_4626.lead_scan_core.get_chain_name", lambda _chain_id: "Test")
    monkeypatch.setattr("eth_defi.erc_4626.lead_scan_core.get_provider_name", lambda _provider: "Test RPC")
    monkeypatch.setattr("eth_defi.erc_4626.lead_scan_core.MultiProviderWeb3Factory", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "eth_defi.erc_4626.lead_scan_core.configure_hypersync_from_env",
        lambda *_args, **_kwargs: SimpleNamespace(hypersync_client=object(), hypersync_url="https://hypersync.example"),
    )
    monkeypatch.setattr("eth_defi.erc_4626.hypersync_discovery.HypersyncVaultDiscover", FakeHypersyncVaultDiscover)

    report = scan_leads(
        "https://rpc.example",
        vault_db_path,
        max_workers=1,
        end_block=FULL_SCAN_BLOCK,
        web3=fake_web3,
        printer=lambda _message: None,
    )

    assert captured["start_block"] == LAST_CACHED_BLOCK + 1
    assert captured["end_block"] == FULL_SCAN_BLOCK
    assert captured["seeded_leads"] == {vault_address: persisted_lead}
    assert report.start_block == LAST_CACHED_BLOCK + 1
    assert VaultDatabase.read(vault_db_path).last_scanned_block == {1: FULL_SCAN_BLOCK}


def test_initial_discovery_refuses_json_rpc_event_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initial discovery refuses the unsupported genesis-to-head RPC fallback."""

    fake_web3 = SimpleNamespace(
        eth=SimpleNamespace(chain_id=1, block_number=FULL_SCAN_BLOCK),
        provider=object(),
    )
    monkeypatch.setattr("eth_defi.erc_4626.lead_scan_core.get_chain_name", lambda _chain_id: "Test")
    monkeypatch.setattr("eth_defi.erc_4626.lead_scan_core.get_provider_name", lambda _provider: "Test RPC")
    monkeypatch.setattr("eth_defi.erc_4626.lead_scan_core.MultiProviderWeb3Factory", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "eth_defi.erc_4626.lead_scan_core.configure_hypersync_from_env",
        lambda *_args, **_kwargs: SimpleNamespace(hypersync_client=None, hypersync_url=None),
    )

    with pytest.raises(RuntimeError, match="requires HyperSync"):
        scan_leads(
            "https://rpc.example",
            tmp_path / "vault-metadata-db.pickle",
            max_workers=1,
            end_block=FULL_SCAN_BLOCK,
            web3=fake_web3,
            printer=lambda _message: None,
        )


def test_failed_metadata_refresh_keeps_previous_cache_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed refresh does not hide a retry behind a fresh cache timestamp."""

    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(last_scanned_block={1: LAST_CACHED_BLOCK}).write(vault_db_path)
    state_path = get_lead_discovery_state_path(tmp_path, 1)
    previous_state = LeadDiscoveryState(
        chain_id=1,
        signature="obsolete-signature",
        signature_configuration={},
        completed_at=native_datetime_utc_now(),
        completed_block=LAST_CACHED_BLOCK,
    )
    save_lead_discovery_state(previous_state, state_path)

    def fake_web3(*_args: object, **_kwargs: object) -> SimpleNamespace:
        """Return a minimal verified Web3 substitute."""
        return SimpleNamespace(eth=SimpleNamespace(chain_id=1))

    def failing_scan_leads(**_kwargs: object) -> LeadScanReport:
        """Simulate a lagging or unavailable event backend."""
        message = "Hypersync has not advanced"
        raise RuntimeError(message)

    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", fake_web3)
    monkeypatch.setattr(
        scan_all_chains,
        "build_chain_configs",
        lambda: [ChainConfig(name="Test", env_var="JSON_RPC_TEST", scan_vaults=True)],
    )
    monkeypatch.setattr(scan_all_chains, "scan_leads", failing_scan_leads)

    success, _metrics = scan_all_chains.scan_vaults_for_chain("https://rpc.example", 1, vault_db_path=vault_db_path)

    state, reason = load_lead_discovery_state(state_path)
    assert success is False
    assert reason is None
    assert state == previous_state


def test_incremental_discovery_rejects_nonadvancing_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lagging HyperSync head cannot rewind the persisted discovery cursor."""

    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(last_scanned_block={1: LAST_CACHED_BLOCK}).write(vault_db_path)

    class FakeHypersyncVaultDiscover:
        """Return a stale clipped head without issuing network requests."""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        @staticmethod
        def seed_existing_leads(_leads: dict[HexAddress, PotentialVaultMatch]) -> None:
            pass

        @staticmethod
        def scan_vaults(start_block: int, _end_block: int) -> LeadScanReport:
            return LeadScanReport(start_block=start_block, end_block=start_block)

    fake_web3 = SimpleNamespace(
        eth=SimpleNamespace(chain_id=1, block_number=FULL_SCAN_BLOCK),
        provider=object(),
    )
    monkeypatch.setattr("eth_defi.erc_4626.lead_scan_core.get_chain_name", lambda _chain_id: "Test")
    monkeypatch.setattr("eth_defi.erc_4626.lead_scan_core.get_provider_name", lambda _provider: "Test RPC")
    monkeypatch.setattr("eth_defi.erc_4626.lead_scan_core.MultiProviderWeb3Factory", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "eth_defi.erc_4626.lead_scan_core.configure_hypersync_from_env",
        lambda *_args, **_kwargs: SimpleNamespace(hypersync_client=object(), hypersync_url="https://hypersync.example"),
    )
    monkeypatch.setattr("eth_defi.erc_4626.hypersync_discovery.HypersyncVaultDiscover", FakeHypersyncVaultDiscover)

    with pytest.raises(RuntimeError, match="did not advance past its scan range"):
        scan_leads(
            "https://rpc.example",
            vault_db_path,
            max_workers=1,
            end_block=FULL_SCAN_BLOCK,
            web3=fake_web3,
            printer=lambda _message: None,
        )

    assert VaultDatabase.read(vault_db_path).last_scanned_block == {1: LAST_CACHED_BLOCK}
