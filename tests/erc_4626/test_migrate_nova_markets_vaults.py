"""Preserve historical state while removing Nova's stale gTrade identity."""

import datetime
import importlib.util
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_POOL_ADDRESS, NOVA_POOL_APP
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def migration() -> ModuleType:
    """Import the standalone repair without running its environment entrypoint."""
    path = Path(__file__).resolve().parents[2] / "eth_defi/vault_protocols/repairs/nova.py"
    spec = importlib.util.spec_from_file_location("migrate_nova_markets_vaults", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def old_metadata(tmp_path: Path) -> Path:
    """Write a real pickle containing stale identity, leads and unrelated rows."""
    timestamp = datetime.datetime(2026, 9, 25, tzinfo=datetime.UTC).replace(tzinfo=None)
    spec = VaultSpec(42161, NOVA_POOL_ADDRESS)
    detection = ERC4262VaultDetection(42161, NOVA_POOL_ADDRESS, 507000000, timestamp, {ERC4626Feature.gains_like}, timestamp, 27, 1)
    database = VaultDatabase(
        rows={
            spec: {"Protocol": "Gains Network", "Name": "gTrade (Nova USDC)", "_detection_data": detection, "_flags": {VaultFlag.illiquid}, "_manager_name": "Manual attribution", "NAV": "preserve observation"},
            VaultSpec(1, NOVA_POOL_ADDRESS): {"Protocol": "Other chain", "_notes": "Preserve"},
            VaultSpec(42161, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"): {"Protocol": "Gains Network", "_notes": "Preserve"},
        },
        leads={spec: PotentialVaultMatch(42161, NOVA_POOL_ADDRESS, 507000000, timestamp, deposit_count=27)},
        last_scanned_block={42161: 518876926},
    )
    path = tmp_path / "vault-metadata-db.pickle"
    database.write(path)
    return path


def test_nova_migration_dry_run_preserves_exact_bytes(migration: ModuleType, old_metadata: Path) -> None:
    """Default execution cannot modify the pickle or create a backup."""
    original = old_metadata.read_bytes()
    result = migration.migrate_nova_markets_vaults(old_metadata)
    assert "_detection_data" in result.changed_fields
    assert "Protocol" in result.changed_fields
    assert result.backup_path is None
    assert old_metadata.read_bytes() == original


def test_nova_reclassification_retains_leads_prices_and_reader_boundaries(migration: ModuleType, old_metadata: Path) -> None:
    """Existing observations survive the protocol change and an unchanged rerun."""
    original_bytes = old_metadata.read_bytes()
    before = VaultDatabase.read(old_metadata)
    state_paths = [old_metadata.parent / "reader-state.pickle", old_metadata.parent / "vault-prices-1h.parquet"]
    for path in state_paths:
        path.write_bytes(b"preserve historical state")
    result = migration.migrate_nova_markets_vaults(old_metadata, dry_run=False)
    assert result.backup_path.read_bytes() == original_bytes
    after = VaultDatabase.read(old_metadata)
    spec = VaultSpec(42161, NOVA_POOL_ADDRESS)
    row = after.rows[spec]
    assert row["Protocol"] == "Nova Markets"
    assert row["protocol_slug"] == "nova-markets"
    assert row["Name"] == "Nova Conviction Pool"
    assert row["Link"] == NOVA_POOL_APP
    assert row["features"] == {ERC4626Feature.nova_markets_like}
    assert row["_detection_data"].features == {ERC4626Feature.nova_markets_like}
    assert row["_curator_slug"] == "nova-markets"
    assert row["_detection_data"].first_seen_at_block == before.rows[spec]["_detection_data"].first_seen_at_block
    assert row["_detection_data"].deposit_count == before.rows[spec]["_detection_data"].deposit_count
    assert row["_flags"] == before.rows[spec]["_flags"]
    assert row["_manager_name"] == "Manual attribution"
    assert row["NAV"] == "preserve observation"
    assert after.leads == before.leads
    assert after.last_scanned_block == before.last_scanned_block
    for other_spec in before.rows.keys() - {spec}:
        assert after.rows[other_spec] == before.rows[other_spec]
    assert all(path.read_bytes() == b"preserve historical state" for path in state_paths)
    applied = old_metadata.read_bytes()
    second = migration.migrate_nova_markets_vaults(old_metadata, dry_run=False)
    assert second.changed_fields == ()
    assert second.backup_path is None
    assert old_metadata.read_bytes() == applied


def test_nova_migration_rejects_conflicting_detection(migration: ModuleType, old_metadata: Path) -> None:
    """A row with mismatched chain cannot be rewritten based on its label."""
    database = VaultDatabase.read(old_metadata)
    row = database.rows[VaultSpec(42161, NOVA_POOL_ADDRESS)]
    row["_detection_data"] = replace(row["_detection_data"], chain=1)
    database.write(old_metadata)
    original = old_metadata.read_bytes()
    with pytest.raises(ValueError, match="conflicting identity"):
        migration.migrate_nova_markets_vaults(old_metadata, dry_run=False)
    assert old_metadata.read_bytes() == original
    assert not tuple(old_metadata.parent.glob("*.bak-nova-markets*"))
