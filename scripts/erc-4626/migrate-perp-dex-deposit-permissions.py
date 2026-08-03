#!/usr/bin/env python3
"""Migrate cached native perp DEX deposit-permission metadata.

Native perp DEX source databases retain the public deposit state used by their
scanners. Vault metadata written before the current export mapping may therefore
have a missing or stale ``_deposit_permission`` and ``_whitelist_notes`` value.
This metadata-only migration reads those DuckDB sources in read-only mode and
updates only the two cached export fields.

The command starts in dry-run mode. Inspect the summary, then apply it:

.. code-block:: shell

    poetry run python scripts/erc-4626/migrate-perp-dex-deposit-permissions.py
    DRY_RUN=false poetry run python scripts/erc-4626/migrate-perp-dex-deposit-permissions.py

The migration does not scan APIs, alter price Parquet files, or write to source
DuckDB databases. A non-overwriting backup is created before an apply run.

Environment variables:

``DRY_RUN``
    Print proposed changes without writing. Defaults to ``true``.

``VAULT_DB_PATH``
    Vault metadata pickle. Defaults to the active pipeline database.

``BACKUP_PATH``
    Optional backup path. Defaults to a non-overwriting sibling path.

``HYPERLIQUID_DB_PATH``
    Hyperliquid DuckDB. Defaults to the high-frequency database when present,
    otherwise the daily database.

``LIGHTER_DB_PATH``, ``GRVT_DB_PATH``, ``APEX_DB_PATH``
    Optional source database overrides for the respective integrations.

``LOG_LEVEL``
    Python logging level. Defaults to ``info``.
"""

import json
import logging
import os
import shutil
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import duckdb
from tabulate import tabulate

from eth_defi.apex.constants import APEX_METRICS_DATABASE
from eth_defi.apex.vault_data_export import create_apex_vault_row
from eth_defi.grvt.constants import GRVT_DAILY_METRICS_DATABASE
from eth_defi.grvt.vault_data_export import create_grvt_vault_row
from eth_defi.hyperliquid.constants import HYPERLIQUID_DAILY_METRICS_DATABASE, HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE
from eth_defi.hyperliquid.vault_data_export import classify_hyperliquid_vault_deposit_access, create_hyperliquid_vault_row
from eth_defi.lighter.constants import LIGHTER_DAILY_METRICS_DATABASE, LIGHTER_DEPLOYMENTS_BY_SLUG
from eth_defi.lighter.vault_data_export import create_lighter_pool_row
from eth_defi.perp_dex.vault import PerpVaultDepositAccess, classify_perp_vault_deposit_access
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultDepositPermission
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

PERP_DEX_PROTOCOLS = ("Hyperliquid", "Lighter", "GRVT", "Hibachi", "ApeX")


@dataclass(slots=True, frozen=True)
class PerpDexPermissionUpdate:
    """One native perp DEX metadata row requiring migration."""

    #: Shared vault metadata identity.
    spec: VaultSpec

    #: Protocol label.
    protocol: str

    #: Permission before migration.
    old_access: PerpVaultDepositAccess

    #: Source-derived permission after migration.
    new_access: PerpVaultDepositAccess


@dataclass(slots=True, frozen=True)
class PerpDexPermissionMigrationResult:
    """Summary of one native perp DEX permission migration."""

    #: Number of native perp DEX rows inspected.
    inspected_rows: int

    #: Rows updated or proposed for update.
    updates: tuple[PerpDexPermissionUpdate, ...]

    #: Rows absent from their required source database.
    unresolved_rows: int

    #: Backup created before an apply, when applicable.
    backup_path: Path | None


@dataclass(slots=True, frozen=True)
class PerpDexSourcePaths:
    """DuckDB sources needed to reconstruct native perp DEX access state."""

    #: Hyperliquid daily or high-frequency database.
    hyperliquid: Path

    #: Shared deployment-aware Lighter database.
    lighter: Path

    #: GRVT daily metrics database.
    grvt: Path

    #: ApeX metrics database.
    apex: Path


