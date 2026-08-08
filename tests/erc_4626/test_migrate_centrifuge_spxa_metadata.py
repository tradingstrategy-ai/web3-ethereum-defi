"""Tests for the targeted Centrifuge SPXA metadata migration."""

import importlib.util
from pathlib import Path

import pytest

from eth_defi.erc_4626.vault_protocol.centrifuge.vault import CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY, DESPXA_BASE_VAULT_ADDRESS, SPXA_BASE_VAULT_ADDRESS
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase


def load_migration_module():
    """Load the Centrifuge SPXA migration script as a test module.

    :return:
        Loaded migration module.
    """

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-centrifuge-spxa-metadata.py"
    spec = importlib.util.spec_from_file_location("migrate_centrifuge_spxa_metadata", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_row(name: str, *, flags: object = None, description: str | None = None, short_description: str | None = None) -> dict:
    """Create a minimal vault metadata row for migration tests.

    :param name:
        Persisted vault name.
    :param flags:
        Persisted ``_flags`` value.
    :param description:
        Persisted long description.
    :param short_description:
        Persisted listing description.
    :return:
        Compatible minimal metadata row.
    """

    return {
        "Name": name,
        "Protocol": "Centrifuge",
        "_description": description,
        "_short_description": short_description,
        "_flags": flags or set(),
    }


def create_vault_database(migration) -> VaultDatabase:
    """Create a metadata cache with both target rows and one unrelated row.

    :param migration:
        Loaded migration module exposing the target specs.
    :return:
        Vault database fixture.
    """

    rows = {
        VaultSpec(migration.BASE_CHAIN_ID, SPXA_BASE_VAULT_ADDRESS): create_row("Janus Henderson Anemoy S&P500 Fund"),
        VaultSpec(migration.BASE_CHAIN_ID, DESPXA_BASE_VAULT_ADDRESS): create_row("DeFi Janus Henderson Anemoy S&P500 Fund Token"),
        VaultSpec(1, "0x0000000000000000000000000000000000000001"): create_row("Unrelated vault", flags={VaultFlag.illiquid}),
    }
    return VaultDatabase(rows=rows)


def test_centrifuge_spxa_metadata_targets_reviewed_base_rows() -> None:
    """The migration targets SPXA and deSPXA and derives copy from the adapter."""

    migration = load_migration_module()

    assert migration.CENTRIFUGE_SPXA_METADATA_SPECS == (
        VaultSpec(8_453, SPXA_BASE_VAULT_ADDRESS),
        VaultSpec(8_453, DESPXA_BASE_VAULT_ADDRESS),
    )
    spxa_short, spxa_description, spxa_flags = migration.get_expected_metadata(VaultSpec(8_453, SPXA_BASE_VAULT_ADDRESS))
    despxa_short, despxa_description, despxa_flags = migration.get_expected_metadata(VaultSpec(8_453, DESPXA_BASE_VAULT_ADDRESS))

    assert spxa_short == CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY[SPXA_BASE_VAULT_ADDRESS].short_description
    assert spxa_description == CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY[SPXA_BASE_VAULT_ADDRESS].description
    assert spxa_flags == frozenset({VaultFlag.tokenised_fund})
    assert despxa_short == CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY[DESPXA_BASE_VAULT_ADDRESS].short_description
    assert despxa_description == CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY[DESPXA_BASE_VAULT_ADDRESS].description
    assert despxa_flags == frozenset()


def test_migrate_centrifuge_spxa_metadata_updates_only_target_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The write migration persists reviewed descriptions and current flags."""

    migration = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    create_vault_database(migration).write(vault_db_path)

    result = migration.migrate_centrifuge_spxa_metadata(vault_db_path, dry_run=False)
    captured = capsys.readouterr()

    assert result.inspected_rows == len(migration.CENTRIFUGE_SPXA_METADATA_SPECS) + 1
    assert {update.spec for update in result.updates} == set(migration.CENTRIFUGE_SPXA_METADATA_SPECS)
    assert "new short description" in captured.out
    assert (tmp_path / "vault-metadata-db.pickle.bak-centrifuge-spxa-metadata").exists()

    migrated_db = VaultDatabase.read(vault_db_path)
    spxa_spec = VaultSpec(migration.BASE_CHAIN_ID, SPXA_BASE_VAULT_ADDRESS)
    despxa_spec = VaultSpec(migration.BASE_CHAIN_ID, DESPXA_BASE_VAULT_ADDRESS)
    unrelated_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")

    assert migrated_db.rows[spxa_spec]["_short_description"] == CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY[SPXA_BASE_VAULT_ADDRESS].short_description
    assert migrated_db.rows[spxa_spec]["_description"] == CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY[SPXA_BASE_VAULT_ADDRESS].description
    assert migrated_db.rows[spxa_spec]["_flags"] == {VaultFlag.tokenised_fund}
    assert migrated_db.rows[despxa_spec]["_short_description"] == CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY[DESPXA_BASE_VAULT_ADDRESS].short_description
    assert migrated_db.rows[despxa_spec]["_description"] == CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY[DESPXA_BASE_VAULT_ADDRESS].description
    assert migrated_db.rows[despxa_spec]["_flags"] == set()
    assert migrated_db.rows[unrelated_spec]["_flags"] == {VaultFlag.illiquid}
    assert migrated_db.rows[unrelated_spec]["_description"] is None


def test_migrate_centrifuge_spxa_metadata_dry_run_does_not_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Dry run reports updates without modifying the metadata pickle."""

    migration = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    create_vault_database(migration).write(vault_db_path)

    result = migration.migrate_centrifuge_spxa_metadata(vault_db_path, dry_run=True)
    capsys.readouterr()

    assert len(result.updates) == len(migration.CENTRIFUGE_SPXA_METADATA_SPECS)
    unchanged_db = VaultDatabase.read(vault_db_path)
    for spec in migration.CENTRIFUGE_SPXA_METADATA_SPECS:
        assert unchanged_db.rows[spec]["_description"] is None
        assert unchanged_db.rows[spec]["_short_description"] is None
        assert unchanged_db.rows[spec]["_flags"] == set()
    assert not (tmp_path / "vault-metadata-db.pickle.bak-centrifuge-spxa-metadata").exists()


def test_migrate_centrifuge_spxa_metadata_is_idempotent(tmp_path: Path) -> None:
    """A cache that already contains reviewed metadata is not rewritten."""

    migration = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db = create_vault_database(migration)
    updates = migration.collect_centrifuge_spxa_metadata_updates(vault_db)
    migration.apply_centrifuge_spxa_metadata_updates(vault_db, updates)
    vault_db.write(vault_db_path)

    result = migration.migrate_centrifuge_spxa_metadata(vault_db_path, dry_run=False)

    assert result.updates == ()
    assert not (tmp_path / "vault-metadata-db.pickle.bak-centrifuge-spxa-metadata").exists()


def test_migrate_centrifuge_spxa_metadata_requires_both_target_rows(tmp_path: Path) -> None:
    """An incomplete cache cannot receive a partial targeted migration."""

    migration = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    present_spec = VaultSpec(migration.BASE_CHAIN_ID, SPXA_BASE_VAULT_ADDRESS)
    VaultDatabase(rows={present_spec: create_row("Janus Henderson Anemoy S&P500 Fund")}).write(vault_db_path)

    with pytest.raises(KeyError, match="Expected Centrifuge SPXA metadata rows are missing"):
        migration.migrate_centrifuge_spxa_metadata(vault_db_path, dry_run=False)

    assert VaultDatabase.read(vault_db_path).rows[present_spec]["_description"] is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, True), ("true", True), ("1", True), ("false", False), ("0", False)],
)
def test_parse_boolean_env(value: str | None, expected: object) -> None:
    """The write-mode switch recognises explicit conventional boolean values."""

    migration = load_migration_module()

    assert migration.parse_boolean_env(value, default=True) is expected
