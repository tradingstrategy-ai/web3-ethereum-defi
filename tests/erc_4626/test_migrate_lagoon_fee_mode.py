"""Tests for the persisted Lagoon fee-mode migration."""

import importlib.util
from pathlib import Path

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode, get_vault_fee_mode
from eth_defi.vault.vaultdb import VaultDatabase

#: Representative persisted Lagoon management fee.
MANAGEMENT_FEE = 0.01

#: Representative persisted Lagoon performance fee.
PERFORMANCE_FEE = 0.20

#: Number of fixture rows inspected by the migration.
EXPECTED_INSPECTED_ROWS = 3


def load_migration_module():
    """Load the migration script as a Python module.

    :return:
        Loaded migration module.
    """

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-lagoon-fee-mode.py"
    spec = importlib.util.spec_from_file_location("migrate_lagoon_fee_mode", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_fee_data(fee_mode: VaultFeeMode | None) -> FeeData:
    """Create representative Lagoon fee data.

    :param fee_mode:
        Fee-accounting mode to persist.
    :return:
        Valid structured fee data.
    """

    return FeeData(
        fee_mode=fee_mode,
        management=MANAGEMENT_FEE,
        performance=PERFORMANCE_FEE,
        deposit=0.0,
        withdraw=0.0,
    )


def test_fee_matrix_uses_canonical_lagoon_protocol_name() -> None:
    """Canonical and historical Lagoon names resolve externalised fees."""

    fee_mode = get_vault_fee_mode("Lagoon Finance", "0x0000000000000000000000000000000000000001")
    legacy_fee_mode = get_vault_fee_mode("Lagoon", "0x0000000000000000000000000000000000000001")

    assert fee_mode == VaultFeeMode.externalised
    assert legacy_fee_mode == VaultFeeMode.externalised


def test_migrate_lagoon_fee_mode_updates_only_stale_lagoon_rows(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Migration persists the fee mode without changing fee values or other protocols."""

    migration = load_migration_module()
    lagoon_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    current_lagoon_spec = VaultSpec(1, "0x0000000000000000000000000000000000000002")
    morpho_spec = VaultSpec(1, "0x0000000000000000000000000000000000000003")
    vault_db = VaultDatabase(
        rows={
            lagoon_spec: {
                "Name": "Stale Lagoon vault",
                "Protocol": "Lagoon Finance",
                "Mgmt fee": MANAGEMENT_FEE,
                "Perf fee": PERFORMANCE_FEE,
                "_fees": create_fee_data(None),
            },
            current_lagoon_spec: {
                "Name": "Current Lagoon vault",
                "Protocol": "Lagoon Finance",
                "_fees": create_fee_data(VaultFeeMode.externalised),
            },
            morpho_spec: {
                "Name": "Morpho vault",
                "Protocol": "Morpho",
                "_fees": create_fee_data(None),
            },
        }
    )
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)

    result = migration.migrate_lagoon_fee_mode(vault_db_path, dry_run=False)
    captured = capsys.readouterr()

    assert result.inspected_rows == EXPECTED_INSPECTED_ROWS
    assert result.migrated_rows == 1
    assert result.missing_fee_data_rows == 0
    assert "Stale Lagoon vault" in captured.out

    migrated_db = VaultDatabase.read(vault_db_path)
    migrated_fees = migrated_db.rows[lagoon_spec]["_fees"]
    assert migrated_fees.fee_mode == VaultFeeMode.externalised
    assert migrated_fees.management == MANAGEMENT_FEE
    assert migrated_fees.performance == PERFORMANCE_FEE
    assert migrated_db.rows[current_lagoon_spec]["_fees"].fee_mode == VaultFeeMode.externalised
    assert migrated_db.rows[morpho_spec]["_fees"].fee_mode is None
    assert (tmp_path / "vault-metadata-db.pickle.bak-lagoon-fee-mode").exists()


def test_migrate_lagoon_fee_mode_dry_run_does_not_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Dry run reports stale Lagoon fee data without changing the pickle."""

    migration = load_migration_module()
    lagoon_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    vault_db = VaultDatabase(
        rows={
            lagoon_spec: {
                "Name": "Stale Lagoon vault",
                "Protocol": "Lagoon Finance",
                "_fees": create_fee_data(None),
            },
        }
    )
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)

    result = migration.migrate_lagoon_fee_mode(vault_db_path, dry_run=True)
    captured = capsys.readouterr()

    assert result.migrated_rows == 1
    assert "Stale Lagoon vault" in captured.out
    unchanged_db = VaultDatabase.read(vault_db_path)
    assert unchanged_db.rows[lagoon_spec]["_fees"].fee_mode is None
    assert not (tmp_path / "vault-metadata-db.pickle.bak-lagoon-fee-mode").exists()


def test_create_backup_path_keeps_existing_backup(tmp_path: Path) -> None:
    """Migration never overwrites an existing metadata backup."""

    migration = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    backup_path = tmp_path / "vault-metadata-db.pickle.bak-lagoon-fee-mode"
    vault_db_path.touch()
    backup_path.touch()

    assert migration.create_backup_path(vault_db_path) == tmp_path / "vault-metadata-db.pickle.bak-lagoon-fee-mode.1"
