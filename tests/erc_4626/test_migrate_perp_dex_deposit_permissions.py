"""Tests for the native perp DEX deposit-permission migration."""

import datetime
import importlib.util
from contextlib import closing
from pathlib import Path
from types import ModuleType

import duckdb
import pytest

from eth_defi.apex.vault_data_export import create_apex_vault_row
from eth_defi.grvt.vault_data_export import create_grvt_vault_row
from eth_defi.hibachi.vault_data_export import create_hibachi_vault_row
from eth_defi.hyperliquid.vault_data_export import LEADER_FRACTION_DEPOSIT_WARNING, create_hyperliquid_vault_row
from eth_defi.lighter.vault_data_export import create_lighter_pool_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow


def load_migration_module() -> ModuleType:
    """Load the perp DEX migration script as a test module.

    :return:
        Loaded migration module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-perp-dex-deposit-permissions.py"
    spec = importlib.util.spec_from_file_location("migrate_perp_dex_deposit_permissions", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_stale(row: VaultRow) -> VaultRow:
    """Replace current access metadata with the legacy public classification.

    :param row:
        Fresh protocol export row.
    :return:
        Copied row containing stale access fields.
    """
    stale_row = row.copy()
    stale_row["_deposit_permission"] = "permissionless"
    stale_row["_whitelist_notes"] = None
    stale_row["_deposit_closed_reason"] = None
    return stale_row


def create_source_databases(tmp_path: Path) -> dict[str, Path]:
    """Create legacy-compatible native perp DEX source fixtures.

    The fixtures contain only columns read by the migration. GRVT deliberately
    stores its access fields inside JSON to exercise migration of an older
    source schema.

    :param tmp_path:
        Temporary directory for DuckDB files.
    :return:
        Source database paths keyed by protocol slug.
    """
    paths = {
        "hyperliquid": tmp_path / "hyperliquid.duckdb",
        "lighter": tmp_path / "lighter.duckdb",
        "grvt": tmp_path / "grvt.duckdb",
        "apex": tmp_path / "apex.duckdb",
    }

    with closing(duckdb.connect(str(paths["hyperliquid"]))) as connection:
        connection.execute("CREATE TABLE vault_metadata (vault_address VARCHAR, is_closed BOOLEAN, allow_deposits BOOLEAN)")
        connection.execute("INSERT INTO vault_metadata VALUES (?, ?, ?)", ["0x0000000000000000000000000000000000000001", False, False])

    with closing(duckdb.connect(str(paths["lighter"]))) as connection:
        connection.execute("CREATE TABLE pool_metadata (account_index BIGINT, status INTEGER)")
        connection.execute("INSERT INTO pool_metadata VALUES (?, ?)", [2, 1])

    with closing(duckdb.connect(str(paths["grvt"]))) as connection:
        connection.execute("CREATE TABLE vault_metadata (vault_id VARCHAR, chain_vault_id INTEGER, extended_vault_info VARCHAR)")
        connection.execute("INSERT INTO vault_metadata VALUES (?, ?, ?)", ["VLT:closed", 3, '{"discoverable": false, "status": "active"}'])

    first_seen = datetime.datetime(2026, 8, 1, 12, 0)  # noqa: DTZ001 - Repository convention is naive UTC.
    with closing(duckdb.connect(str(paths["apex"]))) as connection:
        connection.execute("CREATE TABLE vault_metadata (vault_id VARCHAR, status VARCHAR, first_seen TIMESTAMP)")
        connection.execute("INSERT INTO vault_metadata VALUES (?, ?, ?)", ["closed", "VAULT_FINISHED", first_seen])

    return paths


def create_stale_vault_database(path: Path) -> dict[VaultSpec, VaultRow]:
    """Create stale native perp metadata matching the source fixtures.

    :param path:
        Target shared metadata pickle.
    :return:
        Original rows keyed by vault identity.
    """
    first_seen = datetime.datetime(2026, 8, 1, 12, 0)  # noqa: DTZ001 - Repository convention is naive UTC.
    rows = dict(
        [
            create_hyperliquid_vault_row(
                vault_address="0x0000000000000000000000000000000000000001",
                name="Closed Hyperliquid",
                description=None,
                tvl=1.0,
                create_time=first_seen,
                is_closed=False,
                allow_deposits=False,
            ),
            create_lighter_pool_row(
                account_index=2,
                name="Closed Lighter",
                description=None,
                tvl=1.0,
                created_at=first_seen,
                status=1,
            ),
            create_grvt_vault_row(
                vault_id="VLT:closed",
                chain_vault_id=3,
                name="Closed GRVT",
                description=None,
                tvl=1.0,
                discoverable=False,
                status="active",
            ),
            create_apex_vault_row(
                vault_id="closed",
                name="Closed ApeX",
                description=None,
                tvl=1.0,
                share_count=1.0,
                created_at=first_seen,
                first_seen=first_seen,
                status="VAULT_FINISHED",
            ),
            create_hibachi_vault_row(
                vault_id=4,
                symbol="HIB",
                name="Unknown Hibachi",
                description=None,
                tvl=1.0,
            ),
        ]
    )
    stale_rows = {spec: make_stale(row) for spec, row in rows.items()}
    VaultDatabase(rows=stale_rows).write(path)
    return stale_rows


def test_migrate_perp_dex_permissions_dry_run_apply_and_idempotency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """All native perp permissions migrate without altering source databases.

    1. Create stale shared metadata and legacy-shaped read-only sources.
    2. Confirm dry run reports every correction without writing any file.
    3. Apply only access fields with a backup, then confirm idempotency.

    :param tmp_path:
        Temporary directory supplied by pytest.
    :param monkeypatch:
        Environment isolation supplied by pytest.
    """
    migration = load_migration_module()
    monkeypatch.delenv("BACKUP_PATH", raising=False)
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    original_rows = create_stale_vault_database(vault_db_path)
    source_path_map = create_source_databases(tmp_path)
    source_paths = migration.PerpDexSourcePaths(**source_path_map)
    original_vault_bytes = vault_db_path.read_bytes()
    original_source_bytes = {slug: path.read_bytes() for slug, path in source_path_map.items()}
    expected_updates = len(original_rows)

    dry_run_result = migration.migrate_perp_dex_deposit_permissions(vault_db_path, source_paths, dry_run=True)

    assert dry_run_result.inspected_rows == expected_updates
    assert len(dry_run_result.updates) == expected_updates
    assert dry_run_result.unresolved_rows == 0
    assert dry_run_result.backup_path is None
    assert vault_db_path.read_bytes() == original_vault_bytes
    assert {slug: path.read_bytes() for slug, path in source_path_map.items()} == original_source_bytes

    apply_result = migration.migrate_perp_dex_deposit_permissions(vault_db_path, source_paths, dry_run=False)

    assert len(apply_result.updates) == expected_updates
    assert apply_result.backup_path is not None
    assert apply_result.backup_path.read_bytes() == original_vault_bytes
    migrated_rows = VaultDatabase.read(vault_db_path).rows
    for spec, original_row in original_rows.items():
        migrated_row = migrated_rows[spec]
        expected_permission = "unknown" if original_row["Protocol"] == "Hibachi" else "whitelisted"
        assert migrated_row["_deposit_permission"] == expected_permission
        assert (migrated_row["_whitelist_notes"] is None) == (expected_permission == "unknown")
        assert (migrated_row["_deposit_closed_reason"] is None) == (expected_permission == "unknown")
        migrated_unchanged = {key: value for key, value in migrated_row.items() if key not in {"_deposit_permission", "_whitelist_notes", "_deposit_closed_reason"}}
        original_unchanged = {key: value for key, value in original_row.items() if key not in {"_deposit_permission", "_whitelist_notes", "_deposit_closed_reason"}}
        assert migrated_unchanged == original_unchanged

    idempotent_result = migration.migrate_perp_dex_deposit_permissions(vault_db_path, source_paths, dry_run=True)
    assert not idempotent_result.updates
    assert idempotent_result.unresolved_rows == 0


def test_migrate_perp_dex_permissions_refuses_partial_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An apply run cannot silently leave unmatched source rows stale.

    :param tmp_path:
        Temporary directory supplied by pytest.
    :param monkeypatch:
        Environment isolation supplied by pytest.
    """
    migration = load_migration_module()
    monkeypatch.delenv("BACKUP_PATH", raising=False)
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    create_stale_vault_database(vault_db_path)
    source_path_map = create_source_databases(tmp_path)
    with closing(duckdb.connect(str(source_path_map["lighter"]))) as connection:
        connection.execute("DELETE FROM pool_metadata")
    source_paths = migration.PerpDexSourcePaths(**source_path_map)
    original_vault_bytes = vault_db_path.read_bytes()

    dry_run_result = migration.migrate_perp_dex_deposit_permissions(vault_db_path, source_paths, dry_run=True)

    assert dry_run_result.unresolved_rows == 1
    with pytest.raises(RuntimeError, match="Refusing partial migration with 1 unresolved"):
        migration.migrate_perp_dex_deposit_permissions(vault_db_path, source_paths, dry_run=False)
    assert vault_db_path.read_bytes() == original_vault_bytes
    assert not tuple(tmp_path.glob("*.before-perp-dex-deposit-permission-migration*"))


