"""Persist reviewed Centrifuge SPXA and deSPXA metadata in the vault cache.

The Centrifuge adapter now exposes address-scoped descriptions for the Janus
Henderson Anemoy S&P 500 SPXA and deSPXA Base vaults, and the SPXA vault has a
manual ``tokenised_fund`` flag. Existing ``vault-metadata-db.pickle`` rows keep
their old ``_description``, ``_short_description`` and ``_flags`` values until
the rows are rescanned or repaired.

This targeted migration updates only those cached metadata fields for the two
reviewed Base rows. It makes no RPC calls and does not alter vault leads,
reader state or price Parquet files.

Usage:

.. code-block:: shell

    # Inspect expected changes without writing (the default)
    source .local-test.env && poetry run python scripts/erc-4626/migrate-centrifuge-spxa-metadata.py

    # Back up and persist the reviewed metadata
    source .local-test.env && DRY_RUN=false \\
        poetry run python scripts/erc-4626/migrate-centrifuge-spxa-metadata.py

Environment variables:

- ``VAULT_DB_PATH``: Optional path to ``vault-metadata-db.pickle``.
- ``DRY_RUN``: Set to ``false`` to write changes. Defaults to ``true``.
- ``LOG_LEVEL``: Optional console log level. Defaults to ``info``.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.erc_4626.vault_protocol.centrifuge.vault import (
    CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY,
    DESPXA_BASE_VAULT_ADDRESS,
    SPXA_BASE_VAULT_ADDRESS,
)
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag, get_vault_special_flags
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Centrifuge SPXA and deSPXA vault rows are on Base.
BASE_CHAIN_ID = 8_453

#: Human-readable protocol label used by the cached vault rows.
CENTRIFUGE_PROTOCOL_NAME = "Centrifuge"

#: Exact cached rows repaired by this migration.
CENTRIFUGE_SPXA_METADATA_SPECS = (
    VaultSpec(BASE_CHAIN_ID, SPXA_BASE_VAULT_ADDRESS),
    VaultSpec(BASE_CHAIN_ID, DESPXA_BASE_VAULT_ADDRESS),
)


@dataclass(slots=True, frozen=True)
class CentrifugeSPXAMetadataUpdate:
    """Describe one cached Centrifuge SPXA metadata repair.

    :param spec:
        Chain and vault address identifying the cached metadata row.
    :param name:
        Cached human-readable vault name used for reporting.
    :param old_short_description:
        Existing listing description in the metadata cache.
    :param new_short_description:
        Reviewed listing description from the Centrifuge adapter overlay.
    :param old_description:
        Existing full Markdown description in the metadata cache.
    :param new_description:
        Reviewed full Markdown description from the Centrifuge adapter overlay.
    :param old_flags:
        Existing persisted vault flag set.
    :param new_flags:
        Current adapter-equivalent manual flag set.
    """

    spec: VaultSpec
    name: str
    old_short_description: str | None
    new_short_description: str
    old_description: str | None
    new_description: str
    old_flags: frozenset[VaultFlag]
    new_flags: frozenset[VaultFlag]


@dataclass(slots=True, frozen=True)
class CentrifugeSPXAMetadataMigrationResult:
    """Summarise the targeted Centrifuge SPXA metadata migration.

    :param inspected_rows:
        Total vault metadata rows in the source cache.
    :param updates:
        Rows that were or would be updated.
    """

    inspected_rows: int
    updates: tuple[CentrifugeSPXAMetadataUpdate, ...]


def parse_boolean_env(value: str | None, *, default: bool) -> bool:
    """Parse an explicit environment boolean value.

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
    """Choose a non-overwriting backup path for the metadata cache.

    :param vault_db_path:
        Existing metadata cache to protect.
    :return:
        Sibling backup path that does not already exist.
    """

    backup_path = vault_db_path.with_suffix(".pickle.bak-centrifuge-spxa-metadata")
    if not backup_path.exists():
        return backup_path

    backup_index = 1
    while True:
        indexed_backup_path = Path(f"{backup_path}.{backup_index}")
        if not indexed_backup_path.exists():
            return indexed_backup_path
        backup_index += 1


def normalise_flags(flags: object) -> frozenset[VaultFlag]:
    """Normalise a persisted flag collection to enum values.

    Older or externally patched pickles can contain enum values, enum names or
    enum string values. The migration compares normalised sets so it remains
    idempotent across those serialisation variants.

    :param flags:
        Stored ``_flags`` value.
    :return:
        Immutable normalised flag set.
    """

    if not flags:
        return frozenset()

    normalised_flags = set()
    for flag in flags:
        if isinstance(flag, VaultFlag):
            normalised_flags.add(flag)
            continue

        if isinstance(flag, str):
            try:
                normalised_flags.add(VaultFlag(flag))
                continue
            except ValueError:
                pass

            try:
                normalised_flags.add(VaultFlag[flag])
                continue
            except KeyError:
                logger.warning("Skipping unknown vault flag string: %s", flag)
                continue

        logger.warning("Skipping unsupported vault flag value: %r", flag)

    return frozenset(normalised_flags)


def format_flags(flags: frozenset[VaultFlag]) -> str:
    """Format vault flags for tabular reporting.

    :param flags:
        Normalised flag values.
    :return:
        Comma-separated flag values or ``-`` for an empty set.
    """

    if not flags:
        return "-"
    return ", ".join(sorted(flag.value for flag in flags))


