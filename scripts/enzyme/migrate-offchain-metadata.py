#!/usr/bin/env python3
"""Fetch and persist Enzyme Blue manager-entered listing metadata.

Enzyme's authenticated ``GetVault`` API is the authoritative source for
manager-entered Blue vault taglines and descriptions. This command fetches
those fields for the Enzyme Blue rows already present in the local vault
metadata database, stores successful replies in the shared Enzyme cache, and
updates the public ``_short_description`` and ``_description`` fields.

The Enzyme API documents the Blue deployments on Ethereum, Polygon, Base and
Arbitrum. Enzyme Onyx descriptions are editable in the management application,
but no public metadata endpoint is documented for it. This migration therefore
does not alter Onyx rows or scrape a gated UI.

The command starts in dry-run mode. It never alters historical prices, scanner
reader state or discovery leads. A failed API response aborts before either the
database or cache is written, so a temporary offchain failure cannot replace
existing descriptions with fallbacks.

Usage::

    source .local-test.env && ENZYME_BLUE_API_TOKEN=... \\
        poetry run python scripts/enzyme/migrate-offchain-metadata.py

    source .local-test.env && ENZYME_BLUE_API_TOKEN=... DRY_RUN=false \\
        poetry run python scripts/enzyme/migrate-offchain-metadata.py

Environment variables:

- ``ENZYME_BLUE_API_TOKEN``: required bearer token generated in the Enzyme app.
- ``DRY_RUN``: print proposed changes without writing, default ``true``.
- ``VAULT_DB_PATH``: metadata pickle to update, default pipeline location.
- ``ENZYME_METADATA_CACHE_PATH``: persistent API cache location.
- ``MAX_WORKERS``: bounded concurrent API requests, default ``1``. Keep this
  conservative because Enzyme returns ``429`` with a ``Retry-After`` header
  when a token exceeds its request quota.
- ``API_TIMEOUT``: per-request timeout in seconds, default ``30``.
- ``BACKUP_PATH``: optional database backup destination for a real run.

Official API documentation:
https://sdk.enzyme.finance/api/overview/
"""

import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from eth_typing import HexAddress
from joblib import Parallel, delayed
from requests import RequestException, Session
from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.enzyme.offchain_metadata import (
    DEFAULT_ENZYME_METADATA_CACHE_PATH,
    EnzymeVaultMetadata,
    create_enzyme_api_session,
    fetch_enzyme_api_vault_metadata,
    load_enzyme_vault_metadata_cache,
    resolve_enzyme_blue_vault_metadata,
    write_enzyme_vault_metadata_cache,
)
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir


@dataclass(slots=True, frozen=True)
class EnzymeMetadataFetchResult:
    """One official API fetch outcome, kept separate from database mutation.

    :param vault_spec: Existing Enzyme Blue vault identity.
    :param metadata: Parsed API result, including a valid empty result.
    :param error: Request or schema error, if collection failed.
    """

    vault_spec: VaultSpec
    metadata: EnzymeVaultMetadata | None = None
    error: str | None = None


