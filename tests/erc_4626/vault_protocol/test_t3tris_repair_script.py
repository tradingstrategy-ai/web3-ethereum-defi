"""Test T3tris production repair script helpers."""

import datetime
import importlib.util
import sys
import types
from pathlib import Path

from eth_typing import HexAddress

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
