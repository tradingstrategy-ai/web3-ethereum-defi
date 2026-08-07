"""Add missing settlement estimates to cached withdrawal-period metadata.

``WithdrawalPeriod`` gained the optional ``estimated_settlement`` field after
some vault metadata pickles had already been written. Because the dataclass is
slotted, an older pickled instance unpickles without that slot. Reading its new
field then raises :class:`AttributeError` during lifetime-metrics export.

This metadata-only migration replaces only those legacy instances with the
current dataclass shape, setting ``estimated_settlement`` to ``None``. It does
not infer a settlement estimate, alter binding withdrawal timing, scan prices,
change reader state, or contact any network service.

Usage:

.. code-block:: shell

    # Inspect legacy records without writing (the default)
    source .local-test.env && \\
        poetry run python scripts/erc-4626/migrate-withdrawal-period-estimates.py

    # Back up and persist the compatible metadata pickle
    source .local-test.env && \\
        DRY_RUN=false \\
        poetry run python scripts/erc-4626/migrate-withdrawal-period-estimates.py

Environment variables:

- ``VAULT_DB_PATH``: Optional path to ``vault-metadata-db.pickle``. Falls
  back to ``VAULT_DB`` and then the normal pipeline location.
- ``DRY_RUN``: Set to ``false`` to write. Defaults to ``true``.
- ``LOG_LEVEL``: Optional console log level. Defaults to ``info``.
"""

import logging
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec, WithdrawalPeriod
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

_MISSING_SETTLEMENT = object()


@dataclass(slots=True, frozen=True)
class WithdrawalPeriodEstimateUpdate:
    """One legacy withdrawal-period object requiring the new nullable field."""

    #: Chain and vault address identifying the cached metadata row.
    spec: VaultSpec

    #: Cached human-readable vault name.
    name: str

    #: Cached protocol label.
    protocol: str


@dataclass(slots=True, frozen=True)
class WithdrawalPeriodEstimateMigrationResult:
    """Summarise the compatible metadata updates selected by the migration."""

    #: Total metadata rows inspected.
    inspected_rows: int

    #: Rows containing a structured withdrawal period.
    structured_period_rows: int

    #: Rows that were or would be rewritten with ``estimated_settlement=None``.
    updates: tuple[WithdrawalPeriodEstimateUpdate, ...]


