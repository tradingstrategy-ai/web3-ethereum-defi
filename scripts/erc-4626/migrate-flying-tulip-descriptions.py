#!/usr/bin/env python3
"""Refresh persisted Flying Tulip public descriptions without RPC access.

The Flying Tulip protocol metadata YAML is published by the normal protocol
metadata exporter and does not live in the vault metadata pickle. Individual
vault rows do persist their listing summary and protocol notes, however. This
one-off migration updates only ``_short_description``, ``_notes`` and
``_protocol_notes`` for the three reviewed sftUSD deployments.

The script does not discover vaults, make network requests, or touch price
Parquet files, reader state, lead state, timestamp caches or historical context.
All three reviewed rows must already exist and be classified as Flying Tulip;
otherwise the migration fails before changing anything.

Usage::

    DRY_RUN=true poetry run python scripts/erc-4626/migrate-flying-tulip-descriptions.py
    DRY_RUN=false poetry run python scripts/erc-4626/migrate-flying-tulip-descriptions.py

Environment variables:

- ``DRY_RUN``: Report the exact changes without writing. Defaults to ``true``.
- ``VAULT_DB_PATH``: Optional metadata pickle path. Defaults to
  ``$PIPELINE_DATA_DIR/vault-metadata-db.pickle``.
- ``LOG_LEVEL``: Console log level. Defaults to ``info``.
"""

import logging
import os
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_NOTES, FLYING_TULIP_SFTUSD_BY_CHAIN, FLYING_TULIP_SHORT_DESCRIPTION
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

logger = logging.getLogger(__name__)

#: Protocol name expected on every reviewed persisted row.
FLYING_TULIP_PROTOCOL_NAME = "Flying Tulip"