def parse_boolean_env(value: str | None, *, default: bool) -> bool:
    """Parse a strict boolean environment value.

    :param value:
        Environment value, or ``None`` when unset.
    :param default:
        Value used when the variable is unset.
    :return:
        Parsed boolean value.
    :raises ValueError:
        If a configured value is not recognised.
    """
    if value is None:
        return default
    normalised = value.strip().casefold()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean environment value, got {value!r}")


def resolve_source_paths() -> PerpDexSourcePaths:
    """Resolve native perp DEX source databases from the environment.

    :return:
        Source paths used for read-only status reconstruction.
    """
    configured_hyperliquid = os.environ.get("HYPERLIQUID_DB_PATH")
    if configured_hyperliquid:
        hyperliquid_path = Path(configured_hyperliquid).expanduser()
    elif HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE.exists():
        hyperliquid_path = HYPERLIQUID_HIGH_FREQ_METRICS_DATABASE
    else:
        hyperliquid_path = HYPERLIQUID_DAILY_METRICS_DATABASE

    return PerpDexSourcePaths(
        hyperliquid=hyperliquid_path,
        lighter=Path(os.environ.get("LIGHTER_DB_PATH", str(LIGHTER_DAILY_METRICS_DATABASE))).expanduser(),
        grvt=Path(os.environ.get("GRVT_DB_PATH", str(GRVT_DAILY_METRICS_DATABASE))).expanduser(),
        apex=Path(os.environ.get("APEX_DB_PATH", str(APEX_METRICS_DATABASE))).expanduser(),
    )


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose a non-overwriting backup path for the metadata pickle.

    :param vault_db_path:
        Existing metadata database to protect.
    :return:
        Unused sibling or explicitly configured backup path.
    :raises FileExistsError:
        If an explicit ``BACKUP_PATH`` already exists.
    """
    configured_path = os.environ.get("BACKUP_PATH")
    if configured_path:
        backup_path = Path(configured_path).expanduser()
        if backup_path.exists():
            raise FileExistsError(f"Refusing to overwrite existing backup: {backup_path}")
        return backup_path

    base_path = vault_db_path.with_name(f"{vault_db_path.name}.before-perp-dex-deposit-permission-migration")
    backup_path = base_path
    suffix = 1
    while backup_path.exists():
        backup_path = base_path.with_name(f"{base_path.name}.{suffix}")
        suffix += 1
    return backup_path


def _row_access(row: VaultRow) -> PerpVaultDepositAccess:
    """Read normalised access fields from one shared metadata row.

    :param row:
        Shared vault metadata row.
    :return:
        Persisted permission and qualification.
    """
    raw_permission = row.get("_deposit_permission", VaultDepositPermission.unknown.value)
    try:
        permission = VaultDepositPermission(raw_permission)
    except (TypeError, ValueError):
        permission = VaultDepositPermission.unknown
    whitelist_notes = row.get("_whitelist_notes")
    return PerpVaultDepositAccess(permission=permission, whitelist_notes=whitelist_notes if isinstance(whitelist_notes, str) else None)


def _fetch_rows(connection: duckdb.DuckDBPyConnection, query: str) -> Iterator[dict[str, object]]:
    """Fetch DuckDB rows as column-keyed dictionaries.

    :param connection:
        Read-only DuckDB connection.
    :param query:
        Static internal query for a source metadata table.
    :return:
        Result rows keyed by their source column names.
    """
    cursor = connection.execute(query)
    column_names = [str(column[0]) for column in cursor.description]
    return (dict(zip(column_names, values, strict=True)) for values in cursor.fetchall())


def _parse_optional_bool(value: object) -> bool | None:
    """Parse an optional boolean from a DuckDB or JSON value.

    :param value:
        Boolean, common string representation, or missing value.
    :return:
        Parsed boolean, or ``None`` when the source is inconclusive.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalised = value.strip().casefold()
        if normalised == "true":
            return True
        if normalised == "false":
            return False
    return None


