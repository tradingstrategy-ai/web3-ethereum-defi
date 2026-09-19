#!/usr/bin/env python3
"""Fetch and persist Enzyme Blue manager profile metadata.

Enzyme's vault-detail app exposes Blue vault profiles through an undocumented,
unauthenticated GraphQL ``vaultProfile`` query. This command snapshots the
public vault and manager fields for every discovered Enzyme Blue row, stores
successful replies in the shared Enzyme cache, and updates the public
``_short_description``, ``_description`` and ``_manager_name`` fields.

This is intentionally isolated from the regular scanner because Enzyme has not
published a stable external contract for this app backend. Enzyme Onyx remains
out of scope: no equivalent public profile reader has been established.

The command starts in dry-run mode. It never alters historical prices, scanner
reader state or discovery leads. A failed app response aborts before either the
database or cache is written. In apply mode, successful responses are saved in
a small migration-only state file after each request batch, so a later retry
does not repeat completed app reads. The exact retired generated Blue fallback
text is also cleared locally without an app request.

Usage::

    source .local-test.env && poetry run python scripts/enzyme/migrate-offchain-metadata.py

    source .local-test.env && DRY_RUN=false poetry run python scripts/enzyme/migrate-offchain-metadata.py

Every discovered Blue vault is collected regardless of its current NAV or
denomination, because contact metadata is independent of asset value.

Environment variables:

- ``DRY_RUN``: print proposed changes without writing, default ``true``.
- ``VAULT_DB_PATH``: metadata pickle to update, default pipeline location.
- ``ENZYME_METADATA_CACHE_PATH``: persistent app-profile cache location.
- ``ENZYME_METADATA_STATE_PATH``: resumable migration state path. Defaults to
  ``enzyme-offchain-metadata-state.json`` next to the vault database and is
  deleted only after a complete cache/database update.
- ``ENZYME_METADATA_REFRESH``: fetch every Blue row again instead of reusing
  a completed cache entry, default ``false``.
- ``ENZYME_PROFILE_BATCH_SIZE``: public GraphQL profile aliases per serial
  request, default ``5``. Enzyme currently enforces this five-alias limit.
- ``ENZYME_REQUEST_INTERVAL_SECONDS``: minimum wait after each request batch,
  default ``1``. Keep this rate limit in place to avoid Cloudflare and backend
  throttling while collecting the complete Blue catalogue.
- ``API_TIMEOUT``: per-request timeout in seconds, default ``30``.
- ``BACKUP_PATH``: optional database backup destination for a real run.

The app implementation currently uses ``https://app.enzyme.finance/api/graphql``.
"""

import json
import logging
import os
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from eth_typing import HexAddress
from requests import RequestException, Session
from tabulate import tabulate
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.enzyme.offchain_metadata import (
    DEFAULT_ENZYME_METADATA_CACHE_PATH,
    ENZYME_APP_MAX_PROFILE_ALIASES,
    ENZYME_METADATA_CACHE_VERSION,
    EnzymeVaultMetadata,
    create_enzyme_app_session,
    fetch_enzyme_app_vault_metadata_batch,
    load_enzyme_vault_metadata_cache,
    write_enzyme_vault_metadata_cache,
)
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.utils import wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

logger = logging.getLogger(__name__)

ENZYME_METADATA_STATE_VERSION = 3
PREVIOUS_ENZYME_METADATA_STATE_VERSION = 2

#: Exact retired Blue fallback text. It is cleared locally without an app read
#: so the profile-only description policy also repairs older database rows.
LEGACY_BLUE_SHORT_DESCRIPTION = "Enzyme Blue tokenised digital-asset investment vehicle."
LEGACY_BLUE_DESCRIPTION_SUFFIX = " is an Enzyme Blue tokenised investment vehicle. Investors hold ERC-20 shares while the vault manager controls the investment configuration and portfolio operations. No manager-provided strategy description is available in this catalogue entry."


@dataclass(slots=True, frozen=True)
class EnzymeMetadataUpdate:
    """One address-specific public database profile update."""

    #: Existing database row identity.
    vault_spec: VaultSpec
    #: App-profile tagline, if supplied.
    short_description: str | None
    #: App-profile long description, if supplied.
    description: str | None
    #: Public manager identity from the app profile, if supplied.
    manager_name: str | None
    #: Public row fields that differ from the successful app-profile reply.
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


