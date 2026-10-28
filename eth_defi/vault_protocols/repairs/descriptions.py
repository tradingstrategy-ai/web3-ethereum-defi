#!/usr/bin/env python3
"""Repair persisted vault descriptions and shorten listing summaries.

Numbered migrations call the in-memory transformation helpers in this module.
The old filesystem wrappers remain for regression compatibility; production
uses ``vault-migrate`` through ``scripts/run-vault-migrations.sh`` as described
in ``.claude/docs/migration-scripts.md``. The former standalone entrypoint is
retired. Helpers used by numbered scripts must not acquire locks or write files.
"""

import logging
import os
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class VaultDescriptionUpdate:
    """Describe a single persisted vault description field requiring repair."""

    #: Chain and vault address of the affected row.
    spec: VaultSpec

    #: Persisted description field to update.
    field: str

    #: Existing text, including any line breaks or literal newline escapes.
    old_value: str

    #: Equivalent single-line text.
    new_value: str


def repair_vault_descriptions(vault_db: VaultDatabase, *, dry_run: bool) -> tuple[VaultDescriptionUpdate, ...]:
    """Normalise descriptions and retain only the first sentence of summaries.

    Calculate all proposed updates before applying them, so malformed source
    values cannot cause a partially applied in-memory repair. Missing and null
    fields are preserved, and rerunning a completed repair has no effect.

    :param vault_db:
        Existing metadata database loaded from its pickle.
    :param dry_run:
        Report proposed changes without mutating the database when ``True``.
    :return:
        Field updates proposed or applied, in database row order.
    """
    updates = []
    for spec, row in vault_db.rows.items():
        for field in ("_description", "_short_description"):
            old_value = row.get(field)
            new_value = normalise_vault_short_description(old_value) if field == "_short_description" else normalise_vault_description(old_value)
            if old_value != new_value:
                updates.append(VaultDescriptionUpdate(spec, field, old_value, new_value))

    if not dry_run:
        for update in updates:
            vault_db.rows[update.spec][update.field] = update.new_value
    return tuple(updates)


def main() -> None:
    """Report or atomically apply the vault description repair.

    Persistent mode takes the scanner's writer lock before reading the pickle.
    Dry runs never write the pickle; unchanged files are not rewritten either.

    :return:
        ``None`` after logging the proposed changes and repair outcome.
    :raises ValueError:
        If ``DRY_RUN`` is not an explicit boolean value.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run_value = os.environ.get("DRY_RUN", "true").lower()
    if dry_run_value not in {"1", "true", "yes", "0", "false", "no"}:
        message = "DRY_RUN must be true or false"
        raise ValueError(message)
    dry_run = dry_run_value in {"1", "true", "yes"}
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(get_pipeline_data_dir() / "vault-metadata-db.pickle"))).expanduser()
    if not vault_db_path.exists():
        raise FileNotFoundError(vault_db_path)

    lock = nullcontext() if dry_run else wait_other_writers(vault_db_path.parent / "scan-pipeline", timeout=60)
    with lock:
        logger.info("Reading vault description metadata from %s", vault_db_path)
        vault_db = VaultDatabase.read(vault_db_path)
        inspected = len(vault_db.rows)
        updates = repair_vault_descriptions(vault_db, dry_run=dry_run)
        for update in updates:
            logger.info("%s %s: %r -> %r", update.spec.as_string_id(), update.field, update.old_value, update.new_value)
        if not dry_run and updates:
            vault_db.write(vault_db_path)

    logger.info("Vault description repair: inspected %d rows, %d changed fields, dry run: %s", inspected, len(updates), dry_run)
