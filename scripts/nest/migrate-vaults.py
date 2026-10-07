#!/usr/bin/env python3
"""Migrate active Nest vault metadata and historical price observations.

Nest's first-party catalogue supplies chain-specific entrypoints and start
blocks. This migration selects all active routes on configured Nest chains,
verifies each current contract with the Nest classification probe, then
backfills its raw and cleaned price history through the shared vault writers.
Existing discovery blocks, event counts, leads, chain discovery cursors and
reader states are preserved. Timestamp caches are filled through HyperSync
before each historical price scan.

The script reads every selected route before changing the database, makes a
sibling metadata backup, and holds the shared scanner writer lock through the
metadata and address-scoped price writes. Dry run is the default.

Usage:

.. code-block:: shell

    source .local-test.env && DRY_RUN=true PYTHONPATH=. \\
        poetry run python scripts/nest/migrate-vaults.py

    source .local-test.env && DRY_RUN=false PYTHONPATH=. \\
        poetry run python scripts/nest/migrate-vaults.py

Set ``VAULT_DB_PATH``, ``UNCLEANED_PRICE_DATABASE``,
``CLEANED_PRICE_DATABASE`` and ``TIMESTAMP_CACHE_DIR`` together to migrate
isolated copies. ``NETWORKS``
selects a subset for a focused repair; the default covers every supported
active chain. Set ``NEST_SCAN_PRICES=false`` for a metadata-only repair. The
corresponding ``JSON_RPC_*`` variables must be set. Applied historical scans
also require ``HYPERSYNC_API_KEY``.
Worldchain and Plume routes require ``JSON_RPC_WORLDCHAIN`` and
``JSON_RPC_PLUME``. Morph has no active catalogue routes. Existing
Nest rows on selected chains also receive current CMS metadata.
"""

import dataclasses
import datetime
import logging
import os
import shutil
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from eth_typing import HexAddress
from tabulate import tabulate
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import CHAIN_RESTRICTED_PROBES, create_vault_instance, detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.nest.offchain_metadata import NEST_ARC_CHAIN_ID, NEST_CHAIN_NAMES, NEST_CURATOR_SLUG, NestVaultMetadata, fetch_nest_vaults, select_nest_manager_name
from eth_defi.erc_4626.vault_protocol.nest.tags import STRATEGY_TAGS
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.hypersync_timestamp import fetch_block_timestamps_using_hypersync_cached
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.env import get_json_rpc_env, read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.research.wrangle_vault_prices import replace_cleaned_vault_histories
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.denomination import DenominationFamily, classify_denomination
from eth_defi.vault.historical import MONAD_CHAIN_ID, fetch_monad_historical_state_start_block, pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.strategy_tag import lookup_strategy_tags
from eth_defi.vault.vaultdb import DEFAULT_RAW_PRICE_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_VAULT_DATABASE, VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Verified Nest chains, shared with the classification probe's allow-list.
MIGRATION_CHAIN_IDS = frozenset(CHAIN_RESTRICTED_PROBES["operatorRegistry"])


@dataclass(slots=True, frozen=True)
class NestMigrationResult:
    """Report the metadata changes prepared for one selected chain.

    Counts describe the verified migration plan. They do not imply that an
    applied metadata write or historical price scan has completed.
    """

    #: Chain whose contracts were verified.
    chain_id: int

    #: Number of catalogue routes inspected.
    routes: int

    #: Number of metadata rows to write.
    rows: int

    #: Number of missing lead records to write.
    leads: int


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse a strict boolean environment setting.

    Reject misspellings so an intended dry run cannot silently become an
    applied migration.

    :param name:
        Environment variable name.
    :param default:
        Value to use when absent.
    :return:
        Parsed boolean.
    :raises ValueError:
        If the supplied value is not a recognised boolean.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    normalised = value.strip().lower()
    if normalised in {"1", "true", "yes"}:
        return True
    if normalised in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true or false, got {value!r}")