def resolve_state_path(vault_db_path: Path) -> Path:
    """Resolve the durable state file for unfinished app-profile collection.

    :param vault_db_path: Metadata pickle updated only after full collection.
    :return: Explicit state path or a small sibling JSON file.
    """

    configured_path = os.environ.get("ENZYME_METADATA_STATE_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return vault_db_path.with_name("enzyme-offchain-metadata-state.json")


def load_metadata_state(  # noqa: PLR0914 - Validates every persisted app-profile field.
    state_path: Path,
    selected_specs: set[VaultSpec],
    *,
    refresh: bool = False,
) -> dict[VaultSpec, EnzymeVaultMetadata]:
    """Load successful app-profile replies saved by an interrupted migration.

    :param state_path: Migration-only JSON checkpoint path.
    :param selected_specs: Currently eligible Blue vault identities.
    :param refresh: Whether the current run deliberately refetches every profile.
    :return: Valid completed app-profile replies that still belong to this migration.
    :raise RuntimeError: If the operator must inspect a malformed state file.
    """

    if not state_path.exists():
        return {}
    try:
        with state_path.open() as inp:
            payload = json.load(inp)
        if not isinstance(payload, dict):
            message = "state must be a JSON object"
            raise ValueError(message)
        version = payload.get("version")
        if version == 1:
            # Version one did not contain contact data and must not suppress a
            # complete profile refresh.
            logger.warning("Discarding pre-contact Enzyme metadata state %s", state_path)
            return {}
        if version not in {PREVIOUS_ENZYME_METADATA_STATE_VERSION, ENZYME_METADATA_STATE_VERSION}:
            message = "unsupported state version"
            raise ValueError(message)
        if version == ENZYME_METADATA_STATE_VERSION and payload.get("refresh") is not refresh:
            logger.info("Ignoring Enzyme metadata state %s from a different refresh mode", state_path)
            return {}
        if version == PREVIOUS_ENZYME_METADATA_STATE_VERSION and refresh:
            logger.info("Ignoring unmarked Enzyme metadata state %s during a full refresh", state_path)
            return {}
        records = payload.get("vaults")
        if not isinstance(records, list):
            message = "vaults must be a list"
            raise ValueError(message)
        state = {}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("chain_id"), int) or not isinstance(record.get("address"), str):
                message = "invalid vault record"
                raise ValueError(message)
            short_description = record.get("short_description")
            description = record.get("description")
            manager_description = record.get("manager_description")
            contact_info = record.get("contact_info")
            contact_email = record.get("contact_email")
            telegram = record.get("telegram")
            twitter = record.get("twitter")
            website_url = record.get("website_url")
            manager_name = record.get("manager_name")
            if short_description is not None and not isinstance(short_description, str):
                message = "invalid short description"
                raise ValueError(message)
            if description is not None and not isinstance(description, str):
                message = "invalid description"
                raise ValueError(message)
            profile_fields = (manager_description, contact_info, contact_email, telegram, twitter, website_url, manager_name)
            if any(value is not None and not isinstance(value, str) for value in profile_fields):
                message = "invalid manager profile field"
                raise ValueError(message)
            vault_spec = VaultSpec(record["chain_id"], record["address"].lower())
            if vault_spec in selected_specs:
                state[vault_spec] = EnzymeVaultMetadata(
                    short_description=short_description,
                    description=description,
                    manager_description=manager_description,
                    contact_info=contact_info,
                    contact_email=contact_email,
                    telegram=telegram,
                    twitter=twitter,
                    website_url=website_url,
                    manager_name=manager_name,
                )
        return state
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"Cannot resume Enzyme metadata state {state_path}: {error}") from error


