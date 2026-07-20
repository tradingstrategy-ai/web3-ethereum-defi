"""Regression tests for the Hyperliquid cached fee-mode migration helper."""

import importlib.util
from pathlib import Path

from eth_defi.hyperliquid.constants import (
    HYPERCORE_CHAIN_ID,
    HYPERLIQUID_VAULT_PERFORMANCE_FEE,
)
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.vaultdb import VaultDatabase


def load_migration_module():
    """Load the hyphenated migration script as a Python module.

    :return:
        Loaded migration module.
    """

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "hyperliquid" / "migrate-vault-fee-mode.py"
    spec = importlib.util.spec_from_file_location(
        "migrate_hyperliquid_vault_fee_mode",
        script_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_row(protocol: str, fee_mode: VaultFeeMode) -> dict:
    """Create minimal metadata with a configurable fee mode.

    :param protocol:
        Protocol name persisted in the metadata row.
    :param fee_mode:
        Existing fee mode to migrate or preserve.
    :return:
        Minimal vault metadata row.
    """

    return {
        "Name": "Test vault",
        "Protocol": protocol,
        "Mgmt fee": 0.0,
        "Perf fee": 0.1,
        "Deposit fee": 0.0,
        "Withdraw fee": 0.0,
        "_fees": FeeData(
            fee_mode=fee_mode,
            management=0.0,
            performance=0.1,
            deposit=0.0,
            withdraw=0.0,
        ),
    }


def test_migrate_hyperliquid_fee_mode_updates_only_hypercore_metadata(
    tmp_path: Path,
) -> None:
    """Migrate Hypercore rows while preserving all unrelated vault metadata."""
    migration = load_migration_module()
    hyperliquid_spec = VaultSpec(
        HYPERCORE_CHAIN_ID,
        "0x1111111111111111111111111111111111111111",
    )
    evm_spec = VaultSpec(1, "0x2222222222222222222222222222222222222222")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(
        rows={
            hyperliquid_spec: create_row(
                "Hyperliquid",
                VaultFeeMode.internalised_skimming,
            ),
            evm_spec: create_row("Yearn", VaultFeeMode.internalised_skimming),
        }
    ).write(vault_db_path)

    dry_run = migration.migrate_hyperliquid_fee_mode(vault_db_path, dry_run=True)
    assert dry_run.inspected_rows == 1
    assert dry_run.migrated_rows == 1
    assert dry_run.backup_path is None
    dry_run_db = VaultDatabase.read(vault_db_path)
    assert dry_run_db.rows[hyperliquid_spec]["_fees"].fee_mode == VaultFeeMode.internalised_skimming

    result = migration.migrate_hyperliquid_fee_mode(vault_db_path, dry_run=False)
    assert result.inspected_rows == 1
    assert result.migrated_rows == 1
    assert result.backup_path is not None
    assert result.backup_path.exists()

    migrated_db = VaultDatabase.read(vault_db_path)
    assert migrated_db.rows[hyperliquid_spec]["_fees"].fee_mode == VaultFeeMode.externalised
    assert migrated_db.rows[hyperliquid_spec]["_fees"].performance == HYPERLIQUID_VAULT_PERFORMANCE_FEE
    assert migrated_db.rows[evm_spec]["_fees"].fee_mode == VaultFeeMode.internalised_skimming


def test_hyperliquid_fee_mode_migration_is_idempotent(tmp_path: Path) -> None:
    """Already migrated metadata does not create a backup or rewrite the pickle."""
    migration = load_migration_module()
    hyperliquid_spec = VaultSpec(
        HYPERCORE_CHAIN_ID,
        "0x1111111111111111111111111111111111111111",
    )
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db = VaultDatabase(
        rows={
            hyperliquid_spec: create_row(
                "Hyperliquid",
                VaultFeeMode.externalised,
            )
        }
    )
    vault_db.write(vault_db_path)

    result = migration.migrate_hyperliquid_fee_mode(vault_db_path, dry_run=False)

    assert result.inspected_rows == 1
    assert result.migrated_rows == 0
    assert result.backup_path is None


def test_create_backup_path_never_overwrites_existing_backup(tmp_path: Path) -> None:
    """Increment the backup suffix when a previous migration backup exists."""
    migration = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    first_backup_path = tmp_path / "vault-metadata-db.pickle.before-hyperliquid-fee-mode-migration"
    first_backup_path.touch()

    expected_backup_path = tmp_path / "vault-metadata-db.pickle.before-hyperliquid-fee-mode-migration.1"
    assert migration.create_backup_path(vault_db_path) == expected_backup_path
