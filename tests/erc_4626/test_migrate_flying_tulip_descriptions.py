"""Test the fixed-scope Flying Tulip public-copy migration."""

import importlib.util
from pathlib import Path

import pytest

from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_NOTES, FLYING_TULIP_SFTUSD_BY_CHAIN, FLYING_TULIP_SHORT_DESCRIPTION
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


def load_migration_module():
    """Load the hyphenated migration script as a Python module.

    :return:
        Imported Flying Tulip migration module.
    """

    repository_root = Path(__file__).resolve().parents[2]
    script_path = repository_root / "scripts" / "erc-4626" / "migrate-flying-tulip-descriptions.py"
    spec = importlib.util.spec_from_file_location("migrate_flying_tulip_descriptions", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_vault_database() -> tuple[VaultDatabase, VaultSpec, dict]:
    """Create three stale Flying Tulip rows and one unrelated row.

    :return:
        Database, unrelated specification and unrelated row object.
    """

    rows = {
        VaultSpec(chain_id, address): {
            "Protocol": "Flying Tulip",
            "Address": address,
            "Name": "Staked Flying Tulip USD",
            "_short_description": "Old summary.",
            "_description": None,
            "_notes": "Old notes.",
            "_protocol_notes": "Old protocol notes.",
            "manual_field": f"preserve-{chain_id}",
        }
        for chain_id, address in FLYING_TULIP_SFTUSD_BY_CHAIN.items()
    }
    unrelated_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    unrelated_row = {
        "Protocol": "Morpho",
        "Address": unrelated_spec.vault_address,
        "_short_description": "Unchanged summary.",
        "_notes": "Unchanged notes.",
    }
    rows[unrelated_spec] = unrelated_row
    vault_db = VaultDatabase(
        rows=rows,
        leads={unrelated_spec: object()},
        last_scanned_block={1: 25_000_000},
    )
    return vault_db, unrelated_spec, unrelated_row


def test_migrate_flying_tulip_descriptions_updates_only_public_copy() -> None:
    """Update all reviewed rows while preserving unrelated fields and state."""

    module = load_migration_module()
    vault_db, unrelated_spec, unrelated_row = create_vault_database()

    result = module.migrate_flying_tulip_descriptions(vault_db, dry_run=False)

    assert result.inspected_rows == len(FLYING_TULIP_SFTUSD_BY_CHAIN)
    assert result.updated_rows == len(FLYING_TULIP_SFTUSD_BY_CHAIN)
    assert result.updated_fields == len(FLYING_TULIP_SFTUSD_BY_CHAIN) * 3
    for chain_id, address in FLYING_TULIP_SFTUSD_BY_CHAIN.items():
        row = vault_db.rows[VaultSpec(chain_id, address)]
        assert row["_short_description"] == FLYING_TULIP_SHORT_DESCRIPTION
        assert row["_notes"] == FLYING_TULIP_NOTES
        assert row["_protocol_notes"] == FLYING_TULIP_NOTES
        assert row["_description"] is None
        assert row["manual_field"] == f"preserve-{chain_id}"
    assert vault_db.rows[unrelated_spec] is unrelated_row
    assert vault_db.leads[unrelated_spec] is not None
    assert vault_db.last_scanned_block == {1: 25_000_000}


def test_migrate_flying_tulip_descriptions_dry_run_does_not_mutate() -> None:
    """Report all stale fields without mutating the supplied database."""

    module = load_migration_module()
    vault_db, _, _ = create_vault_database()
    ethereum_spec = VaultSpec(1, FLYING_TULIP_SFTUSD_BY_CHAIN[1])
    original_row = vault_db.rows[ethereum_spec].copy()

    result = module.migrate_flying_tulip_descriptions(vault_db, dry_run=True)

    assert result.updated_rows == len(FLYING_TULIP_SFTUSD_BY_CHAIN)
    assert result.updated_fields == len(FLYING_TULIP_SFTUSD_BY_CHAIN) * 3
    assert vault_db.rows[ethereum_spec] == original_row


def test_migrate_flying_tulip_descriptions_is_idempotent() -> None:
    """Report no changes after the maintained public copy is already stored."""

    module = load_migration_module()
    vault_db, _, _ = create_vault_database()
    module.migrate_flying_tulip_descriptions(vault_db, dry_run=False)

    result = module.migrate_flying_tulip_descriptions(vault_db, dry_run=False)

    assert result.updated_rows == 0
    assert result.updated_fields == 0
    assert result.updates == ()


def test_migrate_flying_tulip_descriptions_requires_complete_reviewed_scope() -> None:
    """Fail before mutation when any reviewed deployment is absent."""

    module = load_migration_module()
    vault_db, _, _ = create_vault_database()
    ethereum_spec = VaultSpec(1, FLYING_TULIP_SFTUSD_BY_CHAIN[1])
    sonic_spec = VaultSpec(146, FLYING_TULIP_SFTUSD_BY_CHAIN[146])
    original_ethereum_row = vault_db.rows[ethereum_spec].copy()
    del vault_db.rows[sonic_spec]

    with pytest.raises(ValueError, match="missing reviewed rows"):
        module.migrate_flying_tulip_descriptions(vault_db, dry_run=False)

    assert vault_db.rows[ethereum_spec] == original_ethereum_row


def test_flying_tulip_description_migration_main_dry_run_and_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Exercise the environment-driven dry run and atomic persistent write."""

    module = load_migration_module()
    vault_db, unrelated_spec, _ = create_vault_database()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VAULT_DB_PATH", str(vault_db_path))
    monkeypatch.setenv("DRY_RUN", "true")

    module.main()
    capsys.readouterr()

    dry_run_database = VaultDatabase.read(vault_db_path)
    ethereum_spec = VaultSpec(1, FLYING_TULIP_SFTUSD_BY_CHAIN[1])
    assert dry_run_database.rows[ethereum_spec]["_notes"] == "Old notes."

    monkeypatch.setenv("DRY_RUN", "false")
    module.main()
    capsys.readouterr()

    migrated_database = VaultDatabase.read(vault_db_path)
    assert migrated_database.rows[ethereum_spec]["_notes"] == FLYING_TULIP_NOTES
    assert migrated_database.rows[unrelated_spec]["_notes"] == "Unchanged notes."
    assert not (tmp_path / "vault-prices-1h.parquet").exists()
    assert not (tmp_path / "vault-reader-state-1h.pickle").exists()
