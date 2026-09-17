"""Migrate historical Axis StakedUSDx vault metadata.

Reviewed Axis StakedUSDx rewards vaults on Ethereum and Plasma may have been
persisted as generic ERC-4626 records before their address-routed Axis
classification was introduced. This metadata-only migration leaves leads,
reader state and historical-price Parquet files untouched.

Usage:

.. code-block:: shell

    source .local-test.env && DRY_RUN=false poetry run python scripts/erc-4626/migrate-axis-vault-metadata.py

Environment variables:

- ``VAULT_DB``: Vault metadata pickle path.
- ``DRY_RUN``: Set to ``false`` to write the migrated metadata. Defaults to
  ``true`` so the first production invocation is non-mutating.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from eth_defi.erc_4626.classification import AXIS_HARDCODED_PROTOCOLS_BY_CHAIN
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.axis.constants import AXIS_ETHEREUM_CHAIN_ID, AXIS_NOTES_BY_CHAIN, AXIS_PLASMA_CHAIN_ID, AXIS_SHORT_DESCRIPTION
from eth_defi.erc_4626.vault_protocol.axis.tags import STRATEGY_TAGS
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.strategy_tag import lookup_strategy_tags
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


#: Reviewed Axis StakedUSDx deployments to migrate.
AXIS_STAKED_USDX_SPECS = tuple(VaultSpec(chain_id, address) for chain_id, address in AXIS_HARDCODED_PROTOCOLS_BY_CHAIN)

#: Public application page for the reviewed Axis vaults.
AXIS_LINK = "https://app.axis.to/"


@dataclass(slots=True, frozen=True)
class AxisMetadataMigrationResult:
    """Outcome of an Axis metadata migration.

    :param matched_rows:
        Number of reviewed Axis rows present in the database.
    :param migrated_rows:
        Number of metadata rows changed.
    """

    matched_rows: int
    migrated_rows: int


def update_row_value(row: VaultRow, key: str, value: object) -> bool:
    """Set a cached Axis row value only when it differs.

    :param row:
        Mutable vault metadata row.
    :param key:
        Persisted row field name.
    :param value:
        Expected Axis metadata value.
    :return:
        ``True`` when the row changed.
    """

    if key in row and row[key] == value:
        return False
    row[key] = value
    return True


def remove_row_value(row: VaultRow, key: str) -> bool:
    """Remove a cached Axis value when present.

    :param row:
        Mutable vault metadata row.
    :param key:
        Persisted row field name.
    :return:
        ``True`` when the row changed.
    """
    if key not in row:
        return False
    del row[key]
    return True


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose a non-overwriting Axis backup path beside the metadata pickle.

    :param vault_db_path:
        Existing vault metadata database to protect.
    :return:
        Unused sibling backup path.
    """

    backup_path = vault_db_path.with_suffix(".pickle.bak-axis-metadata")
    if not backup_path.exists():
        return backup_path

    backup_index = 1
    while True:
        indexed_backup_path = Path(f"{backup_path}.{backup_index}")
        if not indexed_backup_path.exists():
            return indexed_backup_path
        backup_index += 1


def update_axis_withdrawal_metadata(row: VaultRow, chain_id: int) -> bool:
    """Update only withdrawal metadata that is independent of live state.

    Ethereum V2 always has an asynchronous lifecycle without a universal
    timing bound. Plasma V1 timing depends on its governance-controlled live
    cooldown, so the migration removes stale cached values and lets the next
    scanner pass repopulate them from the contract.

    :param chain_id:
        Axis deployment chain.
    :return:
        ``True`` when the cached row changed.
    """
    if chain_id == AXIS_ETHEREUM_CHAIN_ID:
        changes = (
            update_row_value(row, "_lockup", None),
            update_row_value(row, "_withdrawal_period", WithdrawalPeriod(None, None, WithdrawalDelayType.delay)),
        )
        return any(changes)
    if chain_id == AXIS_PLASMA_CHAIN_ID:
        changes = tuple(remove_row_value(row, key) for key in ("_lockup", "_withdrawal_period"))
        return any(changes)
    raise ValueError(f"Unsupported Axis deployment chain: {chain_id}")