def get_expected_metadata(spec: VaultSpec) -> tuple[str, str, frozenset[VaultFlag]]:
    """Resolve the reviewed metadata expected for one target row.

    :param spec:
        Target Centrifuge vault row.
    :return:
        Short description, full description and manual flag set.
    """

    description_overlay = CENTRIFUGE_VAULT_DESCRIPTION_OVERLAY[spec.vault_address]
    expected_flags = frozenset(get_vault_special_flags(spec.vault_address, CENTRIFUGE_PROTOCOL_NAME))
    return description_overlay.short_description, description_overlay.description, expected_flags


def collect_centrifuge_spxa_metadata_updates(vault_db: VaultDatabase) -> tuple[CentrifugeSPXAMetadataUpdate, ...]:
    """Collect reviewed metadata repairs from a metadata cache.

    Both target rows must be present before the migration can continue. This
    prevents an incomplete or unrelated cache from receiving a partial repair.

    :param vault_db:
        Persisted vault metadata loaded into memory.
    :return:
        Changed rows in deterministic address order.
    :raises KeyError:
        If either expected Centrifuge row is absent.
    """

    missing_specs = sorted(
        set(CENTRIFUGE_SPXA_METADATA_SPECS).difference(vault_db.rows),
        key=lambda spec: (spec.chain_id, spec.vault_address),
    )
    if missing_specs:
        formatted_specs = ", ".join(f"{spec.chain_id}-{spec.vault_address}" for spec in missing_specs)
        raise KeyError(f"Expected Centrifuge SPXA metadata rows are missing: {formatted_specs}")

    updates = []
    for spec in sorted(CENTRIFUGE_SPXA_METADATA_SPECS, key=lambda item: (item.chain_id, item.vault_address)):
        row = vault_db.rows[spec]
        new_short_description, new_description, new_flags = get_expected_metadata(spec)
        old_short_description = row.get("_short_description")
        old_description = row.get("_description")
        old_flags = normalise_flags(row.get("_flags"))

        if (old_short_description, old_description, old_flags) == (new_short_description, new_description, new_flags):
            continue

        updates.append(
            CentrifugeSPXAMetadataUpdate(
                spec=spec,
                name=str(row.get("Name") or ""),
                old_short_description=old_short_description,
                new_short_description=new_short_description,
                old_description=old_description,
                new_description=new_description,
                old_flags=old_flags,
                new_flags=new_flags,
            )
        )

    return tuple(updates)


def apply_centrifuge_spxa_metadata_updates(vault_db: VaultDatabase, updates: tuple[CentrifugeSPXAMetadataUpdate, ...]) -> None:
    """Apply collected Centrifuge metadata repairs in memory.

    :param vault_db:
        Loaded metadata cache to mutate.
    :param updates:
        Reviewed updates collected from the same cache.
    :return:
        None.
    """

    for update in updates:
        row: VaultRow = vault_db.rows[update.spec]
        row["_short_description"] = update.new_short_description
        row["_description"] = update.new_description
        row["_flags"] = set(update.new_flags)


def migrate_centrifuge_spxa_metadata(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    *,
    dry_run: bool,
) -> CentrifugeSPXAMetadataMigrationResult:
    """Persist reviewed SPXA and deSPXA metadata in a vault metadata cache.

    The migration is idempotent and writes only ``_short_description``,
    ``_description`` and ``_flags`` on the two known Base Centrifuge rows. It
    creates a sibling backup before its first write.

    :param vault_db_path:
        Existing vault metadata pickle to inspect and optionally update.
    :param dry_run:
        Report updates without modifying the pickle when ``True``.
    :return:
        Inspection count and changed rows.
    """

    vault_db = VaultDatabase.read(vault_db_path)
    updates = collect_centrifuge_spxa_metadata_updates(vault_db)
    result = CentrifugeSPXAMetadataMigrationResult(inspected_rows=len(vault_db.rows), updates=updates)

    if updates:
        print(
            tabulate(
                [
                    [
                        update.spec.chain_id,
                        update.spec.vault_address,
                        update.name,
                        update.old_short_description,
                        update.new_short_description,
                        format_flags(update.old_flags),
                        format_flags(update.new_flags),
                    ]
                    for update in updates
                ],
                headers=["chain", "address", "name", "old short description", "new short description", "old flags", "new flags"],
                tablefmt="simple",
            )
        )

    if not updates:
        logger.info("No stale Centrifuge SPXA metadata found in %s", vault_db_path)
        return result
    if dry_run:
        logger.info("DRY RUN: would update %d Centrifuge SPXA metadata rows in %s", len(updates), vault_db_path)
        return result

    backup_path = create_backup_path(vault_db_path)
    logger.info("Creating vault metadata backup at %s", backup_path)
    shutil.copy2(vault_db_path, backup_path)
    apply_centrifuge_spxa_metadata_updates(vault_db, updates)
    vault_db.write(vault_db_path)
    logger.info("Updated %d Centrifuge SPXA metadata rows in %s", len(updates), vault_db_path)
    return result


def main() -> None:
    """Run the Centrifuge SPXA metadata migration from environment configuration.

    :return:
        ``None``. Raises if the metadata cache is missing or incomplete.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault database not found: {vault_db_path}")
    dry_run = parse_boolean_env(os.environ.get("DRY_RUN"), default=True)
    result = migrate_centrifuge_spxa_metadata(vault_db_path, dry_run=dry_run)
    print(f"Inspected {result.inspected_rows:,} rows and updated {len(result.updates):,} Centrifuge SPXA metadata rows.")
    if dry_run:
        print("Dry run - no changes written.")
    elif result.updates:
        print(f"Saved migrated vault metadata to {vault_db_path}")


if __name__ == "__main__":
    main()