def fetch_hyperliquid_access(path: Path) -> dict[VaultSpec, PerpVaultDepositAccess]:
    """Read Hyperliquid public deposit state from its metadata table.

    :param path:
        Hyperliquid daily or high-frequency DuckDB path.
    :return:
        Source-backed access metadata by shared vault identity.
    """
    with closing(duckdb.connect(str(path), read_only=True)) as connection:
        records = _fetch_rows(connection, "SELECT * FROM vault_metadata")

    access_by_spec: dict[VaultSpec, PerpVaultDepositAccess] = {}
    for record in records:
        spec, row = create_hyperliquid_vault_row(
            vault_address=str(record["vault_address"]),
            name="",
            description=None,
            tvl=0.0,
            create_time=None,
            is_closed=_parse_optional_bool(record.get("is_closed")),
            allow_deposits=_parse_optional_bool(record.get("allow_deposits")),
            relationship_type=str(record.get("relationship_type") or "normal"),
        )
        access_by_spec[spec] = _row_access(row)
    return access_by_spec


def fetch_lighter_access(path: Path) -> dict[VaultSpec, PerpVaultDepositAccess]:
    """Read Lighter public pool status from its metadata table.

    :param path:
        Deployment-aware Lighter DuckDB path.
    :return:
        Source-backed access metadata by shared vault identity.
    """
    with closing(duckdb.connect(str(path), read_only=True)) as connection:
        records = _fetch_rows(connection, "SELECT * FROM pool_metadata")

    access_by_spec: dict[VaultSpec, PerpVaultDepositAccess] = {}
    for record in records:
        deployment_slug = str(record.get("deployment") or "ethereum")
        try:
            deployment_config = LIGHTER_DEPLOYMENTS_BY_SLUG[deployment_slug]
        except KeyError as error:
            raise ValueError(f"Unknown Lighter deployment in {path}: {deployment_slug}") from error
        status = record.get("status")
        spec, row = create_lighter_pool_row(
            account_index=int(record["account_index"]),
            name="",
            description=None,
            tvl=0.0,
            created_at=None,
            status=None if status is None else int(status),
            deployment=deployment_config,
        )
        access_by_spec[spec] = _row_access(row)
    return access_by_spec


def fetch_grvt_access(path: Path) -> dict[VaultSpec, PerpVaultDepositAccess]:
    """Read GRVT discoverability and lifecycle status read-only.

    Older databases may carry these values only inside
    ``extended_vault_info``. The migration applies the same tolerant JSON
    recovery as the current schema migration without writing first-class
    columns.

    :param path:
        GRVT DuckDB path.
    :return:
        Source-backed access metadata by shared vault identity.
    """
    with closing(duckdb.connect(str(path), read_only=True)) as connection:
        records = _fetch_rows(connection, "SELECT * FROM vault_metadata")

    access_by_spec: dict[VaultSpec, PerpVaultDepositAccess] = {}
    for record in records:
        extended_vault_info: dict[str, object] = {}
        raw_extended_vault_info = record.get("extended_vault_info")
        if isinstance(raw_extended_vault_info, str):
            try:
                parsed_extended_vault_info = json.loads(raw_extended_vault_info)
            except json.JSONDecodeError:
                logger.warning("Could not parse GRVT extended_vault_info for vault %s", record["vault_id"])
            else:
                if isinstance(parsed_extended_vault_info, dict):
                    extended_vault_info = parsed_extended_vault_info

        discoverable = _parse_optional_bool(record.get("discoverable"))
        if discoverable is None:
            discoverable = _parse_optional_bool(extended_vault_info.get("discoverable"))
        status = record.get("status") or extended_vault_info.get("status")
        spec, row = create_grvt_vault_row(
            vault_id=str(record["vault_id"]),
            chain_vault_id=int(record["chain_vault_id"]),
            name="",
            description=None,
            tvl=0.0,
            discoverable=discoverable,
            status=None if status is None else str(status),
        )
        access_by_spec[spec] = _row_access(row)
    return access_by_spec


