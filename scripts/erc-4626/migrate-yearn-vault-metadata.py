#!/usr/bin/env python3
"""Repair persisted Yearn links and yDaemon endorsement classification.

The normal scanner now uses `Yearn's yDaemon metadata
<https://github.com/yearn/ydaemon/tree/main/data/meta/vaults>`__ to distinguish
unendorsed Yearn V3-compatible contracts from official Yearn and
Yearn-endorsed partner vaults. Existing rows need a metadata-only repair so
their links, ``_flags`` and ``_notes`` match the current adapter behaviour.

The fixed scope is persisted Yearn V3, TokenizedStrategy, compounder, and
Morpho compounder rows. The script makes no RPC calls and does not modify
price Parquet files, reader state, lead state, timestamp caches, or historical
context. Dry runs use a temporary yDaemon cache and make no persistent changes.

Usage::

    DRY_RUN=true poetry run python scripts/erc-4626/migrate-yearn-vault-metadata.py
    DRY_RUN=false poetry run python scripts/erc-4626/migrate-yearn-vault-metadata.py

Environment variables:

- ``DRY_RUN``: Report changes without writing. Defaults to ``true``.
- ``VAULT_DB_PATH``: Optional metadata pickle path. Defaults to
  ``$PIPELINE_DATA_DIR/vault-metadata-db.pickle``.
- ``LOG_LEVEL``: Console log level. Defaults to ``info``.
"""

import logging
import os
import shutil
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from tabulate import tabulate

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yearn.offchain_metadata import YearnVaultMetadata, fetch_yearn_vaults_file_for_chain
from eth_defi.erc_4626.vault_protocol.yearn.vault import create_yearn_vault_link
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import NOT_IN_YEARN_FRONTEND, VaultFlag, get_vault_special_flags
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

logger = logging.getLogger(__name__)

#: Persisted scanner protocol name for the fixed Yearn migration scope.
YEARN_PROTOCOL_NAME = "Yearn"

#: Adapter feature families whose current Yearn adapters own links and endorsement flags.
YEARN_METADATA_FEATURES = frozenset(
    {
        ERC4626Feature.yearn_v3_like,
        ERC4626Feature.yearn_tokenised_strategy,
        ERC4626Feature.yearn_compounder_like,
        ERC4626Feature.yearn_morpho_compounder_like,
    }
)

#: Non-overwriting suffix for the pre-write metadata-pickle backup.
BACKUP_SUFFIX = ".bak-yearn-vault-metadata"


@dataclass(slots=True, frozen=True)
class YearnVaultMetadataUpdate:
    """Describe one persisted Yearn metadata row changed by the migration.

    :param spec:
        Persisted vault identity.
    :param endorsed:
        yDaemon endorsement decision, or ``None`` when metadata was unavailable.
    :param changed_fields:
        Persisted fields whose values differ from current adapter behaviour.
    """

    #: Persisted vault identity.
    spec: VaultSpec

    #: yDaemon endorsement decision, or ``None`` when metadata was unavailable.
    endorsed: bool | None

    #: Persisted fields whose values differ from current adapter behaviour.
    changed_fields: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class YearnVaultMetadataMigrationResult:
    """Summarise one Yearn metadata migration run.

    :param inspected_rows:
        Number of persisted rows in the fixed adapter scope.
    :param updated_rows:
        Number of rows changed or proposed for change.
    :param unavailable_metadata_rows:
        Target rows whose chain yDaemon metadata could not be loaded.
    :param skipped_rows:
        Persisted Yearn rows outside the adapter scope.
    :param updates:
        Address-scoped update details.
    """

    #: Number of persisted rows in the fixed adapter scope.
    inspected_rows: int

    #: Number of rows changed or proposed for change.
    updated_rows: int

    #: Target rows whose chain yDaemon metadata could not be loaded.
    unavailable_metadata_rows: int

    #: Persisted Yearn rows outside the adapter scope.
    skipped_rows: int

    #: Address-scoped update details.
    updates: tuple[YearnVaultMetadataUpdate, ...]


