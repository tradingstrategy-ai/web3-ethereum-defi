"""Tests for the targeted Atoma vault-name migration."""

import importlib.util
from pathlib import Path

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


def load_migration_module():
    """Load the Atoma migration script as a test module.

    :return:
        Loaded migration module.
    """

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-atoma-vault-names.py"
    spec = importlib.util.spec_from_file_location("migrate_atoma_vault_names", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_row(name: str) -> dict:
    """Create a minimal vault metadata row for migration tests.

    :param name:
        Persisted vault name.
    :return:
        Compatible minimal metadata row.
    """

    return {"Name": name, "Protocol": "Atoma"}


def create_vault_database(migration) -> VaultDatabase:
    """Create a metadata cache with both Atoma rows and one unrelated row.

    :param migration:
        Loaded migration module exposing the target update mapping.
    :return:
        Vault database fixture.
    """

    rows = {spec: create_row("Atoma Vault Share") for spec in migration.ATOMA_VAULT_NAME_UPDATES}
    rows[VaultSpec(1, "0x0000000000000000000000000000000000000001")] = create_row("Unrelated vault")
    return VaultDatabase(rows=rows)


def test_atoma_vault_name_updates_use_the_reviewed_strategy_names() -> None:
    """The migration maps both supported Atoma addresses to curated names."""

    migration = load_migration_module()

    assert migration.ATOMA_VAULT_NAME_UPDATES == {
        VaultSpec(42_161, "0xcc56410e1a136af0eceb7241c6ae394f4d8b581c"): "Extended and Nado arbitrage",
        VaultSpec(42_161, "0x1c788e14d8e5b446e3f71b5142e2edabcab36da1"): "Atoma Index",
    }


def test_migrate_atoma_vault_names_updates_only_the_two_target_rows(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The write migration persists both curated names and preserves other rows."""

    migration = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db = create_vault_database(migration)
    vault_db.write(vault_db_path)

    result = migration.migrate_atoma_vault_names(vault_db_path, dry_run=False)
    captured = capsys.readouterr()

    assert result.inspected_rows == len(migration.ATOMA_VAULT_NAME_UPDATES) + 1
    assert {update.spec for update in result.updates} == set(migration.ATOMA_VAULT_NAME_UPDATES)
    assert "old name" in captured.out
    assert (tmp_path / "vault-metadata-db.pickle.bak-atoma-vault-names").exists()

    migrated_db = VaultDatabase.read(vault_db_path)
    for spec, expected_name in migration.ATOMA_VAULT_NAME_UPDATES.items():
        assert migrated_db.rows[spec]["Name"] == expected_name
        assert migrated_db.rows[spec]["Protocol"] == "Atoma"
    unrelated_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    assert migrated_db.rows[unrelated_spec]["Name"] == "Unrelated vault"


def test_migrate_atoma_vault_names_dry_run_does_not_write(tmp_path: Path) -> None:
    """Dry run reports updates without modifying the metadata pickle."""

    migration = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    create_vault_database(migration).write(vault_db_path)

    result = migration.migrate_atoma_vault_names(vault_db_path, dry_run=True)

    assert len(result.updates) == len(migration.ATOMA_VAULT_NAME_UPDATES)
    unchanged_db = VaultDatabase.read(vault_db_path)
    assert {row["Name"] for spec, row in unchanged_db.rows.items() if spec in migration.ATOMA_VAULT_NAME_UPDATES} == {"Atoma Vault Share"}
    assert not (tmp_path / "vault-metadata-db.pickle.bak-atoma-vault-names").exists()


def test_migrate_atoma_vault_names_requires_both_target_rows(tmp_path: Path) -> None:
    """An incomplete cache cannot receive a partial targeted migration."""

    migration = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    present_spec = next(iter(migration.ATOMA_VAULT_NAME_UPDATES))
    VaultDatabase(rows={present_spec: create_row("Atoma Vault Share")}).write(vault_db_path)

    with pytest.raises(KeyError, match="Expected Atoma vault metadata rows are missing"):
        migration.migrate_atoma_vault_names(vault_db_path, dry_run=False)

    assert VaultDatabase.read(vault_db_path).rows[present_spec]["Name"] == "Atoma Vault Share"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, True), ("true", True), ("1", True), ("false", False), ("0", False)],
)
def test_parse_boolean_env(value: str | None, expected: object) -> None:
    """The write-mode switch recognises explicit conventional boolean values."""

    migration = load_migration_module()

    assert migration.parse_boolean_env(value, default=True) is expected
