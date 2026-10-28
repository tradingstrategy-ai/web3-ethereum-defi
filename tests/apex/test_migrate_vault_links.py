"""Regression coverage for the metadata-only ApeX link migration."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from eth_defi.apex.constants import APEX_CHAIN_ID
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

USER_SPEC = VaultSpec(APEX_CHAIN_ID, "apex-vault-2099816991878676480")
OFFICIAL_SPEC = VaultSpec(APEX_CHAIN_ID, "apex-vault-10000")
OTHER_SPEC = VaultSpec(1, "0x1111111111111111111111111111111111111111")
CORRECT_USER_LINK = "https://omni.apex.exchange/vaultInfo/2099816991878676480"
EXPECTED_APEX_ROWS = 2


@pytest.fixture()
def migration() -> ModuleType:
    """Load the operator script without running its entry point.

    Keeping the script importable exercises the same migration implementation
    that operators run from the terminal.

    :return:
        Loaded migration script module.
    """
    script_path = Path(__file__).parents[2] / "scripts" / "apex" / "migrate-vault-links.py"
    spec = importlib.util.spec_from_file_location("migrate_apex_vault_links", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def vault_db_path(tmp_path: Path) -> Path:
    """Create a file-backed metadata database with one broken user link.

    Official and unrelated rows have correct links and additional enrichment
    fields so the migration can demonstrate preservation of existing data.

    :param tmp_path:
        Isolated temporary directory.
    :return:
        Shared metadata pickle containing the regression case.
    """
    path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(
        rows={
            USER_SPEC: {"Name": "AI multistrategy for Apex", "Link": f"{CORRECT_USER_LINK}/1", "_description": "Retain this description"},
            OFFICIAL_SPEC: {"Name": "Protocol Vault", "Link": "https://omni.apex.exchange/vaultInfo/10000/1"},
            OTHER_SPEC: {"Name": "Other vault", "Link": "https://example.com/other"},
        },
        last_scanned_block={1: 12345678},
    ).write(path)
    return path


def test_apex_link_migration_preview_apply_and_rerun(migration: ModuleType, vault_db_path: Path) -> None:
    """Preserve metadata and backup bytes across dry run, write and rerun.

    Only the broken user link changes. Already-correct official and unrelated
    rows, scanner progress and the pre-migration backup remain intact.

    :param migration:
        Operator script module.
    :param vault_db_path:
        File-backed regression database.
    """
    original_bytes = vault_db_path.read_bytes()
    original_db = VaultDatabase.read(vault_db_path)
    preview = migration.migrate_apex_vault_links(vault_db_path, dry_run=True)
    assert preview.inspected_rows == EXPECTED_APEX_ROWS
    assert preview.updated_rows == 1
    assert preview.backup_path is None
    assert vault_db_path.read_bytes() == original_bytes
    assert not list(vault_db_path.parent.glob("*.before-apex-link-migration*"))

    result = migration.migrate_apex_vault_links(vault_db_path, dry_run=False)
    assert result.inspected_rows == EXPECTED_APEX_ROWS
    assert result.updated_rows == 1
    assert result.backup_path.read_bytes() == original_bytes
    original_db.rows[USER_SPEC]["Link"] = CORRECT_USER_LINK
    assert VaultDatabase.read(vault_db_path) == original_db

    migrated_bytes = vault_db_path.read_bytes()
    rerun = migration.migrate_apex_vault_links(vault_db_path, dry_run=False)
    assert rerun.updated_rows == 0
    assert rerun.backup_path is None
    assert vault_db_path.read_bytes() == migrated_bytes
    assert len(list(vault_db_path.parent.glob("*.before-apex-link-migration*"))) == 1


@pytest.mark.parametrize("invalid_address", ["0x2222222222222222222222222222222222222222", "apex-vault-", "apex-vault-abc"])
def test_apex_link_migration_rejects_invalid_identity(migration: ModuleType, vault_db_path: Path, invalid_address: str) -> None:
    """Abort before saving any changes if a later ApeX identity is malformed.

    The valid broken row precedes the invalid row to catch partial migrations.

    :param migration:
        Operator script module.
    :param vault_db_path:
        File-backed regression database.
    :param invalid_address:
        Malformed synthetic ApeX identity.
    """
    database = VaultDatabase.read(vault_db_path)
    database.rows[VaultSpec(APEX_CHAIN_ID, invalid_address)] = {"Name": "Malformed"}
    database.write(vault_db_path)
    original_bytes = vault_db_path.read_bytes()
    with pytest.raises(ValueError, match="Invalid ApeX vault identity"):
        migration.migrate_apex_vault_links(vault_db_path, dry_run=False)
    assert vault_db_path.read_bytes() == original_bytes
    assert not list(vault_db_path.parent.glob("*.before-apex-link-migration*"))


def test_apex_link_migration_preserves_existing_backup(migration: ModuleType, vault_db_path: Path) -> None:
    """Keep an earlier backup when another repair is required.

    Numeric suffixes provide a fresh backup path without overwriting an
    operator's earlier recovery point.

    :param migration:
        Operator script module.
    :param vault_db_path:
        File-backed regression database.
    """
    prior_backup = vault_db_path.with_name(f"{vault_db_path.name}.before-apex-link-migration")
    prior_backup.write_bytes(b"Earlier backup")
    original_bytes = vault_db_path.read_bytes()
    result = migration.migrate_apex_vault_links(vault_db_path, dry_run=False)
    assert result.backup_path != prior_backup
    assert result.backup_path.read_bytes() == original_bytes
    assert prior_backup.read_bytes() == b"Earlier backup"


def test_apex_link_migration_main_defaults_to_preview(migration: ModuleType, vault_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the pipeline directory and keep CLI writes explicitly opt-in.

    The default invocation leaves the pickle byte-for-byte unchanged and an
    unrecognised boolean aborts rather than accidentally enabling writes.

    :param migration:
        Operator script module.
    :param vault_db_path:
        File-backed regression database.
    :param monkeypatch:
        Isolated environment configuration.
    """
    monkeypatch.delenv("VAULT_DB", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(vault_db_path.parent))
    original_bytes = vault_db_path.read_bytes()
    migration.main()
    assert vault_db_path.read_bytes() == original_bytes
    monkeypatch.setenv("DRY_RUN", "maybe")
    with pytest.raises(ValueError, match="DRY_RUN must be true or false"):
        migration.main()
    assert vault_db_path.read_bytes() == original_bytes
