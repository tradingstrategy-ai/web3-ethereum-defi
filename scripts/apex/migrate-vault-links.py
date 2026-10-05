"""Repair cached ApeX native vault links without rescanning.

Older exports incorrectly added ``/1`` to every vault URL. In the
`ApeX Omni application <https://omni.apex.exchange/vault>`__, that suffix
selects the official-vault view and shows an empty Insurance Vault for
user-created vaults. This migration regenerates only the ``Link`` field using
the exporter's canonical generator and preserves official-vault links.

Preview, then apply:

.. code-block:: shell

    poetry run python scripts/apex/migrate-vault-links.py
    DRY_RUN=false poetry run python scripts/apex/migrate-vault-links.py

Environment variables:

- ``VAULT_DB``: metadata pickle path; defaults to
  ``vault-metadata-db.pickle`` under the active ``PIPELINE_DATA_DIR``.
- ``DRY_RUN``: ``true`` (default) to preview, ``false`` to write.
- ``LOG_LEVEL``: console logging level, defaults to ``info``.

No RPC endpoint or API token is needed. Persistent mode takes the shared
scanner writer lock, saves a non-overwriting backup beside the pickle and
writes atomically. Prices, reader state, leads and scan progress are preserved.
Republish the metadata through the normal post-processing pipeline afterwards.
"""

import logging
import os
import shutil
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from eth_defi.apex.constants import APEX_CHAIN_ID
from eth_defi.apex.vault_data_export import get_apex_vault_link
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ApexLinkMigrationResult:
    """Describe the changes made or proposed by an ApeX link migration.

    Counters include already-correct rows as inspected, but only changed rows
    as updated. A dry run or an unchanged database does not create a backup.
    """

    #: Number of ApeX vault rows inspected.
    inspected_rows: int

    #: Number of ApeX links changed or proposed for change.
    updated_rows: int

    #: Backup created before writing, if any.
    backup_path: Path | None = None


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose an unused backup path beside the metadata pickle.

    Repeated migrations retain earlier backups by adding a numeric suffix
    rather than overwriting them.

    :param vault_db_path:
        Existing metadata pickle to protect.
    :return:
        An unused sibling backup path.
    """
    base_path = vault_db_path.with_name(f"{vault_db_path.name}.before-apex-link-migration")
    backup_path = base_path
    suffix = 1
    while backup_path.exists():
        backup_path = base_path.with_name(f"{base_path.name}.{suffix}")
        suffix += 1
    return backup_path


def migrate_apex_vault_links(vault_db_path: Path, *, dry_run: bool) -> ApexLinkMigrationResult:
    """Regenerate ApeX links in an existing shared metadata database.

    All matching identities are validated before any row is changed. Only
    ``Link`` values on the synthetic ApeX chain are replaced, leaving all
    other metadata and scanner state untouched. Persistent mode holds the
    scanner lock across reading, backing up and atomically writing the pickle.

    :param vault_db_path:
        Existing shared vault metadata pickle.
    :param dry_run:
        Report prospective changes without writing or creating a backup.
    :return:
        Inspected and updated row counts, and optional backup path.
    :raises ValueError:
        If an ApeX identity is not in the ``apex-vault-{vaultId}`` format.
    """
    vault_db_path = vault_db_path.expanduser().resolve()
    lock = nullcontext() if dry_run else wait_other_writers(vault_db_path.parent / "scan-pipeline", timeout=60)
    logger.info("%s ApeX links in %s", "Previewing" if dry_run else "Migrating", vault_db_path)
    with lock:
        vault_db = VaultDatabase.read(vault_db_path)
        inspected_rows = 0
        pending_updates: list[tuple[VaultRow, str]] = []
        for spec, row in vault_db.rows.items():
            if spec.chain_id != APEX_CHAIN_ID:
                continue
            inspected_rows += 1
            prefix = "apex-vault-"
            vault_id = spec.vault_address.removeprefix(prefix)
            if not spec.vault_address.startswith(prefix) or not vault_id.isascii() or not vault_id.isdecimal():
                raise ValueError(f"Invalid ApeX vault identity: {spec.vault_address}")
            new_link = get_apex_vault_link(vault_id)
            if row.get("Link") != new_link:
                logger.info("%s (%s): %s -> %s", row.get("Name", "<unnamed>"), spec.vault_address, row.get("Link"), new_link)
                pending_updates.append((row, new_link))

        backup_path = None
        if pending_updates and not dry_run:
            backup_path = create_backup_path(vault_db_path)
            logger.info("Backing up metadata to %s", backup_path)
            shutil.copy2(vault_db_path, backup_path)
            for row, new_link in pending_updates:
                row["Link"] = new_link
            vault_db.write(vault_db_path)

        logger.info("Inspected %d ApeX rows; %s %d links", inspected_rows, "would update" if dry_run else "updated", len(pending_updates))
        return ApexLinkMigrationResult(inspected_rows, len(pending_updates), backup_path)


def main() -> None:
    """Run the metadata-only migration using environment configuration.

    Dry-run mode is the default. Only an explicit ``DRY_RUN=false`` enables
    writes; invalid values abort before the metadata database is opened.

    :return:
        ``None`` after reporting the migration result.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run_value = os.environ.get("DRY_RUN", "true").strip().lower()
    if dry_run_value not in {"true", "false"}:
        message = "DRY_RUN must be true or false"
        raise ValueError(message)
    vault_db_path = Path(os.environ.get("VAULT_DB", get_pipeline_data_dir() / "vault-metadata-db.pickle"))
    migrate_apex_vault_links(vault_db_path, dry_run=dry_run_value == "true")


if __name__ == "__main__":
    main()