def fetch_apex_access(path: Path) -> dict[VaultSpec, PerpVaultDepositAccess]:
    """Read ApeX lifecycle statuses from its metadata database.

    :param path:
        ApeX DuckDB path.
    :return:
        Source-backed access metadata by shared vault identity.
    """
    with closing(duckdb.connect(str(path), read_only=True)) as connection:
        records = _fetch_rows(connection, "SELECT * FROM vault_metadata")

    access_by_spec: dict[VaultSpec, PerpVaultDepositAccess] = {}
    for record in records:
        spec, row = create_apex_vault_row(
            vault_id=str(record["vault_id"]),
            name="",
            description=None,
            tvl=None,
            share_count=None,
            created_at=None,
            first_seen=record.get("first_seen"),
            status=str(record["status"]),
        )
        access_by_spec[spec] = _row_access(row)
    return access_by_spec


def build_permission_updates(
    vault_db: VaultDatabase,
    source_paths: PerpDexSourcePaths,
) -> tuple[list[PerpDexPermissionUpdate], int]:
    """Compare cached permissions with current read-only source state.

    Hyperliquid rows retained after disappearing from the current source table
    are classified from their last cached closure reason. Other protocols keep
    their existing value when the source database has no matching identity.

    :param vault_db:
        Shared metadata database loaded in memory.
    :param source_paths:
        Native perp DEX DuckDB sources.
    :return:
        Required updates and unresolved source-row count.
    :raises FileNotFoundError:
        If a source database required by existing protocol rows is missing.
    """
    protocol_rows = Counter(str(row.get("Protocol")) for row in vault_db.rows.values())
    required_sources = {
        "Hyperliquid": source_paths.hyperliquid,
        "Lighter": source_paths.lighter,
        "GRVT": source_paths.grvt,
        "ApeX": source_paths.apex,
    }
    for protocol, path in required_sources.items():
        if protocol_rows[protocol] and not path.exists():
            raise FileNotFoundError(f"{protocol} source database does not exist: {path}")

    source_access: dict[VaultSpec, PerpVaultDepositAccess] = {}
    if protocol_rows["Hyperliquid"]:
        source_access.update(fetch_hyperliquid_access(source_paths.hyperliquid))
    if protocol_rows["Lighter"]:
        source_access.update(fetch_lighter_access(source_paths.lighter))
    if protocol_rows["GRVT"]:
        source_access.update(fetch_grvt_access(source_paths.grvt))
    if protocol_rows["ApeX"]:
        source_access.update(fetch_apex_access(source_paths.apex))

    updates: list[PerpDexPermissionUpdate] = []
    unresolved_rows = 0
    for spec, row in vault_db.rows.items():
        protocol = str(row.get("Protocol"))
        if protocol not in PERP_DEX_PROTOCOLS:
            continue

        if protocol == "Hibachi":
            new_access = classify_perp_vault_deposit_access(public_deposits_open=None)
        elif spec in source_access:
            new_access = source_access[spec]
        elif protocol == "Hyperliquid":
            new_access = classify_hyperliquid_vault_deposit_access(row.get("_deposit_closed_reason"))
        else:
            unresolved_rows += 1
            continue

        old_access = _row_access(row)
        if old_access == new_access:
            continue
        updates.append(
            PerpDexPermissionUpdate(
                spec=spec,
                protocol=protocol,
                old_access=old_access,
                new_access=new_access,
            )
        )

    return updates, unresolved_rows


def apply_permission_updates(vault_db: VaultDatabase, updates: tuple[PerpDexPermissionUpdate, ...]) -> None:
    """Apply only permission and qualification fields in memory.

    :param vault_db:
        Shared metadata database to mutate.
    :param updates:
        Reviewed source-derived changes.
    :return:
        None after updating selected rows.
    """
    for update in updates:
        row = vault_db.rows[update.spec].copy()
        row["_deposit_permission"] = update.new_access.permission.value
        row["_whitelist_notes"] = update.new_access.whitelist_notes
        vault_db.rows[update.spec] = row