def write_metadata_state(
    state_path: Path,
    state: dict[VaultSpec, EnzymeVaultMetadata],
    *,
    refresh: bool = False,
) -> None:
    """Atomically checkpoint successful app-profile replies without publishing them.

    :param state_path: Migration-only JSON checkpoint path.
    :param state: Address-indexed successful app-profile replies.
    :param refresh: Whether the saved replies all came from a forced refresh.
    :return: None after atomically replacing the checkpoint.
    """

    records = [
        {
            "chain_id": spec.chain_id,
            "address": spec.vault_address.lower(),
            "short_description": metadata.short_description,
            "description": metadata.description,
            "manager_description": metadata.manager_description,
            "contact_info": metadata.contact_info,
            "contact_email": metadata.contact_email,
            "telegram": metadata.telegram,
            "twitter": metadata.twitter,
            "website_url": metadata.website_url,
            "manager_name": metadata.manager_name,
        }
        for spec, metadata in sorted(state.items(), key=lambda item: (item[0].chain_id, item[0].vault_address.lower()))
    ]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
    with wait_other_writers(state_path):
        with temporary_path.open("wt") as out:
            json.dump({"version": ENZYME_METADATA_STATE_VERSION, "refresh": refresh, "vaults": records}, out, indent=2, sort_keys=True)
            out.write("\n")
        temporary_path.replace(state_path)


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


def iter_all_enzyme_blue_rows(vault_db: VaultDatabase) -> Iterator[tuple[VaultSpec, VaultRow]]:
    """Yield every existing Enzyme Blue row in deterministic order.

    :param vault_db: Loaded vault metadata database.
    :return: Existing Blue identity and row pairs in deterministic order.
    """

    rows = ((spec, row) for spec, row in vault_db.rows.items() if is_enzyme_blue_row(row))
    yield from sorted(rows, key=lambda item: (item[0].chain_id, item[0].vault_address.lower()))


def create_legacy_fallback_clear_update(vault_spec: VaultSpec, row: VaultRow) -> EnzymeMetadataUpdate | None:
    """Plan removal of retired generated Blue fallback fields independently.

    The old resolver filled absent profile fields separately, so a row can contain
    a real manager field alongside one invented fallback field. Each exact
    generated fragment is therefore cleared independently and the other field
    is preserved.

    :param vault_spec: Existing Blue VaultProxy identity.
    :param row: Existing Blue metadata row.
    :return: Field-preserving clearing update, or ``None`` when no legacy copy exists.
    """

    has_legacy_short = row.get("_short_description") == LEGACY_BLUE_SHORT_DESCRIPTION
    has_legacy_description = str(row.get("_description")).endswith(LEGACY_BLUE_DESCRIPTION_SUFFIX)
    if not has_legacy_short and not has_legacy_description:
        return None
    return EnzymeMetadataUpdate(
        vault_spec,
        None if has_legacy_short else row.get("_short_description"),
        None if has_legacy_description else row.get("_description"),
        row.get("_manager_name"),
        tuple(field for field, enabled in (("_short_description", has_legacy_short), ("_description", has_legacy_description)) if enabled),
    )


def fetch_enzyme_metadata_batch(
    vault_specs: list[VaultSpec],
    *,
    timeout: float,
    session: Session,
) -> dict[VaultSpec, EnzymeVaultMetadata]:
    """Fetch one serial GraphQL alias batch.

    :param vault_specs: Existing Blue VaultProxy identities for one HTTP request.
    :param timeout: Per-request HTTP timeout.
    :param session: Shared retrying HTTP session.
    :return: Metadata indexed by its existing Blue vault identity.
    :raise RuntimeError: If the app endpoint or its response fails.
    """

    try:
        fetched_metadata = fetch_enzyme_app_vault_metadata_batch(
            session,
            shares_addresses=[spec.vault_address for spec in vault_specs],
            timeout=timeout,
        )
        return {vault_spec: fetched_metadata[HexAddress(vault_spec.vault_address.lower())] for vault_spec in vault_specs}
    except (RequestException, ValueError) as error:
        addresses = ", ".join(vault_spec.vault_address for vault_spec in vault_specs[:3])
        raise RuntimeError(f"Enzyme app-profile batch failed for {addresses}: {error}") from error