def parse_networks(value: str | None) -> frozenset[int] | None:
    """Resolve explicit Nest chain names or IDs from ``NETWORKS``.

    With no selection, defer to the active supported catalogue chains.
    Explicit names follow Nest's catalogue aliases.

    :param value:
        Comma-separated chain names or decimal IDs.
    :return:
        Explicit supported chains, or ``None`` to select active chains.
    :raises ValueError:
        If a name is missing or has no verified migration support.
    """
    names = {name.strip().lower() for name in (value or "").split(",") if name.strip()}
    if not names:
        return None
    by_name = {name: chain_id for chain_id, name in NEST_CHAIN_NAMES.items()}
    chains = {int(name) if name.isdecimal() else by_name.get(name) for name in names}
    if None in chains or not chains <= MIGRATION_CHAIN_IDS:
        raise ValueError(f"NETWORKS contains an unsupported Nest chain: {value!r}; supported: {', '.join(NEST_CHAIN_NAMES[chain] for chain in sorted(MIGRATION_CHAIN_IDS))}")
    return frozenset(chains)


def select_active_routes(vaults: dict[str, NestVaultMetadata], chain_ids: frozenset[int] | None) -> dict[int, list[NestVaultMetadata]]:
    """Select active Nest routes with published historical start blocks.

    Disabled, hidden and test products are not migrated into the public vault
    listing. A missing start block is an error rather than a silent skip.
    The published block is a lower bound and may precede an entrypoint's
    actual deployment; it does not prove bytecode existed at that block.

    :param vaults:
        Current first-party Nest catalogue keyed by chain and address.
    :param chain_ids:
        Explicit verified chains, or ``None`` for all active supported chains.
    :return:
        Selected routes grouped by chain.
    :raises ValueError:
        If a selected active route lacks a start block or a chain is empty.
    """
    if chain_ids is None:
        chain_ids = frozenset(route["chain_id"] for route in vaults.values() if route["status"] == "active" and route["chain_id"] in MIGRATION_CHAIN_IDS)
    if not chain_ids:
        message = "Nest API returned no active routes on supported migration chains"
        raise ValueError(message)
    selected: dict[int, list[NestVaultMetadata]] = {chain_id: [] for chain_id in chain_ids}
    for route in vaults.values():
        chain_id = route["chain_id"]
        if chain_id not in chain_ids or route["status"] != "active":
            continue
        if not isinstance(route["start_block"], int) or route["start_block"] <= 0:
            raise ValueError(f"Nest route {chain_id}:{route['vault_address']} has no published start block")
        selected[chain_id].append(route)
    if empty := [chain_id for chain_id, routes in selected.items() if not routes]:
        raise ValueError(f"Nest API returned no active routes for chains {empty}")
    return selected


def create_backup_path(vault_db_path: Path) -> Path:
    """Choose a unique sibling backup without overwriting an earlier run.

    Retain earlier backups by adding a numeric suffix when necessary.

    :param vault_db_path:
        Metadata pickle about to be updated.
    :return:
        Available backup path.
    """
    backup = Path(f"{vault_db_path}.bak-nest-vaults")
    suffix = 1
    while backup.exists():
        backup = Path(f"{vault_db_path}.bak-nest-vaults.{suffix}")
        suffix += 1
    return backup


def create_lead(detection: ERC4262VaultDetection) -> PotentialVaultMatch:
    """Restore a missing lead without losing observed discovery counts.

    Use the saved detection's deployment lower bound and activity counts;
    creating the lead does not advance a discovery cursor.

    :param detection:
        Verified new or persisted Nest detection.
    :return:
        Equivalent discovery lead.
    """
    return PotentialVaultMatch(
        chain=detection.chain,
        address=HexAddress(detection.address.lower()),
        first_seen_at_block=detection.first_seen_at_block,
        first_seen_at=detection.first_seen_at,
        deposit_count=detection.deposit_count,
        withdrawal_count=detection.redeem_count,
        configuration_count=detection.configuration_count,
    )


