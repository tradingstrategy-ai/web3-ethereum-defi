"""Tests for the cached withdrawal-period estimate migration."""

import datetime
import importlib.util
from pathlib import Path

import pytest

from eth_defi.vault.base import VaultSpec, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.vaultdb import VaultDatabase


class LegacyWithdrawalPeriod:
    """Serialise a withdrawal period with the pre-estimate three-slot state."""

    def __reduce__(self) -> tuple[object, tuple[type[WithdrawalPeriod]], list[object]]:
        """Make pickle construct the current class with an old slot sequence.

        :return:
            Pickle reducer that reproduces the persisted legacy object shape.
        """

        return (
            object.__new__,
            (WithdrawalPeriod,),
            [
                datetime.timedelta(days=1),
                datetime.timedelta(days=3),
                WithdrawalDelayType.delay,
            ],
        )


def load_migration_module():
    """Load the standalone migration script as a test module.

    :return:
        Loaded migration module.
    """

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-withdrawal-period-estimates.py"
    spec = importlib.util.spec_from_file_location("migrate_withdrawal_period_estimates", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_row(name: str, withdrawal_period: object) -> dict:
    """Create a minimal metadata row containing withdrawal timing.

    :param name:
        Persisted vault display name.
    :param withdrawal_period:
        Legacy or current structured withdrawal-period value.
    :return:
        Compatible minimal metadata row.
    """

    return {
        "Name": name,
        "Protocol": "Test protocol",
        "_withdrawal_period": withdrawal_period,
        "preserved_field": "unchanged",
    }


def test_migrate_withdrawal_period_estimates_rewrites_only_legacy_slots(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A write migration adds the nullable slot without changing timing data.

    1. Persist a database containing a three-slot legacy pickle value.
    2. Confirm the dry run detects but does not rewrite that value.
    3. Persist the migration and verify its backup and field preservation.
    """

    migration = load_migration_module()
    legacy_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    current_spec = VaultSpec(1, "0x0000000000000000000000000000000000000002")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    current_period = WithdrawalPeriod(
        min_period=None,
        max_period=None,
        delay_type=WithdrawalDelayType.delay,
        estimated_settlement=datetime.timedelta(days=2),
    )
    vault_db = VaultDatabase(
        rows={
            legacy_spec: create_row("Legacy vault", LegacyWithdrawalPeriod()),
            current_spec: create_row("Current vault", current_period),
        }
    )
    vault_db.write(vault_db_path)

    # 1. Reading the historical pickle yields a current class lacking its new slot.
    loaded_legacy_period = VaultDatabase.read(vault_db_path).rows[legacy_spec]["_withdrawal_period"]
    assert isinstance(loaded_legacy_period, WithdrawalPeriod)
    with pytest.raises(AttributeError):
        _ = loaded_legacy_period.estimated_settlement

    # 2. Dry run selects only the legacy object and leaves the pickle untouched.
    original_contents = vault_db_path.read_bytes()
    dry_run_result = migration.migrate_withdrawal_period_estimates(vault_db_path, dry_run=True)
    assert dry_run_result.inspected_rows == len(vault_db.rows)
    assert dry_run_result.structured_period_rows == len(vault_db.rows)
    assert [update.spec for update in dry_run_result.updates] == [legacy_spec]
    assert vault_db_path.read_bytes() == original_contents
    assert not (tmp_path / "vault-metadata-db.pickle.bak-withdrawal-period-estimates").exists()
    assert "Legacy vault" in capsys.readouterr().out

    # 3. Writing preserves timing and unrelated metadata while adding ``None``.
    write_result = migration.migrate_withdrawal_period_estimates(vault_db_path, dry_run=False)
    assert [update.spec for update in write_result.updates] == [legacy_spec]
    assert (tmp_path / "vault-metadata-db.pickle.bak-withdrawal-period-estimates").exists()
    assert "Legacy vault" in capsys.readouterr().out
    migrated_db = VaultDatabase.read(vault_db_path)
    migrated_legacy_period = migrated_db.rows[legacy_spec]["_withdrawal_period"]
    assert isinstance(migrated_legacy_period, WithdrawalPeriod)
    assert migrated_legacy_period.min_period == datetime.timedelta(days=1)
    assert migrated_legacy_period.max_period == datetime.timedelta(days=3)
    assert migrated_legacy_period.delay_type == WithdrawalDelayType.delay
    assert migrated_legacy_period.estimated_settlement is None
    assert migrated_db.rows[legacy_spec]["preserved_field"] == "unchanged"
    assert migrated_db.rows[current_spec]["_withdrawal_period"] == current_period


def test_migrate_withdrawal_period_estimates_does_not_rewrite_current_rows(tmp_path: Path) -> None:
    """A current metadata pickle does not receive a backup or a needless write."""

    migration = load_migration_module()
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(
        rows={
            spec: create_row(
                "Current vault",
                WithdrawalPeriod(
                    min_period=datetime.timedelta(0),
                    max_period=datetime.timedelta(0),
                    delay_type=WithdrawalDelayType.instant,
                ),
            )
        }
    ).write(vault_db_path)
    original_contents = vault_db_path.read_bytes()

    result = migration.migrate_withdrawal_period_estimates(vault_db_path, dry_run=False)

    assert result.inspected_rows == 1
    assert result.structured_period_rows == 1
    assert not result.updates
    assert vault_db_path.read_bytes() == original_contents
    assert not (tmp_path / "vault-metadata-db.pickle.bak-withdrawal-period-estimates").exists()