def parse_boolean_env(value: str | None, *, default: bool) -> bool:
    """Parse a conventional environment boolean without an unsafe default.

    :param value:
        Environment value, or ``None`` when unset.
    :param default:
        Value to use when ``value`` is unset.
    :return:
        Parsed boolean value.
    """

    if value is None:
        return default

    normalised_value = value.strip().lower()
    if normalised_value in {"1", "true", "yes", "on"}:
        return True
    if normalised_value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean environment value, got {value!r}")


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose an unused sibling path for a metadata-pickle backup.

    :param vault_db_path:
        Existing metadata pickle to protect before writing.
    :return:
        Non-existing sibling backup path.
    """

    backup_path = vault_db_path.with_suffix(".pickle.bak-withdrawal-period-estimates")
    if not backup_path.exists():
        return backup_path

    backup_index = 1
    while True:
        indexed_backup_path = Path(f"{backup_path}.{backup_index}")
        if not indexed_backup_path.exists():
            return indexed_backup_path
        backup_index += 1


def collect_withdrawal_period_estimate_updates(vault_db: VaultDatabase) -> WithdrawalPeriodEstimateMigrationResult:
    """Find cached periods serialised before ``estimated_settlement`` existed.

    :param vault_db:
        Loaded vault metadata cache, including old slotted dataclass instances.
    :return:
        Counts and metadata rows needing a compatible replacement.
    """

    updates: list[WithdrawalPeriodEstimateUpdate] = []
    structured_period_rows = 0
    for spec, row in vault_db.rows.items():
        withdrawal_period = row.get("_withdrawal_period")
        if not isinstance(withdrawal_period, WithdrawalPeriod):
            continue

        structured_period_rows += 1
        if getattr(withdrawal_period, "estimated_settlement", _MISSING_SETTLEMENT) is not _MISSING_SETTLEMENT:
            continue

        updates.append(
            WithdrawalPeriodEstimateUpdate(
                spec=spec,
                name=str(row.get("Name", "")),
                protocol=str(row.get("Protocol", "")),
            )
        )

    return WithdrawalPeriodEstimateMigrationResult(
        inspected_rows=len(vault_db.rows),
        structured_period_rows=structured_period_rows,
        updates=tuple(updates),
    )


def apply_withdrawal_period_estimate_updates(vault_db: VaultDatabase, updates: Iterable[WithdrawalPeriodEstimateUpdate]) -> None:
    """Replace legacy periods while preserving their binding timing metadata.

    :param vault_db:
        Loaded metadata cache to update in memory.
    :param updates:
        Legacy withdrawal-period rows returned by the collection pass.
    :return:
        None after replacing only the selected row values.
    """

    for update in updates:
        row = vault_db.rows[update.spec].copy()
        withdrawal_period = row["_withdrawal_period"]
        assert isinstance(withdrawal_period, WithdrawalPeriod)
        row["_withdrawal_period"] = WithdrawalPeriod(
            min_period=withdrawal_period.min_period,
            max_period=withdrawal_period.max_period,
            delay_type=withdrawal_period.delay_type,
            estimated_settlement=None,
        )
        vault_db.rows[update.spec] = row


def migrate_withdrawal_period_estimates(vault_db_path: Path = DEFAULT_VAULT_DATABASE, *, dry_run: bool) -> WithdrawalPeriodEstimateMigrationResult:
    """Migrate legacy withdrawal-period slots without any onchain reads.

    A backup is made only when a non-dry run has at least one compatible
    replacement to persist. The database writer performs an atomic replacement
    after the original file has been copied.

    :param vault_db_path:
        Persisted vault metadata pickle to inspect or rewrite.
    :param dry_run:
        Report required changes without writing when ``True``.
    :return:
        Counts and rows that were or would be updated.
    """

    vault_db = VaultDatabase.read(vault_db_path)
    result = collect_withdrawal_period_estimate_updates(vault_db)

    if result.updates:
        print(
            tabulate(
                [[update.spec.chain_id, update.spec.vault_address, update.name, update.protocol] for update in result.updates],
                headers=["chain", "address", "name", "protocol"],
                tablefmt="simple",
            )
        )

    if dry_run:
        logger.info("DRY RUN: would update %d legacy withdrawal periods in %s", len(result.updates), vault_db_path)
        return result

    if not result.updates:
        logger.info("No legacy withdrawal periods need migration in %s", vault_db_path)
        return result

    backup_path = create_backup_path(vault_db_path)
    logger.info("Creating vault metadata backup at %s", backup_path)
    shutil.copy2(vault_db_path, backup_path)
    apply_withdrawal_period_estimate_updates(vault_db, result.updates)
    vault_db.write(vault_db_path)
    logger.info("Updated %d legacy withdrawal periods in %s", len(result.updates), vault_db_path)
    return result


def main() -> None:
    """Run the metadata migration using environment configuration.

    :return:
        None. Raises when the requested metadata pickle is unavailable.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", os.environ.get("VAULT_DB", str(DEFAULT_VAULT_DATABASE)))).expanduser()
    dry_run = parse_boolean_env(os.environ.get("DRY_RUN"), default=True)
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault database not found: {vault_db_path}")

    result = migrate_withdrawal_period_estimates(vault_db_path, dry_run=dry_run)
    print(f"Inspected {result.inspected_rows:,} rows, found {result.structured_period_rows:,} structured periods, and updated {len(result.updates):,} legacy periods.")
    if dry_run:
        print("Dry run - no changes written.")
    elif result.updates:
        print(f"Saved migrated vault metadata to {vault_db_path}")


if __name__ == "__main__":
    main()
