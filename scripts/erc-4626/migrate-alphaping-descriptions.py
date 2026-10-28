#!/usr/bin/env python3
"""Refresh reviewed AlphaPing descriptions in an existing metadata database.

Only ``_description`` and ``_short_description`` are updated. This command
does not access RPC endpoints or alter price files, reader state, vault names,
notes, risk flags or listing status. Missing deployments are reported rather
than inserted. Every existing target's protocol is validated before mutation.

Usage::

    DRY_RUN=true poetry run python scripts/erc-4626/migrate-alphaping-descriptions.py
    DRY_RUN=false poetry run python scripts/erc-4626/migrate-alphaping-descriptions.py

Environment variables:

- ``VAULT_DB_PATH``: Defaults to ``$PIPELINE_DATA_DIR/vault-metadata-db.pickle``
  or the standard pipeline directory when ``PIPELINE_DATA_DIR`` is unset.
- ``DRY_RUN``: Defaults to ``true``; report changes without writing.
- ``LOG_LEVEL``: Defaults to ``info``.

The reviewed inventory and canonical sources are documented in
``docs/protocol-research/alphaping.md``. Persistent mode uses the scanner writer
lock and :class:`~eth_defi.vault.vaultdb.VaultDatabase` atomic pickle write.
"""

import logging
import os
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.handwritten_metadata import ALPHAPING_IPOR_VAULT_METADATA, ALPHAPING_MORPHO_VAULT_METADATA, ALPHAPING_VAULT_METADATA
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class AlphaPingDescriptionUpdate:
    """Describe the maintained fields changed for one reviewed deployment."""

    #: Chain and share-token address of the existing database row.
    spec: VaultSpec

    #: Description fields whose stored values differ from the reviewed copy.
    changed_fields: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class AlphaPingDescriptionMigrationResult:
    """Report reviewed, missing and changed metadata rows."""

    #: Number of reviewed deployments already in the database.
    inspected_rows: int

    #: Reviewed deployments absent from this database; never inserted.
    missing_specs: tuple[VaultSpec, ...]

    #: Changes applied, or proposed when running without writes.
    updates: tuple[AlphaPingDescriptionUpdate, ...]


def migrate_alphaping_descriptions(vault_db: VaultDatabase, *, dry_run: bool) -> AlphaPingDescriptionMigrationResult:
    """Refresh descriptions for existing rows in the frozen AlphaPing inventory.

    All protocol classifications are checked before changing any row. The
    returned changes can be reviewed without mutating memory in dry-run mode.
    Missing rows are reported and never created by this metadata-only repair.

    :param vault_db: Existing metadata database.
    :param dry_run: Report changes without mutating the database when true.
    :return: Existing row count, missing specifications and changed fields.
    :raises ValueError: If an existing reviewed row has an unexpected protocol.
    """
    expected_protocols = {
        **{VaultSpec(chain_id, address): "Morpho" for chain_id, address in ALPHAPING_MORPHO_VAULT_METADATA},
        **{VaultSpec(chain_id, address): "IPOR Fusion" for chain_id, address in ALPHAPING_IPOR_VAULT_METADATA},
    }
    missing_specs = tuple(spec for spec in expected_protocols if spec not in vault_db.rows)
    existing_specs = tuple(spec for spec in expected_protocols if spec in vault_db.rows)
    unexpected_protocols = tuple((spec, vault_db.rows[spec].get("Protocol"), expected_protocols[spec]) for spec in existing_specs if vault_db.rows[spec].get("Protocol") != expected_protocols[spec])
    if unexpected_protocols:
        details = ", ".join(f"{spec.as_string_id()}: {actual!r}, expected {expected!r}" for spec, actual, expected in unexpected_protocols)
        raise ValueError(f"AlphaPing description migration found unexpected protocols: {details}")

    pending: list[tuple[AlphaPingDescriptionUpdate, dict[str, str]]] = []
    for spec in existing_specs:
        metadata = ALPHAPING_VAULT_METADATA[spec.chain_id, spec.vault_address]
        public_copy = {"_description": metadata.description, "_short_description": metadata.short_description}
        changed_fields = tuple(field for field, value in public_copy.items() if vault_db.rows[spec].get(field) != value)
        if changed_fields:
            pending.append((AlphaPingDescriptionUpdate(spec, changed_fields), public_copy))

    if not dry_run:
        for update, public_copy in pending:
            for field in update.changed_fields:
                vault_db.rows[update.spec][field] = public_copy[field]

    return AlphaPingDescriptionMigrationResult(
        inspected_rows=len(existing_specs),
        missing_specs=missing_specs,
        updates=tuple(update for update, _ in pending),
    )


def main() -> None:
    """Read environment configuration, report changes and optionally persist them.

    Persistent mode holds the same writer lock as the scanner for the entire
    read-modify-write operation. Idempotent reruns do not rewrite the pickle.

    :return: None after logging the result.
    :raises ValueError: If ``DRY_RUN`` is invalid or target protocols mismatch.
    :raises FileNotFoundError: If the metadata database is absent.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    value = os.environ.get("DRY_RUN", "true").lower()
    if value not in {"1", "true", "yes", "0", "false", "no"}:
        raise ValueError(f"DRY_RUN must be true or false, got {value!r}")
    dry_run = value in {"1", "true", "yes"}
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(get_pipeline_data_dir() / "vault-metadata-db.pickle"))).expanduser()
    if not vault_db_path.exists():
        raise FileNotFoundError(vault_db_path)

    lock = nullcontext() if dry_run else wait_other_writers(vault_db_path.parent / "scan-pipeline", timeout=60)
    with lock:
        logger.info("Reading AlphaPing metadata from %s", vault_db_path)
        vault_db = VaultDatabase.read(vault_db_path)
        result = migrate_alphaping_descriptions(vault_db, dry_run=dry_run)
        for spec in result.missing_specs:
            logger.warning("Reviewed AlphaPing vault %s is missing; skipping", spec.as_string_id())
        report_rows = [
            {
                "Vault": update.spec.as_string_id(),
                "Stored name": vault_db.rows[update.spec].get("Name"),
                "Changed fields": ", ".join(update.changed_fields),
            }
            for update in result.updates
        ]
        if report_rows:
            print(tabulate(report_rows, headers="keys", tablefmt="rounded_outline"))
        if not dry_run and result.updates:
            vault_db.write(vault_db_path)
        logger.info(
            "AlphaPing descriptions: inspected=%d, missing=%d, changed=%d, dry_run=%s",
            result.inspected_rows,
            len(result.missing_specs),
            len(result.updates),
            dry_run,
        )


if __name__ == "__main__":
    main()
