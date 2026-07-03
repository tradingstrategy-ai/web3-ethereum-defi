"""Repair stale vault feature fields in the metadata database.

Older scanner rows may have ``_detection_data.features`` populated while the
top-level ``features`` field is missing or empty. Some diagnostics and exports
read the top-level field, so these rows look like they have no protocol feature
flags even though classification succeeded.

This script copies authoritative detection features to the top-level feature
field for every row in ``vault-metadata-db.pickle``. It does not touch price
Parquet files, reader state or ``_detection_data.features``.

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/repair-vault-features.py

Environment variables:

- ``VAULT_DB``: Path to the vault metadata pickle. Defaults to
  ``~/.tradingstrategy/vaults/vault-metadata-db.pickle``.
- ``DRY_RUN``: Set to ``true`` to only report without modifying files.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Maximum number of repaired rows shown on stdout.
MAX_REPAIR_ROWS_SHOWN = 50


@dataclass(slots=True, frozen=True)
class FeatureRepairResult:
    """Result of repairing vault metadata feature fields.

    :param inspected_rows:
        Number of vault metadata rows inspected.

    :param repaired_rows:
        Number of rows that were or would be updated.

    :param missing_detection_rows:
        Number of rows that did not have detection metadata and were skipped.
    """

    inspected_rows: int
    repaired_rows: int
    missing_detection_rows: int


def _normalise_feature(feature: object) -> ERC4626Feature | None:
    """Normalise a single stored feature value.

    :param feature:
        Stored feature value. Older pickles may contain either enum values or
        strings.

    :return:
        Normalised enum feature, or ``None`` if the value is unknown.
    """
    if isinstance(feature, ERC4626Feature):
        return feature

    if isinstance(feature, str):
        try:
            return ERC4626Feature(feature)
        except ValueError:
            pass

        try:
            return ERC4626Feature[feature]
        except KeyError:
            logger.warning("Skipping unknown vault feature string: %s", feature)
            return None

    logger.warning("Skipping unsupported vault feature value: %r", feature)
    return None


def _normalise_features(features: object) -> set[ERC4626Feature]:
    """Normalise a stored feature collection.

    :param features:
        Stored row feature value.

    :return:
        Feature set, or an empty set if the row does not have features.
    """
    if not features:
        return set()
    if isinstance(features, str):
        feature = _normalise_feature(features)
        return {feature} if feature is not None else set()
    normalised_features = (_normalise_feature(feature) for feature in features)
    return {feature for feature in normalised_features if feature is not None}


def _format_features(features: object) -> str:
    """Format stored features for tabular reporting.

    :param features:
        Stored feature values.

    :return:
        Comma-separated feature names.
    """
    return ", ".join(sorted(feature.value for feature in _normalise_features(features)))


def repair_row_features(row: VaultRow) -> bool:
    """Copy authoritative detection features to the top-level row field.

    :param row:
        Vault metadata row to repair in place.

    :return:
        ``True`` if the row changed.
    """
    detection = row.get("_detection_data")
    if not isinstance(detection, ERC4262VaultDetection):
        return False

    row_features = _normalise_features(row.get("features"))
    detection_features = _normalise_features(detection.features)
    if not detection_features:
        return False

    stored_features = row.get("features")
    if row_features == detection_features and stored_features == detection_features:
        return False

    row["features"] = set(detection_features)
    return True


def create_backup_path(vault_db_path: Path) -> Path:
    """Create a non-overwriting backup path for the vault database.

    :param vault_db_path:
        Path to the vault metadata pickle.

    :return:
        Backup path that does not yet exist.
    """
    backup_path = vault_db_path.with_suffix(".pickle.bak-feature-repair")
    if not backup_path.exists():
        return backup_path

    backup_index = 1
    while True:
        indexed_backup_path = Path(f"{backup_path}.{backup_index}")
        if not indexed_backup_path.exists():
            return indexed_backup_path
        backup_index += 1


def repair_vault_features(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    *,
    dry_run: bool,
) -> FeatureRepairResult:
    """Repair stale feature fields in a vault metadata database.

    :param vault_db_path:
        Path to the vault metadata pickle.

    :param dry_run:
        If ``True``, report only without modifying files.

    :return:
        Repair result counters.
    """
    db = VaultDatabase.read(vault_db_path)

    repaired_rows: list[tuple[VaultSpec, VaultRow]] = []
    missing_detection_rows = 0

    for spec, row in db.rows.items():
        detection = row.get("_detection_data")
        if not isinstance(detection, ERC4262VaultDetection):
            missing_detection_rows += 1
            continue

        changed = repair_row_features(row)
        if changed:
            repaired_rows.append((spec, row))

    if repaired_rows:
        table_rows = [
            [
                spec.chain_id,
                spec.vault_address,
                row.get("Protocol", ""),
                row.get("Name", ""),
                _format_features(row["features"]),
            ]
            for spec, row in repaired_rows[:MAX_REPAIR_ROWS_SHOWN]
        ]
        print(tabulate(table_rows, headers=["Chain", "Address", "Protocol", "Name", "Features"], tablefmt="simple"))
        if len(repaired_rows) > MAX_REPAIR_ROWS_SHOWN:
            print(f"... {len(repaired_rows) - MAX_REPAIR_ROWS_SHOWN:,} more repaired rows not shown")

    result = FeatureRepairResult(
        inspected_rows=len(db.rows),
        repaired_rows=len(repaired_rows),
        missing_detection_rows=missing_detection_rows,
    )

    if result.repaired_rows == 0:
        logger.info("No stale vault feature rows found in %s", vault_db_path)
        return result

    if dry_run:
        logger.info("DRY RUN: would repair %d vault feature rows in %s", result.repaired_rows, vault_db_path)
        return result

    backup_path = create_backup_path(vault_db_path)
    logger.info("Creating vault DB backup at %s", backup_path)
    shutil.copy2(vault_db_path, backup_path)
    db.write(vault_db_path)
    logger.info("Repaired %d vault feature rows in %s", result.repaired_rows, vault_db_path)
    return result


def main() -> None:
    """Run the feature repair script."""
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "info"),
        log_file=Path("logs/repair-vault-features.log"),
    )

    vault_db_path = Path(os.environ.get("VAULT_DB", DEFAULT_VAULT_DATABASE)).expanduser()
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    assert vault_db_path.exists(), f"Vault database not found: {vault_db_path}"

    result = repair_vault_features(vault_db_path, dry_run=dry_run)
    print(f"Inspected {result.inspected_rows:,} rows, repaired {result.repaired_rows:,}, skipped {result.missing_detection_rows:,} rows without detection metadata")
    if dry_run:
        print("Dry run - no changes written.")
    elif result.repaired_rows:
        print(f"Saved repaired vault database to {vault_db_path}")


if __name__ == "__main__":
    main()
