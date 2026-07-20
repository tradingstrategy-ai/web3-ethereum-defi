"""Migrate persisted Lagoon fee modes in the vault metadata database.

Older metadata rows have the correct Lagoon management and performance fee
percentages, but their :class:`~eth_defi.vault.fee.FeeData` has no fee mode.
The historical fee matrix used ``"Lagoon"`` while the scanner persists the
canonical protocol name ``"Lagoon Finance"``.  A missing fee mode prevents
fee-adjusted net-return metrics from being calculated.

This metadata-only migration sets ``VaultFeeMode.externalised`` on stale
Lagoon ``_fees`` values. It does not alter fee percentages, price Parquet
files, reader state, or any vault history rows.

Usage:

.. code-block:: shell

    # Inspect affected rows without changing the database
    source .local-test.env && DRY_RUN=true \\
        poetry run python scripts/erc-4626/migrate-lagoon-fee-mode.py

    # Create a backup and persist the repair
    source .local-test.env && DRY_RUN=false \\
        poetry run python scripts/erc-4626/migrate-lagoon-fee-mode.py

Environment variables:

- ``VAULT_DB_PATH``: Optional path to ``vault-metadata-db.pickle``. Defaults
  to the production metadata path.
- ``DRY_RUN``: Set to ``true`` to report affected rows without writing. The
  default is ``true``.
- ``LOG_LEVEL``: Optional log level. Defaults to ``info``.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Canonical scanner protocol name for Lagoon vaults.
LAGOON_PROTOCOL_NAME = "Lagoon Finance"

#: Limit stdout detail while retaining the full migration count.
MAX_MIGRATED_ROWS_SHOWN = 50


@dataclass(slots=True, frozen=True)
class LagoonFeeModeMigrationResult:
    """Summarise a Lagoon fee-mode metadata migration.

    :param inspected_rows:
        Number of metadata rows inspected.
    :param migrated_rows:
        Number of stale Lagoon fee records that were or would be updated.
    :param missing_fee_data_rows:
        Number of Lagoon rows without a structured ``_fees`` record.
    """

    inspected_rows: int
    migrated_rows: int
    missing_fee_data_rows: int


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose a non-overwriting backup path for a vault metadata pickle.

    :param vault_db_path:
        Existing metadata pickle to protect before migration.
    :return:
        A sibling path that does not already exist.
    """

    backup_path = vault_db_path.with_suffix(".pickle.bak-lagoon-fee-mode")
    if not backup_path.exists():
        return backup_path

    backup_index = 1
    while True:
        indexed_backup_path = Path(f"{backup_path}.{backup_index}")
        if not indexed_backup_path.exists():
            return indexed_backup_path
        backup_index += 1


def migrate_lagoon_fee_mode(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    *,
    dry_run: bool,
) -> LagoonFeeModeMigrationResult:
    """Set the externalised fee mode on stale Lagoon metadata records.

    The fee values are already persisted in each ``FeeData`` object. Only a
    missing fee mode is repaired, making this safe to run repeatedly and
    preventing accidental changes to any fee percentage.

    :param vault_db_path:
        Path to the metadata pickle to inspect or update.
    :param dry_run:
        When ``True``, report candidates without modifying the pickle.
    :return:
        Counts for inspected, migrated, and unsupported rows.
    """

    vault_db = VaultDatabase.read(vault_db_path)
    migrated_rows: list[tuple[int, str, VaultRow]] = []
    missing_fee_data_rows = 0

    for spec, row in vault_db.rows.items():
        if row.get("Protocol") != LAGOON_PROTOCOL_NAME:
            continue

        fee_data = row.get("_fees")
        if not isinstance(fee_data, FeeData):
            missing_fee_data_rows += 1
            continue

        if fee_data.fee_mode is not None:
            continue

        migrated_rows.append((spec.chain_id, spec.vault_address, row))
        if not dry_run:
            fee_data.fee_mode = VaultFeeMode.externalised

    if migrated_rows:
        table_rows = [[chain_id, address, row.get("Name", ""), row.get("Mgmt fee"), row.get("Perf fee")] for chain_id, address, row in migrated_rows[:MAX_MIGRATED_ROWS_SHOWN]]
        print(tabulate(table_rows, headers=["Chain", "Address", "Name", "Mgmt fee", "Perf fee"], tablefmt="simple"))
        if len(migrated_rows) > MAX_MIGRATED_ROWS_SHOWN:
            print(f"... {len(migrated_rows) - MAX_MIGRATED_ROWS_SHOWN:,} more migrated rows not shown")

    result = LagoonFeeModeMigrationResult(
        inspected_rows=len(vault_db.rows),
        migrated_rows=len(migrated_rows),
        missing_fee_data_rows=missing_fee_data_rows,
    )
    if result.migrated_rows == 0:
        logger.info("No stale Lagoon fee modes found in %s", vault_db_path)
        return result

    if dry_run:
        logger.info("DRY RUN: would migrate %d Lagoon fee modes in %s", result.migrated_rows, vault_db_path)
        return result

    backup_path = create_backup_path(vault_db_path)
    logger.info("Creating vault DB backup at %s", backup_path)
    shutil.copy2(vault_db_path, backup_path)
    vault_db.write(vault_db_path)
    logger.info("Migrated %d Lagoon fee modes in %s", result.migrated_rows, vault_db_path)
    return result


def main() -> None:
    """Run the Lagoon fee-mode metadata migration.

    Environment configuration keeps the production invocation consistent with
    the other ERC-4626 maintenance scripts.

    :return:
        ``None``. Raises if the requested metadata database is unavailable.
    """

    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=Path("logs/migrate-lagoon-fee-mode.log"),
    )
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    assert vault_db_path.exists(), f"Vault database not found: {vault_db_path}"

    result = migrate_lagoon_fee_mode(vault_db_path, dry_run=dry_run)
    print(f"Inspected {result.inspected_rows:,} rows, migrated {result.migrated_rows:,}, skipped {result.missing_fee_data_rows:,} Lagoon rows without structured fee data.")
    if dry_run:
        print("Dry run - no changes written.")
    elif result.migrated_rows:
        print(f"Saved migrated vault database to {vault_db_path}")


if __name__ == "__main__":
    main()
