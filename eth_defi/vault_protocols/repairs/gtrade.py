#!/usr/bin/env python3
"""Refresh the 24 reviewed Gains-family deployments without network reads.

Numbered migrations call the in-memory transformation helpers in this module.
The old filesystem wrappers remain for regression compatibility; production
uses ``vault-migrate`` through ``scripts/run-vault-migrations.sh`` as described
in ``.claude/docs/migration-scripts.md``. The former standalone entrypoint is
retired. Helpers used by numbered scripts must not acquire locks or write files.
"""

import logging
import os
import shutil
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gains.metadata import REVIEWED_GAINS_VAULTS
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class GtradeMetadataUpdate:
    """One exact deployment and the metadata fields requiring a refresh."""

    #: Reviewed deployment identity.
    spec: VaultSpec
    #: Fields whose persisted values differ from the adapter's current output.
    changed_fields: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class GtradeMetadataMigrationResult:
    """Auditable outcome of a dry run or persistent metadata refresh."""

    #: Number of reviewed deployments present in the input database.
    inspected_rows: int
    #: Reviewed historical deployments absent from this database.
    missing: tuple[VaultSpec, ...]
    #: Proposed or applied changes, with one entry per changed row.
    updates: tuple[GtradeMetadataUpdate, ...]
    #: Byte-for-byte backup taken before an atomic replacement, if needed.
    backup_path: Path | None


def _backup_metadata(path: Path) -> Path:
    """Back up a metadata pickle without overwriting an earlier backup.

    Exclusive file creation protects both repeated runs and existing backups.

    :param path: Existing metadata database.
    :return: New sibling backup path.
    """
    index = 0
    while True:
        suffix = f".{index}" if index else ""
        backup_path = Path(f"{path}.bak-gtrade-metadata{suffix}")
        try:
            backup_file = backup_path.open("xb")
        except FileExistsError:
            index += 1
            continue
        with backup_file, path.open("rb") as source:
            shutil.copyfileobj(source, backup_file)
        return backup_path


def migrate_gtrade_metadata(path: Path, *, dry_run: bool = True) -> GtradeMetadataMigrationResult:
    """Refresh reviewed rows using the same metadata path as normal scanning.

    Validate every present target before changing any row. Persistent runs
    acquire the shared pipeline lock, back up the original pickle, and replace
    it atomically. Dry runs only compare; an unchanged rerun makes no writes.

    :param path: Existing vault metadata pickle; tests may use an isolated copy.
    :param dry_run: Report differences without writing when ``True``.
    :return: Present, missing and changed deployments, plus an optional backup.
    :raises ValueError: If no reviewed rows exist or a target has a conflicting
        protocol, address, chain or detection record.
    """
    if not path.is_file():
        raise FileNotFoundError(path)
    lock = nullcontext() if dry_run else wait_other_writers(path.parent / "scan-pipeline", timeout=60)
    with lock:
        database = VaultDatabase.read(path)
        result = transform_gtrade(database, dry_run=dry_run)
        if result.updates and not dry_run:
            backup_path = _backup_metadata(path)
            database.write(path)
            result = GtradeMetadataMigrationResult(result.inspected_rows, result.missing, result.updates, backup_path)
        return result


def main() -> None:
    """Run and report the fixed-scope metadata-only repair.

    Configuration comes from environment variables, with dry run as the default.

    :return: ``None`` after reporting the proposed or applied changes.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    raw_dry_run = os.environ.get("DRY_RUN", "true").strip().lower()
    if raw_dry_run not in {"true", "false"}:
        message = "DRY_RUN must be true or false"
        raise ValueError(message)
    path = Path(os.environ.get("VAULT_DB_PATH", str(get_pipeline_data_dir() / "vault-metadata-db.pickle"))).expanduser()
    result = migrate_gtrade_metadata(path, dry_run=raw_dry_run == "true")
    if result.updates:
        print(
            tabulate(
                [(update.spec.chain_id, update.spec.vault_address, ", ".join(update.changed_fields)) for update in result.updates],
                headers=("Chain", "Vault", "Changed fields"),
                tablefmt="rounded_outline",
            )
        )
    logger.info("Reviewed=%d, present=%d, absent=%d, changed=%d, dry run=%s", len(REVIEWED_GAINS_VAULTS), result.inspected_rows, len(result.missing), len(result.updates), raw_dry_run)


def transform_gtrade(database: VaultDatabase, *, dry_run: bool = False, targets: tuple[VaultSpec, ...] | None = None) -> GtradeMetadataMigrationResult:
    """Stage the reviewed repair without filesystem access or writer locks.

    The caller owns persistence and backups. Validate all present targets before
    changing rows, retaining discovery boundaries and unrelated fields.

    :param database: Shared staged metadata database.
    :param dry_run: Calculate changes without changing rows when true.
    :param targets: Frozen migration inventory, or the maintained default.
    :return: Changed fields and missing rows, without a backup path.
    """
    specs = targets if targets is not None else tuple(VaultSpec(chain_id, address) for chain_id, address in REVIEWED_GAINS_VAULTS)
    present = tuple(spec for spec in specs if spec in database.rows)
    missing = tuple(spec for spec in specs if spec not in database.rows)
    if not present:
        message = "No reviewed Gains-family deployments found; check VAULT_DB_PATH"
        raise ValueError(message)
    desired_rows: dict[VaultSpec, dict[str, object]] = {}
    for spec in present:
        row = database.rows[spec]
        if row.get("Protocol") not in {"Gains Network", "gTrade"}:
            raise ValueError(f"Unexpected protocol for {spec.as_string_id()}: {row.get('Protocol')!r}")
        if row.get("Address", spec.vault_address).lower() != spec.vault_address.lower():
            raise ValueError(f"Conflicting row address for {spec.as_string_id()}")
        detection = row.get("_detection_data")
        if detection is not None:
            if not isinstance(detection, ERC4262VaultDetection):
                raise ValueError(f"Unexpected detection record for {spec.as_string_id()}")
            if detection.chain != spec.chain_id or detection.address.lower() != spec.vault_address.lower() or ERC4626Feature.gains_like not in detection.features:
                raise ValueError(f"Conflicting Gains detection for {spec.as_string_id()}")
            if detection.features & {ERC4626Feature.ostium_like, ERC4626Feature.domination_finance_like}:
                raise ValueError(f"Different Gains-family protocol for {spec.as_string_id()}")
        adapter = GainsVault(web3=None, spec=spec, features={ERC4626Feature.gains_like})
        desired_rows[spec] = {
            "Link": adapter.get_link(),
            "_notes": adapter.get_notes(),
            "_strategy_tags": adapter.get_strategy_tags(),
            "_curator_slug": REVIEWED_GAINS_VAULTS[spec.chain_id, spec.vault_address.lower()].curator,
        }

    updates = tuple(GtradeMetadataUpdate(spec, tuple(key for key, value in values.items() if key not in database.rows[spec] or database.rows[spec][key] != value)) for spec, values in desired_rows.items())
    updates = tuple(update for update in updates if update.changed_fields)
    for update in updates:
        logger.info("%s: %s", update.spec.as_string_id(), ", ".join(update.changed_fields))
    for spec in missing:
        logger.warning("Reviewed deployment absent; no row created: %s", spec.as_string_id())

    if not dry_run:
        for update in updates:
            database.rows[update.spec].update({key: desired_rows[update.spec][key] for key in update.changed_fields})
    return GtradeMetadataMigrationResult(len(present), missing, updates, None)
