#!/usr/bin/env python3
"""Migrate cached Hyperliquid vault metadata to withdrawal-time fee semantics.

Hyperliquid legacy vaults deduct the leader's 10% profit share when a depositor
withdraws. Older ``vault-metadata-db.pickle`` files incorrectly label this fee
as internalised, which makes calculated gross and net returns identical. This
metadata-only migration changes Hypercore ``FeeData.fee_mode`` to
``externalised`` and preserves all recorded fee percentages.

The script starts in dry-run mode. Inspect the proposed changes, then apply it:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/hyperliquid/migrate-vault-fee-mode.py
    source .local-test.env && DRY_RUN=false poetry run python scripts/hyperliquid/migrate-vault-fee-mode.py

After applying it, run the normal Hyperliquid scanner and price post-processing
pipeline to republish the corrected gross and net metrics. Configuration is
through environment variables:

``DRY_RUN``
    Print proposed changes without writing. Defaults to ``true``.

``VAULT_DB_PATH``
    Metadata pickle to migrate. Defaults to the active pipeline data directory.

``BACKUP_PATH``
    Optional backup pickle path. Defaults to a non-overwriting sibling path.

``LOG_LEVEL``
    Python logging level. Defaults to ``info``.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HyperliquidFeeModeMigration:
    """One cached Hyperliquid metadata row requiring a fee-mode update."""

    #: Existing metadata database key.
    vault_spec: VaultSpec

    #: Human-readable vault name for the operator output.
    name: str

    #: Fee mode persisted before this migration.
    old_fee_mode: VaultFeeMode | None

    #: Updated fee details to persist.
    new_fee_data: FeeData


@dataclass(slots=True)
class HyperliquidFeeModeMigrationResult:
    """Summary of a Hyperliquid metadata fee-mode migration."""

    #: Number of Hyperliquid metadata rows inspected.
    inspected_rows: int

    #: Number of rows requiring the fee-mode update.
    migrated_rows: int

    #: Backup written before the atomic metadata update, if any.
    backup_path: Path | None


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse a boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value to use when the variable is unset.
    :return:
        Parsed boolean value.
    """

    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def resolve_vault_database_path() -> Path:
    """Resolve the cached vault metadata database to migrate.

    :return:
        Explicit ``VAULT_DB_PATH`` or the active pipeline metadata database.
    """

    configured_path = os.environ.get("VAULT_DB_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return get_pipeline_data_dir() / "vault-metadata-db.pickle"


def create_backup_path(vault_db_path: Path) -> Path:
    """Create a non-overwriting sibling backup path.

    :param vault_db_path:
        Metadata database to back up.
    :return:
        Unused backup path next to the metadata database.
    """

    configured_path = os.environ.get("BACKUP_PATH")
    if configured_path:
        backup_path = Path(configured_path).expanduser()
        if backup_path.exists():
            raise FileExistsError(f"Refusing to overwrite existing backup: {backup_path}")
        return backup_path

    base_path = vault_db_path.with_name(f"{vault_db_path.name}.before-hyperliquid-fee-mode-migration")
    backup_path = base_path
    suffix = 1
    while backup_path.exists():
        backup_path = base_path.with_name(f"{base_path.name}.{suffix}")
        suffix += 1
    return backup_path


def _create_externalised_fee_data(row: VaultRow) -> FeeData:
    """Create externalised fee metadata while preserving stored fee percentages.

    :param row:
        Existing Hyperliquid vault metadata row.
    :return:
        Fee data with the withdrawal-time fee model.
    """

    existing_fees: FeeData | None = row.get("_fees")
    if existing_fees is None:
        return FeeData(
            fee_mode=VaultFeeMode.externalised,
            management=row.get("Mgmt fee"),
            performance=row.get("Perf fee"),
            deposit=row.get("Deposit fee", 0.0),
            withdraw=row.get("Withdraw fee", 0.0),
        )

    return FeeData(
        fee_mode=VaultFeeMode.externalised,
        management=existing_fees.management,
        performance=existing_fees.performance,
        deposit=existing_fees.deposit,
        withdraw=existing_fees.withdraw,
    )


def build_hyperliquid_fee_mode_migrations(
    vault_db: VaultDatabase,
) -> tuple[int, list[HyperliquidFeeModeMigration]]:
    """Find Hypercore metadata rows still using internalised fee semantics.

    :param vault_db:
        In-memory metadata database to inspect without mutating it.
    :return:
        Number of inspected Hyperliquid rows and required migrations.
    """

    inspected_rows = 0
    migrations: list[HyperliquidFeeModeMigration] = []

    for vault_spec, row in vault_db.rows.items():
        if vault_spec.chain_id != HYPERCORE_CHAIN_ID or row.get("Protocol") != "Hyperliquid":
            continue

        inspected_rows += 1
        existing_fees: FeeData | None = row.get("_fees")
        old_fee_mode = existing_fees.fee_mode if existing_fees else None
        if old_fee_mode == VaultFeeMode.externalised:
            continue

        migrations.append(
            HyperliquidFeeModeMigration(
                vault_spec=vault_spec,
                name=row.get("Name", "<unnamed>"),
                old_fee_mode=old_fee_mode,
                new_fee_data=_create_externalised_fee_data(row),
            )
        )

    return inspected_rows, migrations


def apply_hyperliquid_fee_mode_migrations(
    vault_db: VaultDatabase,
    migrations: list[HyperliquidFeeModeMigration],
) -> None:
    """Apply fee-mode updates without altering unrelated metadata or state.

    :param vault_db:
        In-memory metadata database to update.
    :param migrations:
        Fee-mode changes selected by :func:`build_hyperliquid_fee_mode_migrations`.
    :return:
        ``None`` after updating the in-memory rows.
    """

    for migration in migrations:
        row = vault_db.rows[migration.vault_spec].copy()
        row["_fees"] = migration.new_fee_data
        vault_db.rows[migration.vault_spec] = row


def migrate_hyperliquid_fee_mode(
    vault_db_path: Path,
    *,
    dry_run: bool,
) -> HyperliquidFeeModeMigrationResult:
    """Migrate a metadata pickle with a backup before any real write.

    :param vault_db_path:
        Existing metadata pickle to inspect and optionally update.
    :param dry_run:
        If ``True``, report required changes without writing any files.
    :return:
        Migration summary including the backup path when one was written.
    """

    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault metadata database does not exist: {vault_db_path}")

    vault_db = VaultDatabase.read(vault_db_path)
    inspected_rows, migrations = build_hyperliquid_fee_mode_migrations(vault_db)
    logger.info(
        "Inspected %d Hyperliquid metadata rows; %d require migration",
        inspected_rows,
        len(migrations),
    )

    if dry_run or not migrations:
        return HyperliquidFeeModeMigrationResult(
            inspected_rows=inspected_rows,
            migrated_rows=len(migrations),
            backup_path=None,
        )

    backup_path = create_backup_path(vault_db_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vault_db_path, backup_path)
    apply_hyperliquid_fee_mode_migrations(vault_db, migrations)
    vault_db.write(vault_db_path)

    return HyperliquidFeeModeMigrationResult(
        inspected_rows=inspected_rows,
        migrated_rows=len(migrations),
        backup_path=backup_path,
    )


def main() -> None:
    """Run the Hyperliquid metadata migration configured through environment variables.

    :return:
        ``None`` after printing the migration result.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_bool_env("DRY_RUN", default=True)
    vault_db_path = resolve_vault_database_path()
    result = migrate_hyperliquid_fee_mode(vault_db_path, dry_run=dry_run)

    print(
        tabulate(
            [
                [
                    vault_db_path,
                    result.inspected_rows,
                    result.migrated_rows,
                    "yes" if dry_run else "no",
                    result.backup_path or "-",
                ]
            ],
            headers=[
                "vault database",
                "Hyperliquid rows",
                "migrated rows",
                "dry run",
                "backup",
            ],
            tablefmt="rounded_outline",
        )
    )
    if dry_run:
        print("Dry run: no files written. Re-run with DRY_RUN=false to apply the migration.")


if __name__ == "__main__":
    main()