def needs_refresh(row: VaultRow | None, route: NestVaultMetadata, features: set[ERC4626Feature]) -> bool:
    """Check whether a saved row lacks current Nest classification or content.

    Compare the same first-party fields used for historical row updates.
    Onchain TVL alone does not trigger a rebuild; the normal scanner refreshes
    that metric.

    :param row:
        Saved scanner row, if any.
    :param route:
        Current first-party route metadata.
    :param features:
        Current onchain classification.
    :return:
        Whether to rebuild the scanner metadata row.
    """
    if row is None:
        return True
    expected = get_metadata_fields(route, VaultSpec(route["chain_id"], route["vault_address"]))
    expected.pop("_nest_offchain_data")
    saved_snapshot = row.get("_nest_offchain_data") or {}
    live_fields = {"reported_apy", "tvl_usd", "num_holders", "volume_24h_usd"}
    return row.get("Protocol") != "Nest" or row.get("features") != features or row["_detection_data"].features != features or any(row.get(key) != value for key, value in expected.items()) or any(saved_snapshot.get(key) != value for key, value in route.items() if key not in live_fields)


def get_metadata_fields(route: NestVaultMetadata, spec: VaultSpec) -> dict[str, object]:
    """Assemble the first-party fields shared by all Nest metadata repairs.

    Local onchain names remain authoritative on legacy chains. Only active
    Arc routes use the CMS display name and a direct product link.

    :param route: First-party catalogue and CMS snapshot.
    :param spec: Chain and address of the persisted row.
    :return: Metadata fields to compare or apply without changing scan state.
    """
    fields: dict[str, object] = {
        "_manager_name": select_nest_manager_name(route["slug"], route.get("yield_source_partners", [])),
        "_curator_slug": NEST_CURATOR_SLUG,
        "_short_description": route.get("short_description"),
        "_description": route.get("description"),
        "_nest_offchain_data": route,
        "Link": "https://app.nest.credit/vaults",
    }
    if spec.chain_id == NEST_ARC_CHAIN_ID and route.get("status") == "active":
        fields["Link"] = f"https://app.nest.credit/vaults/{route['slug']}"
        if display_name := route.get("display_name"):
            fields["Name"] = display_name
    if tags := lookup_strategy_tags(STRATEGY_TAGS, spec.vault_address):
        fields["_strategy_tags"] = tags
    return fields


def plan_existing_metadata_updates(vault_db: VaultDatabase, routes: dict[str, NestVaultMetadata], replacements: dict[VaultSpec, VaultRow], chain_ids: frozenset[int] | None = None) -> dict[VaultSpec, dict[str, object]]:
    """Refresh first-party fields on existing Nest rows not rebuilt onchain.

    This includes historical rows on the selected chains. Unknown routes
    retain their saved content, and the planner never mutates the database.

    :param vault_db:
        Persisted scanner database.
    :param routes:
        Fresh chain-and-address keyed Nest catalogue.
    :param replacements:
        Rows already fully rebuilt by the onchain migration.
    :param chain_ids:
        Restrict a focused repair to its selected chains. ``None`` checks all.
    :return:
        Changed first-party fields keyed by vault identity.
    """
    updates: dict[VaultSpec, dict[str, object]] = {}
    for spec, row in vault_db.rows.items():
        if row.get("Protocol") != "Nest" or spec in replacements or (chain_ids is not None and spec.chain_id not in chain_ids):
            continue
        route = routes.get(f"{spec.chain_id}:{spec.vault_address.lower()}")
        if route is None:
            logger.warning("Nest row %s is absent from the current catalogue; preserving it", spec.as_string_id())
            continue
        expected = get_metadata_fields(route, spec)
        changed = {key: value for key, value in expected.items() if row.get(key) != value}
        if changed:
            updates[spec] = changed
    return updates


