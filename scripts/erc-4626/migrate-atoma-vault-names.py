"""Apply curated display names to the two cached Atoma vault records.

Atoma's ERC-4626 share-token ``name()`` values are generic and do not identify
the strategy.  The Atoma adapter now provides reviewed address-scoped display
names, but existing vault metadata pickles retain the previous onchain values.
This targeted migration persists the two curated names without making RPC
calls, rescanning vaults, or modifying any price, reader-state, or other
metadata fields.

The generic ``migrate-vault-token-metadata.py`` command must not be used for
this repair: it deliberately reads the onchain token names and would overwrite
these curated names.

Usage:

.. code-block:: shell

    # Inspect the two expected updates without writing (the default)
    source .local-test.env && poetry run python scripts/erc-4626/migrate-atoma-vault-names.py

    # Back up and persist the curated names
    source .local-test.env && DRY_RUN=false \\
        poetry run python scripts/erc-4626/migrate-atoma-vault-names.py

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

from eth_defi.erc_4626.vault_protocol.atoma.vault import ATOMA_VAULT_NAME_OVERLAY
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase

logger = logging.getLogger(__name__)

#: Atoma vaults are ERC-4626 deployments on Arbitrum One.
ATOMA_CHAIN_ID = 42_161

#: Exact cached rows and their reviewed display names.
ATOMA_VAULT_NAME_UPDATES: dict[VaultSpec, str] = {VaultSpec(ATOMA_CHAIN_ID, address): name for address, name in ATOMA_VAULT_NAME_OVERLAY.items()}


@dataclass(slots=True, frozen=True)
class AtomaVaultNameUpdate:
    """Describe one cached Atoma vault display-name change."""

    #: Chain and vault address identifying the cached metadata row.
    spec: VaultSpec

    #: Generic name currently persisted in the metadata database.
    old_name: str | None

    #: Curated address-scoped display name to persist.
    new_name: str


@dataclass(slots=True, frozen=True)
class AtomaVaultNameMigrationResult:
    """Summarise the targeted Atoma display-name migration."""

    #: Total vault metadata rows in the source cache.
    inspected_rows: int

    #: Rows that were or would be updated.
    updates: tuple[AtomaVaultNameUpdate, ...]


def parse_boolean_env(value: str | None, *, default: bool) -> bool:
    """Parse an explicit environment boolean without unsafe fallbacks.

    :param value:
        Environment value, or ``None`` when it is absent.
    :param default:
        Value to return for an absent environment variable.
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
    """Choose a non-overwriting backup path for the metadata pickle.

    :param vault_db_path:
        Existing metadata cache that will be backed up before a write.
    :return:
        Unused sibling backup path.
    """

    backup_path = vault_db_path.with_suffix(".pickle.bak-atoma-vault-names")
    if not backup_path.exists():
        return backup_path

    backup_index = 1
    while True:
        indexed_backup_path = Path(f"{backup_path}.{backup_index}")
        if not indexed_backup_path.exists():
            return indexed_backup_path
        backup_index += 1


def collect_atoma_vault_name_updates(vault_db: VaultDatabase) -> tuple[AtomaVaultNameUpdate, ...]:
    """Collect the two reviewed Atoma name updates from a metadata cache.

    Both target rows must be present before the migration can continue. This
    prevents an incomplete or unrelated cache from receiving a partial repair.

    :param vault_db:
        Persisted vault metadata loaded into memory.
    :return:
        Changed rows in deterministic address order.
    :raises KeyError:
        If either expected Atoma vault row is absent.
    """

    missing_specs = sorted(
        set(ATOMA_VAULT_NAME_UPDATES).difference(vault_db.rows),
        key=lambda spec: (spec.chain_id, spec.vault_address),
    )
    if missing_specs:
        formatted_specs = ", ".join(f"{spec.chain_id}-{spec.vault_address}" for spec in missing_specs)
        raise KeyError(f"Expected Atoma vault metadata rows are missing: {formatted_specs}")

    updates = []
    for spec, new_name in sorted(
        ATOMA_VAULT_NAME_UPDATES.items(),
        key=lambda item: (item[0].chain_id, item[0].vault_address),
    ):
        old_name = vault_db.rows[spec].get("Name")
        if old_name != new_name:
            updates.append(AtomaVaultNameUpdate(spec=spec, old_name=old_name, new_name=new_name))
    return tuple(updates)


def migrate_atoma_vault_names(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    *,
    dry_run: bool,
) -> AtomaVaultNameMigrationResult:
    """Persist address-scoped Atoma strategy names in a vault metadata cache.

    The migration is idempotent and writes only ``Name`` on the two known
    Arbitrum Atoma rows. It creates a sibling backup before its first write.

    :param vault_db_path:
        Existing vault metadata pickle to inspect and optionally update.
    :param dry_run:
        Report updates without modifying the pickle when ``True``.
    :return:
        Inspection count and changed rows.
    """

    vault_db = VaultDatabase.read(vault_db_path)
    updates = collect_atoma_vault_name_updates(vault_db)
    result = AtomaVaultNameMigrationResult(inspected_rows=len(vault_db.rows), updates=updates)

    if updates:
        print(
            tabulate(
                [[update.spec.chain_id, update.spec.vault_address, update.old_name, update.new_name] for update in updates],
                headers=["chain", "address", "old name", "new name"],
                tablefmt="simple",
            )
        )

    if not updates:
        logger.info("No stale Atoma vault names found in %s", vault_db_path)
        return result
    if dry_run:
        logger.info("DRY RUN: would update %d Atoma vault names in %s", len(updates), vault_db_path)
        return result

    backup_path = create_backup_path(vault_db_path)
    logger.info("Creating vault metadata backup at %s", backup_path)
    shutil.copy2(vault_db_path, backup_path)
    for update in updates:
        vault_db.rows[update.spec]["Name"] = update.new_name
    vault_db.write(vault_db_path)
    logger.info("Updated %d Atoma vault names in %s", len(updates), vault_db_path)
    return result


def main() -> None:
    """Run the Atoma name migration from environment configuration.

    :return:
        ``None``. Raises if the metadata cache is missing or incomplete.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault database not found: {vault_db_path}")
    dry_run = parse_boolean_env(os.environ.get("DRY_RUN"), default=True)
    result = migrate_atoma_vault_names(vault_db_path, dry_run=dry_run)
    print(f"Inspected {result.inspected_rows:,} rows and updated {len(result.updates):,} Atoma vault names.")
    if dry_run:
        print("Dry run - no changes written.")
    elif result.updates:
        print(f"Saved migrated vault metadata to {vault_db_path}")


if __name__ == "__main__":
    main()
