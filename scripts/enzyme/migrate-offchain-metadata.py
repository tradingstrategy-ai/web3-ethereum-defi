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
database or cache is written. In apply mode, successful responses are saved in
a small migration-only state file after each request batch, so a later retry
does not repeat completed API reads. The exact retired generated Blue fallback
text is also cleared locally without an API request.

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
- ``ENZYME_METADATA_STATE_PATH``: resumable migration state path. Defaults to
  ``enzyme-offchain-metadata-state.json`` next to the vault database and is
  deleted only after a complete cache/database update.
- Only Blue vaults whose recorded accounting-unit NAV exceeds 1,000 USD,
  1 ETH or 0.1 BTC equivalents are collected. Unsupported denominations are
  skipped rather than converted using an inferred exchange rate.
- ``MAX_WORKERS``: bounded concurrent API requests, default ``1``. Keep this
  conservative because Enzyme returns ``429`` with a ``Retry-After`` header
  when a token exceeds its request quota.
- ``API_TIMEOUT``: per-request timeout in seconds, default ``30``.
- ``BACKUP_PATH``: optional database backup destination for a real run.

Official API documentation:
https://sdk.enzyme.finance/api/overview/
"""

import json
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from eth_typing import HexAddress
from joblib import Parallel, delayed
from requests import RequestException, Session
from tabulate import tabulate
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.enzyme.offchain_metadata import (
    DEFAULT_ENZYME_METADATA_CACHE_PATH,
    EnzymeVaultMetadata,
    create_enzyme_api_session,
    fetch_enzyme_api_vault_metadata,
    load_enzyme_vault_metadata_cache,
    write_enzyme_vault_metadata_cache,
)
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.utils import wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

ENZYME_METADATA_STATE_VERSION = 1

#: Minimum recorded NAV by accounting-unit family for API metadata collection.
#: The database stores values in each vault's denomination, not a universal
#: USD price feed. Keep symbols only where the denomination itself is a
#: reviewed USD, ETH or BTC equivalent.
MINIMUM_NAV_BY_VALUE_UNIT = {
    **dict.fromkeys({"USD", "USDC", "USDC.E", "USDT", "USDT0", "USD₮0", "DAI", "USDS", "SUSD", "BUSD", "FRAX", "USDE", "SUSDE"}, Decimal("1000")),
    **dict.fromkeys({"ETH", "WETH", "STETH", "WSTETH", "RETH", "CBETH", "WEETH", "OSETH"}, Decimal("1")),
    **dict.fromkeys({"BTC", "WBTC", "CBBTC", "IBTC", "TBTC", "FBTC", "LBTC"}, Decimal("0.1")),
}

#: Exact retired Blue fallback text. It is cleared locally without an API read
#: so the current API-only description policy also repairs older database rows.
LEGACY_BLUE_SHORT_DESCRIPTION = "Enzyme Blue tokenised digital-asset investment vehicle."
LEGACY_BLUE_DESCRIPTION_SUFFIX = " is an Enzyme Blue tokenised investment vehicle. Investors hold ERC-20 shares while the vault manager controls the investment configuration and portfolio operations. No manager-provided strategy description is available in this catalogue entry."


@dataclass(slots=True, frozen=True)
class EnzymeMetadataFetchResult:
    """One official API fetch outcome, kept separate from database mutation.

    :param vault_spec: Existing Enzyme Blue vault identity.
    :param metadata: Parsed API result, including a valid empty result.
    :param error: Request or schema error, if collection failed.
    """

    #: Existing Enzyme Blue vault identity.
    vault_spec: VaultSpec
    #: Parsed response, including a valid empty API reply.
    metadata: EnzymeVaultMetadata | None = None
    #: Request or response-validation error.
    error: str | None = None


@dataclass(slots=True, frozen=True)
class EnzymeMetadataUpdate:
    """One address-specific public database description update.

    :param vault_spec: Existing database row identity.
    :param short_description: Resolved short description.
    :param description: Resolved long description.
    :param changed_fields: Fields whose persisted value differs.
    """

    #: Existing database row identity.
    vault_spec: VaultSpec
    #: Official API tagline, if supplied.
    short_description: str | None
    #: Official API long description, if supplied.
    description: str | None
    #: Public row fields that differ from the successful API reply.
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
    """Resolve the durable state file for unfinished API collection.

    :param vault_db_path: Metadata pickle updated only after full collection.
    :return: Explicit state path or a small sibling JSON file.
    """

    configured_path = os.environ.get("ENZYME_METADATA_STATE_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return vault_db_path.with_name("enzyme-offchain-metadata-state.json")


def load_metadata_state(state_path: Path, selected_specs: set[VaultSpec]) -> dict[VaultSpec, EnzymeVaultMetadata]:
    """Load successful API replies saved by an interrupted migration.

    :param state_path: Migration-only JSON checkpoint path.
    :param selected_specs: Currently eligible Blue vault identities.
    :return: Valid completed API replies that still belong to this migration.
    :raise RuntimeError: If the operator must inspect a malformed state file.
    """

    if not state_path.exists():
        return {}
    try:
        with state_path.open() as inp:
            payload = json.load(inp)
        if not isinstance(payload, dict) or payload.get("version") != ENZYME_METADATA_STATE_VERSION:
            message = "unsupported state version"
            raise ValueError(message)
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
            if short_description is not None and not isinstance(short_description, str):
                message = "invalid short description"
                raise ValueError(message)
            if description is not None and not isinstance(description, str):
                message = "invalid description"
                raise ValueError(message)
            vault_spec = VaultSpec(record["chain_id"], record["address"].lower())
            if vault_spec in selected_specs:
                state[vault_spec] = EnzymeVaultMetadata(short_description=short_description, description=description)
        return state
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError(f"Cannot resume Enzyme metadata state {state_path}: {error}") from error


def write_metadata_state(state_path: Path, state: dict[VaultSpec, EnzymeVaultMetadata]) -> None:
    """Atomically checkpoint successful API replies without publishing them.

    :param state_path: Migration-only JSON checkpoint path.
    :param state: Address-indexed successful official API replies.
    :return: None after atomically replacing the checkpoint.
    """

    records = [
        {
            "chain_id": spec.chain_id,
            "address": spec.vault_address.lower(),
            "short_description": metadata.short_description,
            "description": metadata.description,
        }
        for spec, metadata in sorted(state.items(), key=lambda item: (item[0].chain_id, item[0].vault_address.lower()))
    ]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
    with wait_other_writers(state_path):
        with temporary_path.open("wt") as out:
            json.dump({"version": ENZYME_METADATA_STATE_VERSION, "vaults": records}, out, indent=2, sort_keys=True)
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


def get_metadata_value_unit(row: VaultRow) -> str | None:
    """Read the persisted accounting-unit symbol used for the NAV threshold.

    :param row: Persisted vault metadata row.
    :return: Uppercase accounting-unit symbol, if known.
    """

    value_unit = row.get("Denomination") or (row.get("_vault_info") or {}).get("value_asset")
    return value_unit.upper() if isinstance(value_unit, str) else None


def has_description_metadata_minimum_value(row: VaultRow) -> bool:
    """Check whether a Blue row meets the API-collection value threshold.

    :param row: Persisted Blue vault metadata row with accounting-unit NAV.
    :return: ``True`` only above the reviewed USD, ETH or BTC-equivalent limit.
    """

    value_unit = get_metadata_value_unit(row)
    threshold = MINIMUM_NAV_BY_VALUE_UNIT.get(value_unit)
    if threshold is None:
        return False
    try:
        nav = Decimal(str(row.get("NAV")))
    except (InvalidOperation, ValueError):
        return False
    return nav.is_finite() and nav > threshold


def iter_all_enzyme_blue_rows(vault_db: VaultDatabase) -> Iterator[tuple[VaultSpec, VaultRow]]:
    """Yield every existing Enzyme Blue row in deterministic order.

    :param vault_db: Loaded vault metadata database.
    :return: Existing Blue identity and row pairs in deterministic order.
    """

    rows = ((spec, row) for spec, row in vault_db.rows.items() if is_enzyme_blue_row(row))
    yield from sorted(rows, key=lambda item: (item[0].chain_id, item[0].vault_address.lower()))


def is_legacy_blue_fallback(row: VaultRow) -> bool:
    """Return whether a Blue row contains only the retired generated fallback.

    Matching both fields, including the persisted name, avoids clearing a
    manager-supplied description that happens to mention Enzyme Blue.

    :param row: Existing Enzyme Blue metadata row.
    :return: ``True`` only for the exact old generated pair of descriptions.
    """

    display_name = (row.get("Name") or "").strip() or "Unnamed vault"
    return row.get("_short_description") == LEGACY_BLUE_SHORT_DESCRIPTION and row.get("_description") == f"{display_name}{LEGACY_BLUE_DESCRIPTION_SUFFIX}"


def create_legacy_fallback_clear_update(vault_spec: VaultSpec, row: VaultRow) -> EnzymeMetadataUpdate | None:
    """Plan removal of the exact retired generated Blue fallback.

    :param vault_spec: Existing Blue VaultProxy identity.
    :param row: Existing Blue metadata row.
    :return: Clearing update, or ``None`` when the row has no legacy fallback.
    """

    if not is_legacy_blue_fallback(row):
        return None
    return EnzymeMetadataUpdate(vault_spec, None, None, ("_short_description", "_description"))


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
    """Replace description fields with a successful official API response.

    :param vault_spec: Existing Blue VaultProxy identity.
    :param row: Existing metadata row.
    :param metadata: Successful official API result, possibly without text.
    :return: API replacement values, which can be absent when Enzyme has no copy.
    """

    updates = {
        "_short_description": metadata.short_description,
        "_description": metadata.description,
    }
    changed_fields = tuple(field for field, value in updates.items() if row.get(field) != value)
    return EnzymeMetadataUpdate(vault_spec, metadata.short_description, metadata.description, changed_fields)


def apply_metadata_updates(vault_db: VaultDatabase, updates: list[EnzymeMetadataUpdate]) -> None:
    """Apply only description fields owned by this migration in memory.

    :param vault_db: Loaded database to modify.
    :param updates: Planned API description updates.
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
    """Fetch eligible Blue descriptions and optionally persist metadata repairs.

    :return: None after printing the migration plan or completing a safe write.
    :raise RuntimeError: If any official API response fails before a real write.
    """

    dry_run = parse_bool_env("DRY_RUN", default=True)
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
    blue_rows = list(iter_all_enzyme_blue_rows(vault_db))
    selected_rows = [(spec, row) for spec, row in blue_rows if has_description_metadata_minimum_value(row)]
    legacy_clear_updates = [update for spec, row in blue_rows if (update := create_legacy_fallback_clear_update(spec, row))]
    if not selected_rows and not legacy_clear_updates:
        print("No Enzyme Blue metadata updates are needed.")
        return

    state_path = resolve_state_path(vault_db_path)
    state: dict[VaultSpec, EnzymeVaultMetadata] = {}
    missing_specs: list[VaultSpec] = []
    successful: list[EnzymeMetadataFetchResult] = []
    if selected_rows:
        api_token = os.environ.get("ENZYME_BLUE_API_TOKEN")
        if not api_token:
            message = "ENZYME_BLUE_API_TOKEN is required to fetch official Enzyme Blue vault metadata"
            raise RuntimeError(message)
        selected_specs = {spec for spec, _row in selected_rows}
        state = load_metadata_state(state_path, selected_specs)
        missing_specs = [spec for spec, _row in selected_rows if spec not in state]
        session = create_enzyme_api_session(max_workers)
        try:
            with tqdm(total=len(selected_rows), initial=len(state), desc="Fetching Enzyme Blue metadata") as progress:
                for start in range(0, len(missing_specs), max_workers):
                    batch_specs = missing_specs[start : start + max_workers]
                    batch_results = Parallel(n_jobs=max_workers, backend="threading")(delayed(fetch_one_enzyme_metadata)(spec, api_token=api_token, timeout=timeout, session=session) for spec in batch_specs)
                    successes = [result for result in batch_results if result.metadata is not None]
                    state.update({result.vault_spec: result.metadata for result in successes})
                    if not dry_run and successes:
                        write_metadata_state(state_path, state)
                    progress.update(len(batch_results))
                    failures = [result for result in batch_results if result.error]
                    if failures:
                        examples = "; ".join(f"{item.vault_spec.vault_address}: {item.error}" for item in failures[:3])
                        raise RuntimeError(f"Enzyme metadata fetch failed for {len(failures)} vaults; state was saved for {len(state)} of {len(selected_rows)} rows. Examples: {examples}")
        finally:
            session.close()

        assert len(state) == len(selected_rows)
        successful = [EnzymeMetadataFetchResult(spec, metadata=state[spec]) for spec, _row in selected_rows]

    rows_by_spec = dict(selected_rows)
    api_updates = [create_metadata_update(result.vault_spec, rows_by_spec[result.vault_spec], result.metadata) for result in successful]
    changed_updates = legacy_clear_updates + [update for update in api_updates if update.changed_fields]
    with_short_description = sum(result.metadata.short_description is not None for result in successful)
    with_description = sum(result.metadata.description is not None for result in successful)
    print(
        tabulate(
            [
                ["Eligible Enzyme Blue rows", len(selected_rows)],
                ["Resumed API replies", len(selected_rows) - len(missing_specs)],
                ["Legacy fallback rows cleared", len(legacy_clear_updates)],
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

    backup_path = resolve_backup_path(vault_db_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(vault_db_path, backup_path)
    if successful:
        cached_metadata = load_enzyme_vault_metadata_cache(cache_path)
        cached_metadata.update({(result.vault_spec.chain_id, HexAddress(result.vault_spec.vault_address.lower())): result.metadata for result in successful})
        write_enzyme_vault_metadata_cache(cached_metadata, cache_path)
    apply_metadata_updates(vault_db, changed_updates)
    vault_db.write(vault_db_path)
    if selected_rows:
        state_path.unlink()
    print(f"Updated {len(changed_updates)} Enzyme Blue rows. Metadata backup: {backup_path}. Cache: {cache_path}.")


if __name__ == "__main__":
    main()
