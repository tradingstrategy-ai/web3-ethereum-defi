"""Metadata-only Arcadia repair safety and idempotence checks."""

import copy
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from eth_defi.erc_4626.vault_protocol.arcadia.offchain_metadata import parse_arcadia_pools
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

USDG_TRANCHE = "0xa5e1e1f92a244f192943899eb5810e2bab372ea4"
EXPECTED_REPAIRED_ROWS = 3


@pytest.fixture
def migration() -> ModuleType:
    """Load the environment-driven migration without executing its main."""
    path = Path(__file__).resolve().parents[2] / "scripts/erc-4626/migrate-arcadia-vault-metadata.py"
    spec = importlib.util.spec_from_file_location("migrate_arcadia_metadata", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def vault_db() -> VaultDatabase:
    """Supply missing, label-only, curated and unrelated persisted rows."""
    return VaultDatabase(
        rows={
            VaultSpec(4663, USDG_TRANCHE): {"Protocol": "Arcadia Finance", "Name": "Senior ArcadiaV2 Global Dollar", "Denomination": "USDG", "_description": "Arcadia USDG Pool", "_short_description": None, "_notes": "Keep this note", "NAV": 100},
            VaultSpec(8453, USDG_TRANCHE): {"Protocol": "Arcadia Finance", "Name": "Senior ArcadiaV2 USDC", "Denomination": "USDC", "_description": "Curated narrative. Keep every word.", "_short_description": None},
            VaultSpec(10, USDG_TRANCHE): {"Protocol": "Arcadia Finance", "Name": "ArcadiaV2 WETH", "Denomination": "WETH", "_description": None, "_short_description": "Curated summary"},
            VaultSpec(1, USDG_TRANCHE): {"Protocol": "Other protocol", "_description": None, "_short_description": None},
        },
        leads={VaultSpec(8453, "0x1111111111111111111111111111111111111111"): {"unchanged": True}},
        last_scanned_block={4663: 12345},
    )


def test_repair_dry_run_preservation_and_idempotence(migration: ModuleType, vault_db: VaultDatabase) -> None:
    """Change only missing/label descriptions, preserving all historical context."""
    before = copy.deepcopy(vault_db)
    changes = migration.migrate_arcadia_vault_metadata(vault_db, {}, dry_run=True)
    assert len(changes) == EXPECTED_REPAIRED_ROWS
    assert vault_db == before
    assert migration.migrate_arcadia_vault_metadata(vault_db, {}, dry_run=False) == changes
    assert migration.migrate_arcadia_vault_metadata(vault_db, {}, dry_run=False) == {}
    for spec, old_row in before.rows.items():
        for field, value in old_row.items():
            if field not in changes.get(spec, ()):
                assert vault_db.rows[spec][field] == value
    assert vault_db.leads == before.leads
    assert vault_db.last_scanned_block == before.last_scanned_block
    robinhood = vault_db.rows[VaultSpec(4663, USDG_TRANCHE)]
    assert "senior tranche" in robinhood["_description"]
    assert "USDG" in robinhood["_short_description"]
    assert vault_db.rows[VaultSpec(8453, USDG_TRANCHE)]["_short_description"] == "Curated narrative."
    assert vault_db.rows[VaultSpec(10, USDG_TRANCHE)]["_short_description"] == "Curated summary"


def test_repair_prefers_api_narrative_and_keeps_existing_copy(migration: ModuleType, vault_db: VaultDatabase) -> None:
    """The scanner resolver also supplies repair copy without overwriting prose."""
    payload = [{"address": "0xf37c0c5996503fdd2b5ccce36e659cd30393ae59", "name": "USDG", "description": "Provider lending narrative. More detail.", "tranches": [{"address": USDG_TRANCHE, "name": "Senior USDG"}]}]
    index = parse_arcadia_pools(4663, payload)
    migration.migrate_arcadia_vault_metadata(vault_db, {4663: index}, dry_run=False)
    assert vault_db.rows[VaultSpec(4663, USDG_TRANCHE)]["_description"] == "Provider lending narrative. More detail."
    assert vault_db.rows[VaultSpec(4663, USDG_TRANCHE)]["_short_description"] == "Provider lending narrative."
    assert vault_db.rows[VaultSpec(8453, USDG_TRANCHE)]["_description"] == "Curated narrative. Keep every word."


def test_main_default_dry_run_backup_and_atomic_write(migration: ModuleType, vault_db: VaultDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The runnable command defaults to read-only and backs up actual writes."""
    path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(path)
    original_bytes = path.read_bytes()
    sentinel = tmp_path / "vault-prices-1h.parquet"
    sentinel.write_bytes(b"historical prices must stay untouched")
    monkeypatch.setenv("VAULT_DB_PATH", str(path))
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(migration, "fetch_arcadia_vaults", lambda *_args, **_kwargs: None)
    migration.main()
    assert path.read_bytes() == original_bytes
    assert not list(tmp_path.glob("*.bak-*"))

    monkeypatch.setenv("DRY_RUN", "false")
    migration.main()
    backups = list(tmp_path.glob("*.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original_bytes
    repaired = VaultDatabase.read(path)
    assert repaired.leads == vault_db.leads
    assert repaired.last_scanned_block == vault_db.last_scanned_block
    assert repaired.rows[VaultSpec(4663, USDG_TRANCHE)]["_description"] != "Arcadia USDG Pool"
    assert sentinel.read_bytes() == b"historical prices must stay untouched"
    migration.main()
    assert list(tmp_path.glob("*.bak-*")) == backups


def test_backup_never_overwrites_previous_file(migration: ModuleType, tmp_path: Path) -> None:
    """Repeated operator runs keep all prior pre-write metadata snapshots."""
    path = tmp_path / "metadata.pickle"
    path.write_bytes(b"first snapshot")
    first = migration._backup_metadata(path)
    path.write_bytes(b"second snapshot")
    second = migration._backup_metadata(path)
    assert first != second
    assert first.read_bytes() == b"first snapshot"
    assert second.read_bytes() == b"second snapshot"