def migrate_axis_row(row: VaultRow, axis_spec: VaultSpec, protocol_name: str, fee_data: FeeData) -> bool:
    """Apply the canonical metadata to one cached Axis row.

    :param row:
        Mutable cached vault row.
    :param axis_spec:
        Reviewed Axis deployment identity.
    :param protocol_name:
        Display protocol name.
    :param fee_data:
        Reviewed vault-contract fee data.
    :return:
        ``True`` when at least one value changed.
    """
    deployment_key = axis_spec.chain_id, axis_spec.vault_address
    expected_features = set(AXIS_HARDCODED_PROTOCOLS_BY_CHAIN[deployment_key])
    changes = [update_row_value(row, "features", expected_features)]

    detection = row.get("_detection_data")
    if isinstance(detection, ERC4262VaultDetection) and detection.features != expected_features:
        detection.features.clear()
        detection.features.update(expected_features)
        changes.append(True)

    updates = {
        "Protocol": protocol_name,
        "Features": ", ".join(sorted(item.name for item in expected_features)),
        "Mgmt fee": fee_data.management,
        "Perf fee": fee_data.performance,
        "Deposit fee": fee_data.deposit,
        "Withdraw fee": fee_data.withdraw,
        "_fees": fee_data,
        "Link": AXIS_LINK,
        "_short_description": AXIS_SHORT_DESCRIPTION,
        "_notes": AXIS_NOTES_BY_CHAIN[deployment_key],
        "_deposit_manager": None,
        "_strategy_tags": lookup_strategy_tags(STRATEGY_TAGS, axis_spec.vault_address),
    }
    changes.extend(update_row_value(row, key, value) for key, value in updates.items())
    changes.append(update_axis_withdrawal_metadata(row, axis_spec.chain_id))
    return any(changes)


def migrate_axis_metadata(vault_db_path: Path = DEFAULT_VAULT_DATABASE, *, dry_run: bool) -> AxisMetadataMigrationResult:
    """Update reviewed historical StakedUSDx rows with current Axis metadata.

    The migration does not create a new row when the scanner has not yet seen a
    deployment. This keeps unrelated rows untouched and leaves discovery as the
    sole source of new vault leads.

    :param vault_db_path:
        Vault metadata database to inspect and optionally migrate.
    :param dry_run:
        Report prospective changes without writing the database.
    :return:
        Matched and migrated row counters.
    """

    db = VaultDatabase.read(vault_db_path)
    protocol_name = get_vault_protocol_name({ERC4626Feature.axis_like})
    fee_data = FeeData(
        fee_mode=VaultFeeMode.feeless,
        management=0.0,
        performance=0.0,
        deposit=0.0,
        withdraw=0.0,
    )
    matched_rows = 0
    migrated_rows = 0
    for axis_spec in AXIS_STAKED_USDX_SPECS:
        row = db.rows.get(axis_spec)
        if row is None:
            logger.info("No Axis StakedUSDx row found for %s", axis_spec.as_string_id())
            continue

        matched_rows += 1
        migrated_rows += int(migrate_axis_row(row, axis_spec, protocol_name, fee_data))

    result = AxisMetadataMigrationResult(matched_rows=matched_rows, migrated_rows=migrated_rows)
    if result.migrated_rows == 0:
        logger.info("No Axis metadata rows need migration in %s", vault_db_path)
        return result

    if dry_run:
        logger.info("DRY RUN: would migrate %d Axis metadata rows in %s", result.migrated_rows, vault_db_path)
        return result

    backup_path = create_backup_path(vault_db_path)
    logger.info("Creating vault DB backup at %s", backup_path)
    shutil.copy2(vault_db_path, backup_path)
    db.write(vault_db_path)
    logger.info("Migrated %d Axis metadata rows in %s", result.migrated_rows, vault_db_path)
    return result


def main() -> None:
    """Run the Axis metadata migration command."""

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB", DEFAULT_VAULT_DATABASE)).expanduser()
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    assert vault_db_path.exists(), f"Vault database not found: {vault_db_path}"

    result = migrate_axis_metadata(vault_db_path, dry_run=dry_run)
    print(f"Matched {result.matched_rows:,} Axis rows, migrated {result.migrated_rows:,}")
    if dry_run:
        print("Dry run - no changes written.")


if __name__ == "__main__":
    main()