@dataclass(slots=True, frozen=True)
class FlyingTulipDescriptionUpdate:
    """Describe the public-copy fields changed for one reviewed sftUSD row."""

    #: Chain and sftUSD proxy identifying the persisted metadata row.
    spec: VaultSpec

    #: Persisted fields whose values differ from the maintained copy.
    changed_fields: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class FlyingTulipDescriptionMigrationResult:
    """Summarise one Flying Tulip public-copy migration run."""

    #: Number of reviewed sftUSD rows inspected.
    inspected_rows: int

    #: Number of rows changed or proposed for change.
    updated_rows: int

    #: Total number of persisted field values changed.
    updated_fields: int

    #: Address-scoped update details in reviewed chain order.
    updates: tuple[FlyingTulipDescriptionUpdate, ...]


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse one strict boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value returned when the variable is absent.
    :return:
        Parsed boolean value.
    :raises ValueError:
        If the value is not a recognised boolean literal.
    """

    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() in {"1", "true", "yes"}:
        return True
    if value.lower() in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true or false, got {value!r}")


def _get_reviewed_specs() -> tuple[VaultSpec, ...]:
    """Return the fixed Flying Tulip migration scope.

    :return:
        Reviewed Ethereum, BNB Chain and Sonic sftUSD specifications.
    """

    return tuple(VaultSpec(chain_id, address) for chain_id, address in FLYING_TULIP_SFTUSD_BY_CHAIN.items())


def _get_public_copy() -> dict[str, str]:
    """Return the persisted Flying Tulip fields maintained by this migration.

    ``_notes`` is consumed by the vault JSON exporter. ``_protocol_notes`` is
    kept in sync because the Flying Tulip adapter also stores the same
    protocol-owned note in its extra scanner data.

    :return:
        Persisted field names mapped to their maintained values.
    """

    return {
        "_short_description": FLYING_TULIP_SHORT_DESCRIPTION,
        "_notes": FLYING_TULIP_NOTES,
        "_protocol_notes": FLYING_TULIP_NOTES,
    }


def migrate_flying_tulip_descriptions(vault_db: VaultDatabase, *, dry_run: bool) -> FlyingTulipDescriptionMigrationResult:
    """Update public copy for every reviewed persisted Flying Tulip row.

    The complete target set and protocol classifications are validated before
    any row is mutated. Unrelated fields on the selected rows and all unrelated
    database rows and state mappings are preserved.

    :param vault_db:
        Existing vault metadata database loaded from the production pickle.
    :param dry_run:
        Report changes without mutating ``vault_db`` when ``True``.
    :return:
        Reviewed row, changed row and changed field counts.
    :raises ValueError:
        If a reviewed row is absent or has an unexpected protocol.
    """

    reviewed_specs = _get_reviewed_specs()
    missing_specs = tuple(spec for spec in reviewed_specs if spec not in vault_db.rows)
    if missing_specs:
        missing = ", ".join(spec.as_string_id() for spec in missing_specs)
        raise ValueError(f"Flying Tulip description migration is missing reviewed rows: {missing}")

    unexpected_protocols = tuple((spec, vault_db.rows[spec].get("Protocol")) for spec in reviewed_specs if vault_db.rows[spec].get("Protocol") != FLYING_TULIP_PROTOCOL_NAME)
    if unexpected_protocols:
        details = ", ".join(f"{spec.as_string_id()}={protocol!r}" for spec, protocol in unexpected_protocols)
        raise ValueError(f"Flying Tulip description migration found unexpected protocols: {details}")

    public_copy = _get_public_copy()
    updates = tuple(
        FlyingTulipDescriptionUpdate(
            spec=spec,
            changed_fields=tuple(field for field, value in public_copy.items() if vault_db.rows[spec].get(field) != value),
        )
        for spec in reviewed_specs
    )
    changed_updates = tuple(update for update in updates if update.changed_fields)

    if not dry_run:
        for update in changed_updates:
            row: VaultRow = vault_db.rows[update.spec]
            for field in update.changed_fields:
                row[field] = public_copy[field]

    return FlyingTulipDescriptionMigrationResult(
        inspected_rows=len(reviewed_specs),
        updated_rows=len(changed_updates),
        updated_fields=sum(len(update.changed_fields) for update in changed_updates),
        updates=changed_updates,
    )


def main() -> None:
    """Run the fixed-scope Flying Tulip description migration.

    Persistent mode takes the shared scanner writer lock before reading and
    atomically replacing the metadata pickle. Dry-run mode performs the same
    validation and comparison without mutating memory or persistent files.

    :return:
        ``None`` after reporting and optionally writing the migration result.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_bool_env("DRY_RUN", default=True)
    pipeline_directory = get_pipeline_data_dir()
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(pipeline_directory / "vault-metadata-db.pickle"))).expanduser()
    if not vault_db_path.exists():
        raise FileNotFoundError(vault_db_path)

    lock = nullcontext() if dry_run else wait_other_writers(vault_db_path.parent / "scan-pipeline", timeout=60)
    with lock:
        logger.info("Reading Flying Tulip metadata rows from %s", vault_db_path)
        vault_db = VaultDatabase.read(vault_db_path)
        result = migrate_flying_tulip_descriptions(vault_db, dry_run=dry_run)

        report_rows = [
            {
                "Chain": update.spec.chain_id,
                "sftUSD": update.spec.vault_address,
                "Changed fields": ", ".join(update.changed_fields),
            }
            for update in result.updates
        ]
        if report_rows:
            print(tabulate(report_rows, headers="keys", tablefmt="rounded_outline"))

        if not dry_run and result.updated_rows:
            vault_db.write(vault_db_path)

    if dry_run:
        outcome = "Dry run; no files changed"
    elif result.updated_rows:
        outcome = f"Written atomically to {vault_db_path}"
    else:
        outcome = "No changes required; metadata pickle not rewritten"
    logger.info(
        "Flying Tulip description migration: inspected=%d, updated rows=%d, updated fields=%d. %s.",
        result.inspected_rows,
        result.updated_rows,
        result.updated_fields,
        outcome,
    )


if __name__ == "__main__":
    main()