def prepare_chain_migration(
    web3: Web3,
    vault_db: VaultDatabase,
    routes: list[NestVaultMetadata],
    token_cache: TokenDiskCache,
    updated_at: datetime.datetime,
) -> tuple[dict[VaultSpec, VaultRow], dict[VaultSpec, PotentialVaultMatch], NestMigrationResult]:
    """Verify one chain and prepare its metadata changes without mutating state.

    Existing detector history is preserved. Newly seeded routes use Nest's
    published start block as a lower bound; no historical events or prices are inferred.
    Every route is verified onchain before the caller can save the pickle.

    :param web3:
        Connection to the route's actual EVM chain.
    :param vault_db:
        Persisted metadata database.
    :param routes:
        Active routes from Nest's current catalogue for one chain.
    :param token_cache:
        Temporary token metadata cache for scanner reads.
    :param updated_at:
        Naive UTC time of this migration.
    :return:
        Replacement rows, missing leads and a summary.
    :raises ValueError:
        If the provider, persisted state or contract classification is wrong.
    """
    chain_id = web3.eth.chain_id
    if chain_id not in MIGRATION_CHAIN_IDS or not routes or any(route["chain_id"] != chain_id for route in routes):
        raise ValueError(f"RPC chain {chain_id} does not match selected Nest routes")

    source_block = web3.eth.block_number
    replacements: dict[VaultSpec, VaultRow] = {}
    leads: dict[VaultSpec, PotentialVaultMatch] = {}
    report: list[dict[str, object]] = []

    for route in tqdm(routes, desc=f"Nest {NEST_CHAIN_NAMES[chain_id]} routes"):
        spec = VaultSpec(chain_id, route["vault_address"])
        logger.info("Checking Nest %s route %s", NEST_CHAIN_NAMES[chain_id], spec.vault_address)
        features = detect_vault_features(web3, spec.vault_address, verbose=False)
        if ERC4626Feature.nest_like not in features:
            raise ValueError(f"Nest route {spec} did not classify as Nest: {features}")

        row = vault_db.rows.get(spec)
        existing = row.get("_detection_data") if row else None
        if row is not None:
            if not isinstance(existing, ERC4262VaultDetection) or existing.chain != chain_id or existing.address.lower() != spec.vault_address:
                raise ValueError(f"Nest route {spec} has incompatible persisted detection")
            detection = dataclasses.replace(existing, features=features, updated_at=updated_at)
        else:
            detection = ERC4262VaultDetection(
                chain=chain_id,
                address=HexAddress(spec.vault_address),
                first_seen_at_block=route["start_block"],
                first_seen_at=updated_at,
                features=features,
                updated_at=updated_at,
                deposit_count=0,
                redeem_count=0,
            )

        refresh = needs_refresh(row, route, features)
        if refresh:
            rebuilt = create_vault_scan_record(web3, detection, source_block, token_cache)
            if rebuilt.get("Protocol") != "Nest" or (rebuilt.get("_nest_offchain_data") or {}).get("slug") != route["slug"]:
                raise ValueError(f"Nest route {spec} did not produce matching scanner metadata")
            rebuilt.update(get_metadata_fields(route, spec))
            replacements[spec] = rebuilt
        if spec not in vault_db.leads:
            leads[spec] = create_lead(detection)

        report.append({"chain": NEST_CHAIN_NAMES[chain_id], "slug": route["slug"], "address": spec.vault_address, "action": "refresh" if refresh else "current"})

    print(tabulate(report, headers="keys", tablefmt="github"))
    return replacements, leads, NestMigrationResult(chain_id=chain_id, routes=len(routes), rows=len(replacements), leads=len(leads))