@dataclass(slots=True, frozen=True)
class EnzymeMetadataUpdate:
    """One address-specific public database description update.

    :param vault_spec: Existing database row identity.
    :param short_description: Resolved short description.
    :param description: Resolved long description.
    :param changed_fields: Fields whose persisted value differs.
    """

    vault_spec: VaultSpec
    short_description: str
    description: str
    changed_fields: tuple[str, ...]


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse a boolean environment setting.

    :param name: Environment variable to parse.
    :param default: Value when the setting is absent.
    :return: Parsed boolean value.
    :raise ValueError: If the configured value is invalid.
    """

    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Unsupported {name} value: {value!r}")


def resolve_vault_database_path() -> Path:
    """Resolve the database path selected for this metadata-only migration.

    :return: Explicit ``VAULT_DB_PATH`` or the pipeline's metadata pickle.
    """

    configured_path = os.environ.get("VAULT_DB_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return get_pipeline_data_dir() / "vault-metadata-db.pickle"


def resolve_cache_path() -> Path:
    """Resolve the durable adapter cache updated by this migration.

    :return: Explicit cache path or the normal Enzyme cache location.
    """

    configured_path = os.environ.get("ENZYME_METADATA_CACHE_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return DEFAULT_ENZYME_METADATA_CACHE_PATH


def resolve_backup_path(vault_db_path: Path) -> Path:
    """Choose a timestamped backup path for a real database update.

    :param vault_db_path: Metadata pickle about to be changed.
    :return: Explicit backup path or a timestamped sibling.
    """

    configured_path = os.environ.get("BACKUP_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    timestamp = native_datetime_utc_now().strftime("%Y%m%d-%H%M%S")
    return vault_db_path.with_name(f"{vault_db_path.stem}.before-enzyme-offchain-metadata-{timestamp}{vault_db_path.suffix}")


def is_enzyme_blue_row(row: VaultRow) -> bool:
    """Return whether a database row was factory-confirmed as Enzyme Blue.

    :param row: Persisted vault metadata row.
    :return: ``True`` only for Enzyme Blue feature rows.
    """

    if row.get("Protocol") != "Enzyme":
        return False
    detection = row.get("_detection_data")
    features = row.get("features") or getattr(detection, "features", set())
    feature_values = {feature.value if isinstance(feature, ERC4626Feature) else str(feature) for feature in features}
    return ERC4626Feature.enzyme_blue_like.value in feature_values


def iter_enzyme_blue_rows(vault_db: VaultDatabase) -> Iterator[tuple[VaultSpec, VaultRow]]:
    """Yield existing Enzyme Blue rows in deterministic request order.

    :param vault_db: Loaded vault metadata database.
    :return: Existing Blue identity and row pairs.
    """

    rows = ((spec, row) for spec, row in vault_db.rows.items() if is_enzyme_blue_row(row))
    yield from sorted(rows, key=lambda item: (item[0].chain_id, item[0].vault_address.lower()))


def fetch_one_enzyme_metadata(
    vault_spec: VaultSpec,
    *,
    api_token: str,
    timeout: float,
    session: Session,
) -> EnzymeMetadataFetchResult:
    """Fetch one Blue row while preserving individual failures for reporting.

    :param vault_spec: Existing Blue VaultProxy identity.
    :param api_token: Enzyme API bearer token.
    :param timeout: Per-request HTTP timeout.
    :param session: Shared retrying HTTP session.
    :return: Parsed result or explicit error without mutating persistent state.
    """

    try:
        metadata = fetch_enzyme_api_vault_metadata(
            session,
            chain_id=vault_spec.chain_id,
            shares_address=vault_spec.vault_address,
            api_token=api_token,
            timeout=timeout,
        )
        return EnzymeMetadataFetchResult(vault_spec, metadata=metadata)
    except (RequestException, ValueError) as error:
        return EnzymeMetadataFetchResult(vault_spec, error=str(error))


def create_metadata_update(vault_spec: VaultSpec, row: VaultRow, metadata: EnzymeVaultMetadata) -> EnzymeMetadataUpdate:
    """Resolve official API fields against the safe Blue fallback copy.

    :param vault_spec: Existing Blue VaultProxy identity.
    :param row: Existing metadata row.
    :param metadata: Successful official API result, possibly without text.
    :return: Complete replacement values for the migration-owned fields.
    """

    resolved = resolve_enzyme_blue_vault_metadata(row.get("Name") or "", metadata)
    assert resolved.short_description is not None
    assert resolved.description is not None
    updates = {
        "_short_description": resolved.short_description,
        "_description": resolved.description,
    }
    changed_fields = tuple(field for field, value in updates.items() if row.get(field) != value)
    return EnzymeMetadataUpdate(vault_spec, resolved.short_description, resolved.description, changed_fields)


def apply_metadata_updates(vault_db: VaultDatabase, updates: list[EnzymeMetadataUpdate]) -> None:
    """Apply only description fields owned by this migration in memory.

    :param vault_db: Loaded database to modify.
    :param updates: Planned API/fallback description updates.
    :return: None after replacing changed rows in memory.
    """

    for update in updates:
        if not update.changed_fields:
            continue
        row = vault_db.rows[update.vault_spec].copy()
        row["_short_description"] = update.short_description
        row["_description"] = update.description
        vault_db.rows[update.vault_spec] = row


def main() -> None:  # noqa: PLR0914 - Keeps the one-shot migration transaction visible in one place.
    """Fetch all existing Enzyme Blue descriptions and optionally persist them.

    :return: None after printing the migration plan or completing a safe write.
    :raise RuntimeError: If any official API response fails before a real write.
    """

    dry_run = parse_bool_env("DRY_RUN", default=True)
    api_token = os.environ.get("ENZYME_BLUE_API_TOKEN")
    if not api_token:
        message = "ENZYME_BLUE_API_TOKEN is required to fetch official Enzyme Blue vault metadata"
        raise RuntimeError(message)
    max_workers = int(os.environ.get("MAX_WORKERS", "1"))
    timeout = float(os.environ.get("API_TIMEOUT", "30"))
    if max_workers < 1:
        message = "MAX_WORKERS must be positive"
        raise ValueError(message)
    if timeout <= 0:
        message = "API_TIMEOUT must be positive"
        raise ValueError(message)

    vault_db_path = resolve_vault_database_path()
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault metadata database does not exist: {vault_db_path}")
    cache_path = resolve_cache_path()
    vault_db = VaultDatabase.read(vault_db_path)
    selected_rows = list(iter_enzyme_blue_rows(vault_db))
    if not selected_rows:
        print("No Enzyme Blue rows found; no metadata collection is needed.")
        return

    session = create_enzyme_api_session(max_workers)
    try:
        results = Parallel(n_jobs=max_workers, backend="threading")(delayed(fetch_one_enzyme_metadata)(spec, api_token=api_token, timeout=timeout, session=session) for spec, _row in selected_rows)
    finally:
        session.close()

    failures = [result for result in results if result.error]
    successful = [result for result in results if result.metadata is not None]
    if failures:
        examples = "; ".join(f"{item.vault_spec.vault_address}: {item.error}" for item in failures[:3])
        raise RuntimeError(f"Enzyme metadata fetch failed for {len(failures)} of {len(results)} vaults; no changes were written. Examples: {examples}")
    assert len(successful) == len(selected_rows)

    rows_by_spec = dict(selected_rows)
    updates = [create_metadata_update(result.vault_spec, rows_by_spec[result.vault_spec], result.metadata) for result in successful]
    changed_updates = [update for update in updates if update.changed_fields]
    with_short_description = sum(result.metadata.short_description is not None for result in successful)
    with_description = sum(result.metadata.description is not None for result in successful)
    print(
        tabulate(
            [
                ["Existing Enzyme Blue rows", len(selected_rows)],
                ["Official API taglines", with_short_description],
                ["Official API descriptions", with_description],
                ["Rows with database changes", len(changed_updates)],
                ["Mode", "dry run" if dry_run else "apply"],
            ],
            headers=["Item", "Count"],
            tablefmt="github",
        )
    )
    if dry_run:
        return

    cached_metadata = load_enzyme_vault_metadata_cache(cache_path)
    cached_metadata.update({(result.vault_spec.chain_id, HexAddress(result.vault_spec.vault_address.lower())): result.metadata for result in successful})
    backup_path = resolve_backup_path(vault_db_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vault_db_path, backup_path)
    write_enzyme_vault_metadata_cache(cached_metadata, cache_path)
    apply_metadata_updates(vault_db, changed_updates)
    vault_db.write(vault_db_path)
    print(f"Updated {len(changed_updates)} Enzyme Blue rows. Metadata backup: {backup_path}. Cache: {cache_path}.")


if __name__ == "__main__":
    main()