@dataclass(slots=True, frozen=True)
class _YearnVaultMetadataPlan:
    """Hold one calculated row update before its optional mutation."""

    #: Public migration report for this row.
    update: YearnVaultMetadataUpdate

    #: Flags to persist when the plan is applied.
    flags: set[VaultFlag]

    #: Human-readable note to persist when the plan is applied.
    notes: str | None

    #: Canonical Yearn frontend link to persist when the plan is applied.
    link: str


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse one strict boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Value used when the variable is absent.
    :return:
        Parsed boolean value.
    :raises ValueError:
        If the environment value is not a recognised boolean literal.
    """

    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() in {"1", "true", "yes"}:
        return True
    if value.lower() in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true or false, got {value!r}")


def _get_row_features(row: VaultRow) -> set[ERC4626Feature]:
    """Extract persisted feature flags used to select the migration scope.

    :param row:
        Persisted vault metadata row.
    :return:
        Persisted scanner feature flags, or an empty set for legacy rows.
    """

    raw_features = row.get("features")
    if isinstance(raw_features, (set, frozenset)):
        return set(raw_features)
    detection = row.get("_detection_data")
    detected_features = getattr(detection, "features", None)
    if isinstance(detected_features, (set, frozenset)):
        return set(detected_features)
    return set()


def _is_yearn_metadata_row(row: VaultRow) -> bool:
    """Check whether one row is owned by the revised Yearn adapters.

    :param row:
        Persisted vault metadata row.
    :return:
        ``True`` when the row is in the fixed Yearn adapter scope.
    """

    return row.get("Protocol") == YEARN_PROTOCOL_NAME and bool(_get_row_features(row) & YEARN_METADATA_FEATURES)


def fetch_yearn_metadata_by_chain(chain_ids: set[int], cache_path: Path) -> dict[int, dict[str, YearnVaultMetadata] | None]:
    """Fetch one temporary yDaemon metadata index per relevant chain.

    :param chain_ids:
        Chain IDs with persisted Yearn rows in migration scope.
    :param cache_path:
        Temporary per-run yDaemon cache directory.
    :return:
        Chain IDs mapped to their metadata index, or ``None`` when unavailable.
    """

    metadata_by_chain: dict[int, dict[str, YearnVaultMetadata] | None] = {}
    for chain_id in sorted(chain_ids):
        metadata_by_chain[chain_id] = fetch_yearn_vaults_file_for_chain(chain_id, cache_path=cache_path)
    return metadata_by_chain


def _get_non_overwriting_backup_path(vault_db_path: Path) -> Path:
    """Choose a unique backup path beside the metadata pickle.

    :param vault_db_path:
        Production metadata pickle about to be replaced atomically.
    :return:
        Unused backup path that does not overwrite a prior migration backup.
    """

    candidate = vault_db_path.with_name(f"{vault_db_path.name}{BACKUP_SUFFIX}")
    suffix = 2
    while candidate.exists():
        candidate = vault_db_path.with_name(f"{vault_db_path.name}{BACKUP_SUFFIX}-{suffix}")
        suffix += 1
    return candidate


def _plan_yearn_vault_metadata_update(
    spec: VaultSpec,
    row: VaultRow,
    index: dict[str, YearnVaultMetadata] | None,
) -> _YearnVaultMetadataPlan:
    """Calculate one Yearn row's current adapter-owned metadata fields.

    :param spec:
        Persisted vault identity.
    :param row:
        Persisted vault metadata row in the fixed migration scope.
    :param index:
        yDaemon chain index, or ``None`` when temporarily unavailable.
    :return:
        Complete non-mutating row-update plan.
    :raises ValueError:
        If the persisted flags are malformed.
    """

    raw_flags = row.get("_flags", set())
    if not isinstance(raw_flags, (set, frozenset)):
        raise ValueError(f"Yearn row {spec.as_string_id()} has malformed _flags: {raw_flags!r}")
    old_flags = set(raw_flags)
    new_flags = set(old_flags)
    old_notes = row.get("_notes")
    new_notes = old_notes
    new_link = create_yearn_vault_link(spec.chain_id, spec.vault_address)
    endorsed: bool | None = None

    if index is not None:
        metadata = index.get(spec.vault_address.lower())
        endorsed = metadata.endorsed if metadata is not None else False
        manually_unofficial = VaultFlag.unofficial in get_vault_special_flags(spec.vault_address, protocol_name=YEARN_PROTOCOL_NAME)
        if endorsed and not manually_unofficial:
            new_flags.discard(VaultFlag.unofficial)
            if old_notes == NOT_IN_YEARN_FRONTEND:
                new_notes = None
        elif not endorsed and not manually_unofficial:
            new_flags.add(VaultFlag.unofficial)
            if new_notes is None:
                new_notes = NOT_IN_YEARN_FRONTEND

    changed_fields = tuple(
        field
        for field, old_value, new_value in (
            ("Link", row.get("Link"), new_link),
            ("_flags", old_flags, new_flags),
            ("_notes", old_notes, new_notes),
        )
        if old_value != new_value
    )
    return _YearnVaultMetadataPlan(
        update=YearnVaultMetadataUpdate(spec=spec, endorsed=endorsed, changed_fields=changed_fields),
        flags=new_flags,
        notes=new_notes,
        link=new_link,
    )


def migrate_yearn_vault_metadata(
    vault_db: VaultDatabase,
    metadata_by_chain: dict[int, dict[str, YearnVaultMetadata] | None],
    *,
    dry_run: bool,
) -> YearnVaultMetadataMigrationResult:
    """Apply current Yearn metadata fields to persisted adapter rows.

    Explicitly unendorsed rows gain ``VaultFlag.unofficial`` and its note;
    Yearn-endorsed rows lose only the dynamically managed flag and note. Manual
    address flags are never removed. Every selected row receives the current
    deterministic Yearn frontend link even when its yDaemon metadata is
    temporarily unavailable.

    :param vault_db:
        Existing vault metadata database loaded from the production pickle.
    :param metadata_by_chain:
        Temporary yDaemon indices keyed by relevant chain ID.
    :param dry_run:
        Report changes without mutating ``vault_db`` when ``True``.
    :return:
        Target, change, unavailable-source, and skipped-row counts.
    :raises ValueError:
        If a target row has malformed persisted flags.
    """

    updates: list[YearnVaultMetadataUpdate] = []
    inspected_rows = 0
    unavailable_metadata_rows = 0
    skipped_rows = 0

    for spec, row in vault_db.rows.items():
        if row.get("Protocol") != YEARN_PROTOCOL_NAME:
            continue
        if not _is_yearn_metadata_row(row):
            skipped_rows += 1
            continue

        inspected_rows += 1
        index = metadata_by_chain.get(spec.chain_id)
        if index is None:
            unavailable_metadata_rows += 1
        plan = _plan_yearn_vault_metadata_update(spec, row, index)
        if not plan.update.changed_fields:
            continue

        updates.append(plan.update)
        if not dry_run:
            row["Link"] = plan.link
            row["_flags"] = plan.flags
            row["_notes"] = plan.notes

    return YearnVaultMetadataMigrationResult(
        inspected_rows=inspected_rows,
        updated_rows=len(updates),
        unavailable_metadata_rows=unavailable_metadata_rows,
        skipped_rows=skipped_rows,
        updates=tuple(updates),
    )


def main() -> None:
    """Run the metadata-only Yearn migration from environment variables.

    Persistent mode takes the shared scanner writer lock, creates a
    non-overwriting pickle backup, and atomically replaces only the metadata
    pickle. Both modes use an isolated temporary yDaemon cache, so dry runs do
    not persist network data or change scanner state.

    :return:
        ``None`` after reporting and optionally writing the migration result.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_bool_env("DRY_RUN", default=True)
    pipeline_data_dir = get_pipeline_data_dir()
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(pipeline_data_dir / "vault-metadata-db.pickle"))).expanduser()
    if not vault_db_path.exists():
        raise FileNotFoundError(vault_db_path)

    lock = nullcontext() if dry_run else wait_other_writers(vault_db_path.parent / "scan-pipeline", timeout=60)
    with lock:
        logger.info("Reading Yearn vault metadata from %s", vault_db_path)
        vault_db = VaultDatabase.read(vault_db_path)
        chain_ids = {spec.chain_id for spec, row in vault_db.rows.items() if _is_yearn_metadata_row(row)}
        with TemporaryDirectory(prefix="yearn-ydaemon-") as temporary_directory:
            metadata_by_chain = fetch_yearn_metadata_by_chain(chain_ids, Path(temporary_directory))
        result = migrate_yearn_vault_metadata(vault_db, metadata_by_chain, dry_run=dry_run)

        report_rows = [
            {
                "Chain": update.spec.chain_id,
                "Vault": update.spec.vault_address,
                "Endorsed": update.endorsed,
                "Changed fields": ", ".join(update.changed_fields),
            }
            for update in result.updates
        ]
        if report_rows:
            print(tabulate(report_rows, headers="keys", tablefmt="rounded_outline"))

        if not dry_run and result.updated_rows:
            backup_path = _get_non_overwriting_backup_path(vault_db_path)
            shutil.copy2(vault_db_path, backup_path)
            vault_db.write(vault_db_path)
            logger.info("Created metadata backup at %s", backup_path)

    if dry_run:
        outcome = "Dry run; no persistent files changed"
    elif result.updated_rows:
        outcome = f"Created a backup and atomically wrote {vault_db_path}"
    else:
        outcome = "No changes required; metadata pickle not rewritten"
    logger.info(
        "Yearn metadata migration: inspected=%d, updated=%d, unavailable metadata=%d, skipped=%d. %s.",
        result.inspected_rows,
        result.updated_rows,
        result.unavailable_metadata_rows,
        result.skipped_rows,
        outcome,
    )


if __name__ == "__main__":
    main()