def create_metadata_update(vault_spec: VaultSpec, row: VaultRow, metadata: EnzymeVaultMetadata) -> EnzymeMetadataUpdate:
    """Replace profile fields with a successful Enzyme app response.

    :param vault_spec: Existing Blue VaultProxy identity.
    :param row: Existing metadata row.
    :param metadata: Successful app profile, possibly without text or contacts.
    :return: Profile replacement values, which can be absent when Enzyme has no copy.
    """

    updates = {
        "_short_description": metadata.short_description,
        "_description": metadata.description,
        "_manager_name": metadata.manager_name,
    }
    changed_fields = tuple(field for field, value in updates.items() if row.get(field) != value)
    return EnzymeMetadataUpdate(vault_spec, metadata.short_description, metadata.description, metadata.manager_name, changed_fields)


def apply_metadata_updates(vault_db: VaultDatabase, updates: list[EnzymeMetadataUpdate]) -> None:
    """Apply only app-profile fields owned by this migration in memory.

    :param vault_db: Loaded database to modify.
    :param updates: Planned app-profile updates.
    :return: None after replacing changed rows in memory.
    """

    for update in updates:
        if not update.changed_fields:
            continue
        row = vault_db.rows[update.vault_spec].copy()
        row["_short_description"] = update.short_description
        row["_description"] = update.description
        row["_manager_name"] = update.manager_name
        vault_db.rows[update.vault_spec] = row


def fetch_enzyme_metadata(
    selected_rows: list[tuple[VaultSpec, VaultRow]],
    *,
    state_path: Path,
    cache_path: Path,
    dry_run: bool,
    profile_batch_size: int,
    request_interval_seconds: float,
    timeout: float,
) -> tuple[dict[VaultSpec, EnzymeVaultMetadata], int, dict[tuple[int, HexAddress], EnzymeVaultMetadata]]:
    """Collect Enzyme app profiles, resuming state and completed cache entries.

    The request follows the current public vault-detail page's undocumented
    GraphQL query. A completed cache is reused by default, whereas
    ``ENZYME_METADATA_REFRESH=true`` intentionally reads every vault again.

    :param selected_rows: Eligible Blue database rows in deterministic order.
    :param state_path: Migration-only state checkpoint path.
    :param cache_path: Durable app-profile cache path.
    :param dry_run: Whether successful replies must remain transient.
    :param profile_batch_size: Vault profile aliases per serial request.
    :param request_interval_seconds: Minimum pause after each request batch.
    :param timeout: Per-request app-profile timeout in seconds.
    :return: Collected metadata, request count and existing durable cache.
    :raise RuntimeError: If any app-profile read fails.
    """

    if not selected_rows:
        return {}, 0, {}

    selected_specs = {spec for spec, _row in selected_rows}
    refresh = parse_bool_env("ENZYME_METADATA_REFRESH", default=False)
    state = load_metadata_state(state_path, selected_specs, refresh=refresh)
    cached_metadata = load_enzyme_vault_metadata_cache(cache_path, minimum_version=ENZYME_METADATA_CACHE_VERSION)
    if not refresh:
        state.update({spec: metadata for spec in selected_specs - state.keys() if (metadata := cached_metadata.get((spec.chain_id, HexAddress(spec.vault_address.lower())))) is not None})
    missing_specs = [spec for spec, _row in selected_rows if spec not in state]
    if not missing_specs:
        return state, 0, cached_metadata

    session = create_enzyme_app_session()
    try:
        with tqdm(total=len(selected_rows), initial=len(state), desc="Fetching Enzyme Blue metadata") as progress:
            for start in range(0, len(missing_specs), profile_batch_size):
                batch_specs = missing_specs[start : start + profile_batch_size]
                batch_metadata = fetch_enzyme_metadata_batch(batch_specs, timeout=timeout, session=session)
                state.update(batch_metadata)
                if not dry_run:
                    write_metadata_state(state_path, state, refresh=refresh)
                progress.update(len(batch_metadata))
                if start + len(batch_specs) < len(missing_specs):
                    time.sleep(request_interval_seconds)
    finally:
        session.close()

    assert len(state) == len(selected_rows)
    return state, len(missing_specs), cached_metadata


