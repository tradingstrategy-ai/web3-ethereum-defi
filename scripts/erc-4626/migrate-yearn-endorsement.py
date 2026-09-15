"""Apply Yearn primary-list exclusions to cached ERC-4626 classifications.

`Yearn's vault registry <https://kong.yearn.fi/api/rest/list/vaults>`__ is
broader than Trading Strategy's Yearn-operated catalogue. This migration marks
both its established ``isSet``/``isYearn`` exclusion and records with an empty
``inclusion`` object as not Yearn-operated for our protocol attribution. This
deliberately removes uncurated strategy targets and wrappers, including Katana
Stablecoin Transformer depositors, from Yearn protocol and curated-vault lists.
The generic Yearn web-page template may still render such a record as a Yearn
vault; that presentation does not change our attribution policy. The migration
retains technical Yearn interface features and their specialised vault adapter.
It is not a statement about contract safety or code provenance.

The migration changes metadata only. It does not touch price Parquet files,
reader state, discovery leads, or any vault history.

Usage:

.. code-block:: shell

    source .local-test.env && DRY_RUN=true \\
        poetry run python scripts/erc-4626/migrate-yearn-endorsement.py

    source .local-test.env && DRY_RUN=false \\
        poetry run python scripts/erc-4626/migrate-yearn-endorsement.py

Environment variables:

- ``VAULT_DB``: Optional metadata pickle path. Defaults to the normal vault DB.
- ``DRY_RUN``: Set to ``false`` to create a backup and write updates. Defaults
  to ``true``.
- ``LOG_LEVEL``: Optional console log level. Defaults to ``info``.
"""

import logging
import os
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.yearn.endorsement import (
    YearnRegistryExclusions,
    add_yearn_registry_exclusion,
    fetch_yearn_registry_exclusions,
    is_yearn_registry_excluded_vault,
)
from eth_defi.research.vault_metrics import slugify_protocol
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class YearnRegistryExclusionUpdate:
    """Describe one cached row whose Yearn attribution is removed."""

    #: Chain/address identifier of the updated vault.
    spec: VaultSpec
    #: Cached display name.
    name: str
    #: Previous Yearn protocol label.
    old_protocol: str
    #: Replacement label derived from the retained feature markers.
    new_protocol: str
    #: Cached feature markers before adding the list-membership marker.
    old_features: frozenset[ERC4626Feature]
    #: Feature markers persisted after adding the list-membership marker.
    new_features: frozenset[ERC4626Feature]


def _get_cached_features(row: VaultRow) -> set[ERC4626Feature]:
    """Return the most recent persisted feature set for one vault row.

    Newer rows store features at the top level, while legacy rows retain them
    only in the detection envelope. Keeping this fallback in one place ensures
    the migration updates the same data that regular metadata exports read.

    :param row:
        Cached vault metadata row.
    :return:
        Stored ERC-4626 feature markers, or an empty set when unavailable.
    """
    features = row.get("features")
    if features is not None:
        return set(features)

    detection = row.get("_detection_data")
    if isinstance(detection, ERC4262VaultDetection):
        return set(detection.features)
    return set()


def collect_yearn_registry_exclusion_updates(
    vault_db: VaultDatabase,
    *,
    exclusions: YearnRegistryExclusions | None = None,
) -> list[YearnRegistryExclusionUpdate]:
    """Find Yearn-labelled rows excluded from Yearn's primary vault list.

    The function does not mutate the database, allowing callers to inspect
    every proposed update before writing. It retains technical interface
    features while adding a marker that controls protocol attribution.

    :param vault_db:
        In-memory metadata database to inspect.
    :param exclusions:
        Optional pre-fetched Yearn primary-list exclusion index. When omitted,
        it is fetched once for the complete migration run.
    :return:
        Proposed cached-row classification updates.
    """
    if exclusions is None:
        exclusions = fetch_yearn_registry_exclusions()
    if exclusions is None:
        message = "Cannot migrate Yearn registry exclusions because the live registry is unavailable"
        raise RuntimeError(message)

    updates: list[YearnRegistryExclusionUpdate] = []
    for spec, row in vault_db.rows.items():
        if row.get("Protocol") != "Yearn" or not is_yearn_registry_excluded_vault(spec.chain_id, spec.vault_address, exclusions=exclusions):
            continue

        old_features = _get_cached_features(row)
        new_features = add_yearn_registry_exclusion(spec.chain_id, spec.vault_address, old_features, exclusions=exclusions)
        new_protocol = get_vault_protocol_name(new_features)
        updates.append(
            YearnRegistryExclusionUpdate(
                spec=spec,
                name=str(row.get("Name", "")),
                old_protocol=str(row.get("Protocol", "")),
                new_protocol=new_protocol,
                old_features=frozenset(old_features),
                new_features=frozenset(new_features),
            )
        )
    return updates