def backfill_chain_history(
    web3: Web3,
    routes: list[NestVaultMetadata],
    vault_db: VaultDatabase,
    token_cache: TokenDiskCache,
    *,
    vault_db_path: Path,
    raw_price_path: Path,
    cleaned_price_path: Path,
    timestamp_cache_dir: Path,
    max_workers: int,
) -> dict[str, object]:
    """Backfill one chain's verified Nest routes with the shared price writers.

    HyperSync fills the dense timestamp cache before the historical reader
    starts. The scan is stateless, so it cannot advance or reset the scheduled
    reader-state pickle. Both Parquet writers replace selected addresses only.
    Every hourly observation is retained: Nest's daily NAV changes can be
    smaller than the shared reader's 10-basis-point sparse retention threshold.

    :param web3: Verified JSON-RPC connection for this chain.
    :param routes: Active Nest routes with published historical start blocks.
    :param vault_db: Updated metadata, including verified feature detections.
    :param token_cache: Token details shared with the metadata stage.
    :param vault_db_path: Metadata file used by the cleaned-price writer.
    :param raw_price_path: Existing raw price Parquet destination.
    :param cleaned_price_path: Existing cleaned price Parquet destination.
    :param timestamp_cache_dir: Persistent per-chain dense timestamp cache.
    :param max_workers: Maximum threaded historical RPC workers.
    :return: Chain summary with raw and cleaned row counts.
    :raises ValueError: If a route cannot be represented by a Nest reader.
    """
    chain_id = web3.eth.chain_id
    if not routes or any(route["chain_id"] != chain_id for route in routes):
        raise ValueError(f"RPC chain {chain_id} does not match selected Nest history routes")
    start_block = min(route["start_block"] for route in routes)
    end_block = web3.eth.block_number
    if end_block <= start_block:
        raise ValueError(f"Nest history on chain {chain_id} has no blocks after catalogue start {start_block}")
    if chain_id == MONAD_CHAIN_ID:
        start_block = fetch_monad_historical_state_start_block(web3, start_block=start_block, end_block=end_block)
    specs = [VaultSpec(chain_id, route["vault_address"]) for route in routes]
    addresses = {spec.vault_address for spec in specs}
    cleanable_ids = {spec.as_string_id() for spec in specs if classify_denomination(vault_db.rows[spec].get("Denomination")) == DenominationFamily.stablecoin}
    hypersync_client = configure_hypersync_from_env(web3).hypersync_client
    if hypersync_client is None:
        raise ValueError(f"HyperSync is required to backfill Nest history on chain {chain_id}")

    logger.info("Filling chain %d timestamp cache for blocks %d-%d", chain_id, start_block, end_block - 1)
    timestamps = fetch_block_timestamps_using_hypersync_cached(
        hypersync_client,
        chain_id,
        start_block,
        end_block - 1,
        cache_path=timestamp_cache_dir,
        attempts=1,
        chunk_size=500_000,
    )
    timestamps.close()

    vaults = []
    for route in tqdm(routes, desc=f"Nest {NEST_CHAIN_NAMES[chain_id]} history readers"):
        spec = VaultSpec(chain_id, route["vault_address"])
        features = vault_db.rows[spec]["_detection_data"].features
        vault = create_vault_instance(web3, spec.vault_address, features=features, token_cache=token_cache)
        if vault is None or ERC4626Feature.nest_like not in vault.features:
            raise ValueError(f"Could not initialise Nest historical reader for {spec}")
        vault.first_seen_at_block = route["start_block"]
        vaults.append(vault)

    scan_result = scan_historical_prices_to_parquet(
        output_fname=raw_price_path,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(read_json_rpc_url(chain_id), retries=5),
        vaults=vaults,
        start_block=start_block,
        end_block=end_block,
        max_workers=max_workers,
        chunk_size=32,
        token_cache=token_cache,
        frequency="1h",
        write_all_samples=True,
        reader_states=None,
        hypersync_client=hypersync_client,
        timestamp_cache_file=timestamp_cache_dir,
        vault_addresses=addresses,
    )
    cleaned_rows = (
        replace_cleaned_vault_histories(
            cleanable_ids,
            vault_db_path=vault_db_path,
            raw_price_df_path=raw_price_path,
            cleaned_price_df_path=cleaned_price_path,
            logger=logger.info,
        )
        if cleanable_ids
        else 0
    )
    logger.info("Completed Nest history on chain %d: %s; cleaned_rows=%d; unsupported denominations=%d", chain_id, pformat_scan_result(scan_result), cleaned_rows, len(routes) - len(cleanable_ids))
    return {"chain": NEST_CHAIN_NAMES[chain_id], "routes": len(routes), "scan": pformat_scan_result(scan_result), "cleaned_rows": cleaned_rows, "unsupported_denominations": len(routes) - len(cleanable_ids)}