def main() -> None:  # noqa: PLR0914 - Keeps the one-shot migration transaction visible in one place.
    """Fetch every Blue profile and optionally persist metadata repairs.

    :return: None after printing the migration plan or completing a safe write.
    :raise RuntimeError: If any app-profile response fails before a real write.
    """

    dry_run = parse_bool_env("DRY_RUN", default=True)
    profile_batch_size = int(os.environ.get("ENZYME_PROFILE_BATCH_SIZE", "5"))
    request_interval_seconds = float(os.environ.get("ENZYME_REQUEST_INTERVAL_SECONDS", "1"))
    timeout = float(os.environ.get("API_TIMEOUT", "30"))
    if timeout <= 0:
        message = "API_TIMEOUT must be positive"
        raise ValueError(message)
    if not 1 <= profile_batch_size <= ENZYME_APP_MAX_PROFILE_ALIASES:
        message = f"ENZYME_PROFILE_BATCH_SIZE must be between 1 and {ENZYME_APP_MAX_PROFILE_ALIASES}"
        raise ValueError(message)
    if request_interval_seconds < 0:
        message = "ENZYME_REQUEST_INTERVAL_SECONDS must be non-negative"
        raise ValueError(message)

    vault_db_path = resolve_vault_database_path()
    if not vault_db_path.exists():
        raise FileNotFoundError(f"Vault metadata database does not exist: {vault_db_path}")
    cache_path = resolve_cache_path()
    vault_db = VaultDatabase.read(vault_db_path)
    blue_rows = list(iter_all_enzyme_blue_rows(vault_db))
    selected_rows = blue_rows
    legacy_clear_updates = [update for spec, row in blue_rows if (update := create_legacy_fallback_clear_update(spec, row))]
    if not selected_rows and not legacy_clear_updates:
        print("No Enzyme Blue metadata updates are needed.")
        return

    state_path = resolve_state_path(vault_db_path)
    state, request_count, cached_metadata = fetch_enzyme_metadata(
        selected_rows,
        state_path=state_path,
        cache_path=cache_path,
        dry_run=dry_run,
        profile_batch_size=profile_batch_size,
        request_interval_seconds=request_interval_seconds,
        timeout=timeout,
    )
    rows_by_spec = dict(selected_rows)
    api_updates = [create_metadata_update(vault_spec, rows_by_spec[vault_spec], metadata) for vault_spec, metadata in state.items()]
    changed_updates = legacy_clear_updates + [update for update in api_updates if update.changed_fields]
    with_short_description = sum(metadata.short_description is not None for metadata in state.values())
    with_description = sum(metadata.description is not None for metadata in state.values())
    with_manager_name = sum(metadata.manager_name is not None for metadata in state.values())
    with_contact = sum(any((metadata.contact_info, metadata.contact_email, metadata.telegram, metadata.twitter, metadata.website_url)) for metadata in state.values())
    print(
        tabulate(
            [
                ["All Enzyme Blue rows", len(blue_rows)],
                ["Collected Enzyme Blue rows", len(selected_rows)],
                ["Reused app-profile replies", len(selected_rows) - request_count],
                ["Legacy fallback rows repaired", len(legacy_clear_updates)],
                ["App-profile taglines", with_short_description],
                ["App-profile descriptions", with_description],
                ["App-profile contacts", with_contact],
                ["Derived manager identifiers", with_manager_name],
                ["Rows with database changes", len({update.vault_spec for update in changed_updates})],
                ["Mode", "dry run" if dry_run else "apply"],
            ],
            headers=["Item", "Count"],
            tablefmt="github",
        )
    )
    if dry_run:
        return

    cache_updates = {(vault_spec.chain_id, HexAddress(vault_spec.vault_address.lower())): metadata for vault_spec, metadata in state.items()}
    cache_changed = any(cached_metadata.get(key) != metadata for key, metadata in cache_updates.items())
    if cache_changed:
        cached_metadata.update(cache_updates)
        write_enzyme_vault_metadata_cache(cached_metadata, cache_path)
    if changed_updates:
        backup_path = resolve_backup_path(vault_db_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(vault_db_path, backup_path)
        apply_metadata_updates(vault_db, changed_updates)
        vault_db.write(vault_db_path)
    if selected_rows and state_path.exists():
        state_path.unlink()
    print(f"Updated {len({update.vault_spec for update in changed_updates})} Enzyme Blue rows. Cache updated: {cache_changed}.")


if __name__ == "__main__":
    main()