def apply_yearn_registry_exclusion_updates(vault_db: VaultDatabase, updates: list[YearnRegistryExclusionUpdate]) -> None:
    """Apply Yearn primary-list exclusions to cached vault rows.

    The replacement protocol label and slug are derived from retained features.
    Updating the persisted detection makes subsequent exports and curator
    resolution agree with the top-level metadata fields.

    :param vault_db:
        In-memory metadata database to modify.
    :param updates:
        Validated updates returned by :func:`collect_yearn_registry_exclusion_updates`.
    :return:
        ``None`` after applying all metadata updates.
    """
    for update in updates:
        row: VaultRow = vault_db.rows[update.spec].copy()
        features = set(update.new_features)
        row["features"] = features
        row["Features"] = ", ".join(sorted(feature.name for feature in features))
        row["Protocol"] = update.new_protocol
        row["protocol_slug"] = slugify_protocol(update.new_protocol)
        row["Link"] = f"https://routescan.io/address/{update.spec.vault_address}"
        detection = row.get("_detection_data")
        if isinstance(detection, ERC4262VaultDetection):
            row["_detection_data"] = replace(detection, features=features)
        vault_db.rows[update.spec] = row


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose a non-overwriting backup path for a metadata database.

    :param vault_db_path:
        Existing metadata database to protect.
    :return:
        Available sibling path for the complete metadata backup.
    """
    backup_path = vault_db_path.with_suffix(".pickle.bak-yearn-registry-exclusion")
    backup_index = 1
    while backup_path.exists():
        backup_path = vault_db_path.with_suffix(f".pickle.bak-yearn-registry-exclusion.{backup_index}")
        backup_index += 1
    return backup_path


def migrate_yearn_registry_exclusions(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    *,
    dry_run: bool,
    exclusions: YearnRegistryExclusions | None = None,
) -> list[YearnRegistryExclusionUpdate]:
    """Apply Yearn primary-list exclusions to a persisted metadata DB.

    The complete database is read and all updates are calculated before any
    write. A non-dry run copies the original pickle beside itself before the
    atomic database write, so the operation is reversible.

    :param vault_db_path:
        Metadata database pickle to inspect and optionally update.
    :param dry_run:
        When ``True``, report proposed updates without writing a backup or DB.
    :param exclusions:
        Optional pre-fetched Yearn primary-list exclusion index for callers
        that need a deterministic migration review.
    :return:
        Proposed or applied updates.
    """
    vault_db = VaultDatabase.read(vault_db_path)
    updates = collect_yearn_registry_exclusion_updates(vault_db, exclusions=exclusions)
    if dry_run or not updates:
        return updates

    backup_path = create_backup_path(vault_db_path)
    shutil.copy2(vault_db_path, backup_path)
    apply_yearn_registry_exclusion_updates(vault_db, updates)
    vault_db.write(vault_db_path)
    logger.info("Backed up %s and wrote %d Yearn registry exclusions", backup_path, len(updates))
    return updates


def main() -> None:
    """Run the Yearn registry-exclusion migration from environment variables.

    :return:
        ``None`` after logging each proposed or applied metadata update.
    """
    setup_console_logging(default_log_level="info")
    vault_db_path = Path(os.environ.get("VAULT_DB", str(DEFAULT_VAULT_DATABASE))).expanduser()
    dry_run = os.environ.get("DRY_RUN", "true").lower() not in {"0", "false", "no"}
    updates = migrate_yearn_registry_exclusions(vault_db_path, dry_run=dry_run)
    logger.info("%d Yearn registry exclusions found in %s", len(updates), vault_db_path)
    for update in updates:
        logger.info("%s %s: %s -> %s", update.spec.as_string_id(), update.name, update.old_protocol, update.new_protocol)
    if dry_run:
        logger.info("DRY_RUN=true; metadata database was not modified")


if __name__ == "__main__":
    main()