def validate_history_paths(vault_db_path: Path, raw_price_path: Path, cleaned_price_path: Path, timestamp_cache_dir: Path) -> None:
    """Reject missing or mixed storage before any metadata or price write.

    Isolated metadata requires private price and timestamp paths so a local
    repair cannot accidentally replace the shared scanner's histories.

    :param vault_db_path: Metadata pickle to migrate.
    :param raw_price_path: Raw price Parquet to update.
    :param cleaned_price_path: Cleaned price Parquet to update.
    :param timestamp_cache_dir: Persistent timestamp cache directory.
    :return: ``None`` when the storage paths are safe for an applied run.
    :raises ValueError: If an isolated metadata copy points at shared prices.
    :raises FileNotFoundError: If a required price file is absent.
    """
    if vault_db_path.resolve() != DEFAULT_VAULT_DATABASE.resolve():
        if not all(os.environ.get(name) for name in ("UNCLEANED_PRICE_DATABASE", "CLEANED_PRICE_DATABASE", "TIMESTAMP_CACHE_DIR")):
            message = "Isolated VAULT_DB_PATH requires UNCLEANED_PRICE_DATABASE, CLEANED_PRICE_DATABASE and TIMESTAMP_CACHE_DIR for a history backfill"
            raise ValueError(message)
        shared_price_paths = {DEFAULT_UNCLEANED_PRICE_DATABASE.resolve(), DEFAULT_RAW_PRICE_DATABASE.resolve()}
        if raw_price_path.resolve() in shared_price_paths or cleaned_price_path.resolve() in shared_price_paths or raw_price_path.resolve().parent != vault_db_path.resolve().parent or cleaned_price_path.resolve().parent != vault_db_path.resolve().parent or timestamp_cache_dir.resolve() == DEFAULT_TIMESTAMP_CACHE_FOLDER.resolve():
            message = "Isolated Nest history paths must use private Parquet copies beside VAULT_DB_PATH and a private timestamp cache directory"
            raise ValueError(message)
    if raw_price_path.resolve() == cleaned_price_path.resolve():
        message = "Raw and cleaned Nest price Parquet paths must differ"
        raise ValueError(message)
    for path in (raw_price_path, cleaned_price_path):
        if not path.is_file():
            raise FileNotFoundError(f"Historical price Parquet is missing: {path}")
    if not timestamp_cache_dir.is_dir():
        raise FileNotFoundError(f"Persistent timestamp cache directory is missing: {timestamp_cache_dir}")
    if not os.environ.get("HYPERSYNC_API_KEY"):
        message = "HYPERSYNC_API_KEY is required for the Nest historical backfill"
        raise ValueError(message)


