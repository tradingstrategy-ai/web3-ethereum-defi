"""Test T3tris production repair script helpers."""

import datetime
import importlib.util
import sys
import types
from pathlib import Path

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.t3tris.constants import STRADA_YIELD_ARBITRUM_ADDRESS, STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT_BLOCK
from eth_defi.vault.vaultdb import VaultDatabase

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "erc-4626" / "fix-t3tris-vaults.py"


class _HypersyncConfigStub:
    """Minimal constructor-compatible Hypersync config stub."""

    def __init__(self, **_kwargs):
        pass


class _HypersyncClientStub:
    """Minimal Hypersync client stub."""


def _load_fix_t3tris_module():
    """Load the T3tris repair script as a module."""
    hypersync_stub = types.ModuleType("hypersync")
    hypersync_stub.ClientConfig = _HypersyncConfigStub
    hypersync_stub.StreamConfig = _HypersyncConfigStub
    hypersync_stub.HypersyncClient = _HypersyncClientStub
    sys.modules.setdefault("hypersync", hypersync_stub)

    spec = importlib.util.spec_from_file_location("fix_t3tris_vaults", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_first_capital_ref(module):
    """Create a First Capital T3tris vault reference."""
    return module.T3trisVaultReference(
        chain_id=42161,
        address=HexAddress("0x98e43a491a464F0886bC5E57207c340BBed0D01F"),
        name="First - USDC",
        first_seen_at_block=473_516_860,
        first_seen_at=datetime.datetime(2026, 6, 14, 20, 21, 20, tzinfo=datetime.UTC).replace(tzinfo=None),
        curator_name="First Capital",
        verified=True,
    )


def test_t3tris_repair_refreshes_existing_row_missing_curator() -> None:
    """Existing T3tris rows without the API curator name need metadata refresh."""
    module = _load_fix_t3tris_module()
    ref = _make_first_capital_ref(module)
    vault_db = VaultDatabase()
    vault_db.rows[ref.get_spec()] = {
        "Name": "First - USDC",
        "Denomination": "USDC",
        "_manager_name": None,
    }

    assert module.should_refresh_metadata(vault_db, ref)


def test_t3tris_repair_preserves_existing_row_with_matching_curator() -> None:
    """Existing T3tris rows with the API curator name do not need refresh."""
    module = _load_fix_t3tris_module()
    ref = _make_first_capital_ref(module)
    vault_db = VaultDatabase()
    vault_db.rows[ref.get_spec()] = {
        "Name": "First - USDC",
        "Denomination": "USDC",
        "_manager_name": "First Capital",
    }

    assert not module.should_refresh_metadata(vault_db, ref)


def test_t3tris_repair_keeps_reviewed_migration_vault_when_api_omits_it(monkeypatch) -> None:
    """A migrated vault must remain repairable after the frontend API omits it."""
    module = _load_fix_t3tris_module()
    monkeypatch.setattr(module, "fetch_t3tris_vaults", lambda: [])

    refs = module.load_t3tris_vault_references()

    strada = next(ref for ref in refs if ref.address.lower() == STRADA_YIELD_ARBITRUM_ADDRESS.lower())
    assert strada.name == "Strada Yield"
    assert strada.first_seen_at_block == STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT_BLOCK


def test_t3tris_repair_keeps_reviewed_migration_vault_with_verified_filter(monkeypatch) -> None:
    """A reviewed migration must not be discarded by an API-specific filter."""
    module = _load_fix_t3tris_module()
    monkeypatch.setenv("T3TRIS_VERIFIED_ONLY", "true")

    refs = module.filter_references(module.get_reviewed_t3tris_migration_references())

    assert [ref.address.lower() for ref in refs] == [STRADA_YIELD_ARBITRUM_ADDRESS.lower()]


def test_t3tris_repair_uses_configuration_threshold_for_reviewed_migration() -> None:
    """The repair must not invent deposits for a migration-pool vault."""
    module = _load_fix_t3tris_module()
    ref = next(ref for ref in module.get_reviewed_t3tris_migration_references() if ref.address.lower() == STRADA_YIELD_ARBITRUM_ADDRESS.lower())
    vault_db = VaultDatabase()

    module.upsert_lead(vault_db, ref)
    detection = module.create_detection(ref, {module.ERC4626Feature.t3tris_like}, ref.first_seen_at)

    lead = vault_db.leads[ref.get_spec()]
    assert lead.deposit_count == 0
    assert lead.configuration_count == 1
    assert detection.deposit_count == 0
    assert detection.configuration_count == 1