def migrate_perp_dex_deposit_permissions(
    vault_db_path: Path,
    source_paths: PerpDexSourcePaths,
    *,
    dry_run: bool,
) -> PerpDexPermissionMigrationResult:
    """Migrate native perp DEX permission fields with dry-run safety.

    :param vault_db_path:
        Existing shared metadata pickle.
    :param source_paths:
        Read-only native perp DEX status databases.
    :param dry_run:
        Report without backup or write when ``True``.
    :return:
        Migration counts and optional backup path.
    :raises RuntimeError:
        If an apply run cannot resolve every native perp DEX row from its
        required source database.
    """
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault metadata database does not exist: {vault_db_path}")

    vault_db = VaultDatabase.read(vault_db_path)
    updates, unresolved_rows = build_permission_updates(vault_db, source_paths)
    updates_tuple = tuple(updates)
    inspected_rows = sum(1 for row in vault_db.rows.values() if row.get("Protocol") in PERP_DEX_PROTOCOLS)
    if dry_run:
        return PerpDexPermissionMigrationResult(inspected_rows, updates_tuple, unresolved_rows, None)
    if unresolved_rows:
        raise RuntimeError(f"Refusing partial migration with {unresolved_rows:,} unresolved native perp DEX rows")
    if not updates_tuple:
        return PerpDexPermissionMigrationResult(inspected_rows, updates_tuple, unresolved_rows, None)

    backup_path = create_backup_path(vault_db_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vault_db_path, backup_path)
    apply_permission_updates(vault_db, updates_tuple)
    vault_db.write(vault_db_path)
    return PerpDexPermissionMigrationResult(inspected_rows, updates_tuple, unresolved_rows, backup_path)


def print_migration_summary(vault_db_path: Path, result: PerpDexPermissionMigrationResult, *, dry_run: bool) -> None:
    """Print protocol-level status counts and migration totals.

    :param vault_db_path:
        Metadata pickle inspected by the migration.
    :param result:
        Completed migration result.
    :param dry_run:
        Whether no writes were requested.
    :return:
        None after rendering operator output.
    """
    vault_db = VaultDatabase.read(vault_db_path)
    projected_access = {update.spec: update.new_access for update in result.updates}
    summary = []
    for protocol in PERP_DEX_PROTOCOLS:
        rows = [(spec, row) for spec, row in vault_db.rows.items() if row.get("Protocol") == protocol]
        counts = Counter(projected_access.get(spec, _row_access(row)).permission.value for spec, row in rows)
        protocol_updates = sum(1 for update in result.updates if update.protocol == protocol)
        summary.append(
            [
                protocol,
                len(rows),
                protocol_updates,
                counts[VaultDepositPermission.whitelisted.value],
                counts[VaultDepositPermission.permissionless.value],
                counts[VaultDepositPermission.unknown.value],
            ]
        )
    print(
        tabulate(
            summary,
            headers=["protocol", "rows", "updates", "whitelisted", "permissionless", "unknown"],
            tablefmt="rounded_outline",
        )
    )
    print(f"Vault database: {vault_db_path}")
    print(f"Inspected: {result.inspected_rows:,}; updates: {len(result.updates):,}; unresolved: {result.unresolved_rows:,}")
    if dry_run:
        print("Dry run: no files written. Re-run with DRY_RUN=false to apply the migration.")
    elif result.backup_path:
        print(f"Backup: {result.backup_path}")


def main() -> None:
    """Run the native perp DEX migration from environment configuration.

    :return:
        None after printing the migration result.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    source_paths = resolve_source_paths()
    dry_run = parse_boolean_env(os.environ.get("DRY_RUN"), default=True)
    result = migrate_perp_dex_deposit_permissions(vault_db_path, source_paths, dry_run=dry_run)
    print_migration_summary(vault_db_path, result, dry_run=dry_run)


if __name__ == "__main__":
    main()