def run_migration(*, dry_run: bool, scan_prices: bool, vault_db_path: Path) -> None:  # noqa: PLR0914 - stages share the selected catalogue and verified readers.
    """Verify all routes, persist metadata and backfill their history in order.

    A persistent run holds the shared pipeline writer lock around this entire
    function. After an interruption, a rerun can safely repeat any completed
    address-scoped Parquet replacement without touching unrelated vaults.

    :param dry_run: Print a fully verified, non-mutating migration plan.
    :param scan_prices: Include the historical price stage when applying.
    :param vault_db_path: Metadata pickle to read and atomically update.
    :return: ``None`` after printing the plan or applied results.
    """
    if not vault_db_path.exists():
        raise ValueError(f"Vault metadata database does not exist: {vault_db_path}")
    raw_price_path = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", str(DEFAULT_UNCLEANED_PRICE_DATABASE))).expanduser()
    cleaned_price_path = Path(os.environ.get("CLEANED_PRICE_DATABASE", str(DEFAULT_RAW_PRICE_DATABASE))).expanduser()
    timestamp_cache_dir = Path(os.environ.get("TIMESTAMP_CACHE_DIR", str(DEFAULT_TIMESTAMP_CACHE_FOLDER))).expanduser()
    max_workers = int(os.environ.get("MAX_WORKERS", "8"))
    if max_workers < 1:
        message = "MAX_WORKERS must be positive"
        raise ValueError(message)

    with tempfile.TemporaryDirectory(prefix="nest-migration-catalogue-") as catalogue_cache:
        catalogue = fetch_nest_vaults(cache_path=Path(catalogue_cache), max_cache_duration=datetime.timedelta(0), allow_stale=False)
    routes_by_chain = select_active_routes(catalogue, parse_networks(os.environ.get("NETWORKS")))
    omitted = sorted({route["chain_id"] for route in catalogue.values() if route["status"] == "active"} - routes_by_chain.keys())
    if omitted:
        logger.warning("Active Nest routes outside the selected supported chains: %s", ", ".join(NEST_CHAIN_NAMES.get(chain_id, str(chain_id)) for chain_id in omitted))
    print(tabulate([{"chain": NEST_CHAIN_NAMES[chain_id], "routes": len(routes), "first_block": min(route["start_block"] for route in routes), "rpc": get_json_rpc_env(chain_id), "timestamp_cache": (timestamp_cache_dir / f"{chain_id}-timestamps.duckdb").exists()} for chain_id, routes in sorted(routes_by_chain.items())], headers="keys", tablefmt="github"))

    vault_db = VaultDatabase.read(vault_db_path)
    replacements: dict[VaultSpec, VaultRow] = {}
    leads: dict[VaultSpec, PotentialVaultMatch] = {}
    results: list[NestMigrationResult] = []
    web3_by_chain: dict[int, Web3] = {}
    with tempfile.TemporaryDirectory(prefix="nest-vaults-token-cache-") as cache_directory:
        token_cache = TokenDiskCache(Path(cache_directory) / "tokens.sqlite")
        for chain_id, routes in sorted(routes_by_chain.items()):
            web3 = create_multi_provider_web3(read_json_rpc_url(chain_id))
            web3_by_chain[chain_id] = web3
            rows, new_leads, result = prepare_chain_migration(web3, vault_db, routes, token_cache, native_datetime_utc_now())
            replacements.update(rows)
            leads.update(new_leads)
            results.append(result)

        metadata_updates = plan_existing_metadata_updates(vault_db, catalogue, replacements, frozenset(routes_by_chain))
        print(tabulate([dataclasses.asdict(result) for result in results], headers="keys", tablefmt="github"))
        if dry_run:
            history_plan = f"{sum(len(routes) for routes in routes_by_chain.values())} active Nest histories planned at hourly frequency" if scan_prices else "historical price scan disabled"
            print(f"Dry run: {history_plan}; metadata, reader state and price files unchanged")
            return

        if scan_prices:
            validate_history_paths(vault_db_path, raw_price_path, cleaned_price_path, timestamp_cache_dir)
        if replacements or leads or metadata_updates:
            vault_db.rows.update(replacements)
            for spec, fields in metadata_updates.items():
                vault_db.rows[spec].update(fields)
            vault_db.leads.update(leads)
            backup_path = create_backup_path(vault_db_path)
            shutil.copy2(vault_db_path, backup_path)
            vault_db.write(vault_db_path)
            print(f"Rebuilt {len(replacements)} Nest rows, refreshed {len(metadata_updates)} existing rows and restored {len(leads)} leads; backup: {backup_path}")
        else:
            print("Nest metadata is already current")

        if scan_prices:
            history_results = [
                backfill_chain_history(
                    web3_by_chain[chain_id],
                    routes,
                    vault_db,
                    token_cache,
                    vault_db_path=vault_db_path,
                    raw_price_path=raw_price_path,
                    cleaned_price_path=cleaned_price_path,
                    timestamp_cache_dir=timestamp_cache_dir,
                    max_workers=max_workers,
                )
                for chain_id, routes in sorted(routes_by_chain.items())
            ]
            print(tabulate(history_results, headers="keys", tablefmt="github"))


def main() -> None:
    """Run the Nest migration under the shared writer lock when applying.

    Read environment settings, set up observable logging and hold the common
    pipeline lock for the complete applied migration.

    :return: ``None`` after the plan or historical backfill completes.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"), log_file=Path("logs/migrate-nest-vaults.log"))
    dry_run = parse_bool_env("DRY_RUN", default=True)
    scan_prices = parse_bool_env("NEST_SCAN_PRICES", default=True)
    vault_db_path = Path(os.environ.get("VAULT_DB_PATH", str(DEFAULT_VAULT_DATABASE))).expanduser()
    lock_timeout = int(os.environ.get("PIPELINE_LOCK_TIMEOUT", "60"))
    lock = nullcontext() if dry_run else wait_other_writers(vault_db_path.parent.resolve() / "scan-pipeline", timeout=lock_timeout)
    with lock:
        run_migration(dry_run=dry_run, scan_prices=scan_prices, vault_db_path=vault_db_path)


if __name__ == "__main__":
    main()