def test_migrate_perp_dex_permissions_preserves_hyperliquid_leader_warning(tmp_path: Path) -> None:
    """Metadata-only repair retains a warning absent from the source table.

    :param tmp_path:
        Temporary directory supplied by pytest.
    """
    migration = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    source_path_map = create_source_databases(tmp_path)
    with closing(duckdb.connect(str(source_path_map["hyperliquid"]))) as connection:
        connection.execute("INSERT INTO vault_metadata VALUES (?, ?, ?)", ["0x0000000000000000000000000000000000000002", False, True])

    spec, row = create_hyperliquid_vault_row(
        vault_address="0x0000000000000000000000000000000000000002",
        name="Leader warning",
        description=None,
        tvl=1.0,
        create_time=datetime.datetime(2026, 8, 1, 12, 0),  # noqa: DTZ001 - Repository convention is naive UTC.
        leader_fraction=0.05,
    )
    assert row["_deposit_closed_reason"] == LEADER_FRACTION_DEPOSIT_WARNING
    VaultDatabase(rows={spec: row}).write(vault_db_path)
    source_paths = migration.PerpDexSourcePaths(**source_path_map)

    result = migration.migrate_perp_dex_deposit_permissions(vault_db_path, source_paths, dry_run=True)

    assert not result.updates
    assert result.unresolved_rows == 0
