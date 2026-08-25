"""Migrate metadata and historical prices for fixed Rysk Premium pools.

This one-off production migration has two functions. The metadata stage
rebuilds exactly eight reviewed Ethereum and HyperEVM pool rows and their lead
records through the normal :class:`RyskVault` adapter. The historical stage
reconstructs their final epoch prices from onchain events through the common
writer. Both stages use the same fixed deployment boundaries and run in that
order from one entry point.

Usage::

    # Default: perform real reads for both stages without persistent writes
    poetry run python scripts/erc-4626/migrate-rysk-vaults.py

    # Apply the exact reviewed migration
    DRY_RUN=false poetry run python scripts/erc-4626/migrate-rysk-vaults.py

Environment variables:

- ``DRY_RUN``: use temporary token, timestamp, context and price storage and
  leave all production files unchanged; defaults to ``true``.
- ``MAX_WORKERS``: common historical writer thread count; defaults to ``4``.
- Standard Ethereum and HyperEVM RPC, Hypersync and logging environment
  variables are infrastructure configuration, not migration scope.
"""

import logging
import os
import shutil
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from tabulate import tabulate
from web3 import Web3

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.erc_4626.classification import detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.rysk.historical_context import fetch_and_store_rysk_premium_history
from eth_defi.erc_4626.vault_protocol.rysk.migration import RYSK_MIGRATION_CHAIN_IDS, RyskMigrationPool, iter_rysk_migration_pools, parse_rysk_migration_dry_run
from eth_defi.erc_4626.vault_protocol.rysk.vault import RyskVault
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow, get_pipeline_data_dir

RYSK_MIGRATION_FEATURES = {ERC4626Feature.rysk_premium_like, ERC4626Feature.share_price_equivalence}
RYSK_BACKFILL_FREQUENCY = "1h"
RYSK_DEFAULT_MAX_WORKERS = 4

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class RyskMetadataMigrationResult:
    """Summarise the fixed-scope Rysk metadata repair.

    The counts distinguish new rows from existing rows rebuilt through the
    maintained Rysk adapter.
    """

    #: Reviewed rows constructed successfully.
    pools: int
    #: Rows absent from the input metadata database.
    inserted: int
    #: Existing target rows refreshed.
    updated: int


@dataclass(slots=True, frozen=True)
class RyskMetadataReplacement:
    """Hold one validated row and lead replacement before database mutation."""

    #: Common vault identity.
    spec: VaultSpec
    #: Rebuilt metadata merged over any existing row.
    row: VaultRow
    #: Lead record using the reviewed deployment boundary.
    lead: PotentialVaultMatch
    #: Human-readable ``insert`` or ``update`` action.
    action: str


def _read_activity_counts(existing: VaultRow | None, existing_lead: PotentialVaultMatch | None) -> tuple[int, int, int]:
    """Preserve previously observed activity while repairing metadata.

    :param existing:
        Existing target metadata row, when present.
    :param existing_lead:
        Existing target lead, when present.
    :return:
        Deposit, redemption and configuration event counts.
    """

    existing_detection = existing.get("_detection_data") if existing is not None else None
    if isinstance(existing_detection, ERC4262VaultDetection):
        return existing_detection.deposit_count, existing_detection.redeem_count, getattr(existing_detection, "configuration_count", 0)
    if existing_lead is not None:
        return existing_lead.deposit_count, existing_lead.withdrawal_count, existing_lead.configuration_count
    return 0, 0, 0


def _build_metadata_replacement(web3: Web3, pool: RyskMigrationPool, vault_db: VaultDatabase, token_cache: TokenDiskCache, metadata_block: int) -> RyskMetadataReplacement:
    """Build and validate one reviewed Rysk row without mutating the database.

    :param web3:
        Pool-chain connection.
    :param pool:
        Fixed migration target.
    :param vault_db:
        Metadata database used as the merge base.
    :param token_cache:
        Token metadata cache for common row construction.
    :param metadata_block:
        Fixed current-state block for this chain run.
    :return:
        Validated row and lead replacement.
    :raises RuntimeError:
        If the target does not rebuild as Rysk.
    """

    features = detect_vault_features(web3, pool.address, verbose=False)
    if ERC4626Feature.rysk_premium_like not in features:
        feature_names = sorted(feature.value for feature in features)
        raise RuntimeError(f"Rysk migration target {pool.chain_id}-{pool.address} failed the shared onchain classifier: {feature_names}")

    first_seen_at = native_datetime_utc_fromtimestamp(web3.eth.get_block(pool.deployment_block)["timestamp"])
    spec = VaultSpec(pool.chain_id, pool.address)
    existing = vault_db.rows.get(spec)
    deposit_count, redeem_count, configuration_count = _read_activity_counts(existing, vault_db.leads.get(spec))
    detection = ERC4262VaultDetection(
        chain=pool.chain_id,
        address=pool.address,
        first_seen_at_block=pool.deployment_block,
        first_seen_at=first_seen_at,
        features=features,
        updated_at=native_datetime_utc_now(),
        deposit_count=deposit_count,
        redeem_count=redeem_count,
        configuration_count=configuration_count,
    )
    rebuilt = create_vault_scan_record(web3, detection, metadata_block, token_cache)
    rebuilt_name = rebuilt.get("Name") or ""
    if rebuilt.get("Protocol") != "Rysk" or not rebuilt_name or rebuilt_name.startswith("<broken:"):
        raise RuntimeError(f"Rysk migration target {pool.chain_id}-{pool.address} rebuilt as {rebuilt.get('Protocol')!r} / {rebuilt.get('Name')!r}")

    merged = rebuilt if existing is None else existing | rebuilt
    if existing is not None and rebuilt.get("_strategy_tags") is None and existing.get("_strategy_tags") is not None:
        merged["_strategy_tags"] = existing["_strategy_tags"]
    lead = PotentialVaultMatch(
        chain=pool.chain_id,
        address=pool.address,
        first_seen_at_block=pool.deployment_block,
        first_seen_at=first_seen_at,
        deposit_count=deposit_count,
        withdrawal_count=redeem_count,
        configuration_count=configuration_count,
    )
    return RyskMetadataReplacement(spec=spec, row=merged, lead=lead, action="insert" if existing is None else "update")


def migrate_rysk_metadata(vault_db: VaultDatabase, token_cache: TokenDiskCache, *, dry_run: bool) -> RyskMetadataMigrationResult:
    """Build and optionally apply all reviewed Rysk metadata rows.

    Every RPC read and row validation completes before the in-memory database
    is mutated. Existing target dictionaries are the merge base so manual or
    future fields outside the adapter-owned scan record are retained.

    :param vault_db:
        Production metadata database or a test fixture.
    :param token_cache:
        Token metadata cache used by common scan-row construction.
    :param dry_run:
        Leave ``vault_db`` unchanged when ``True``.
    :return:
        Constructed, inserted and updated row counts.
    :raises RuntimeError:
        If an RPC has the wrong chain or any reviewed pool cannot be rebuilt as
        a valid Rysk row.
    """

    replacements: dict[VaultSpec, VaultRow] = {}
    replacement_leads: dict[VaultSpec, PotentialVaultMatch] = {}
    report_rows: list[tuple[int, str, int, str, str]] = []
    inserted = 0

    for chain_id in RYSK_MIGRATION_CHAIN_IDS:
        web3 = create_multi_provider_web3(read_json_rpc_url(chain_id))
        if web3.eth.chain_id != chain_id:
            raise RuntimeError(f"Rysk migration RPC returned chain ID {web3.eth.chain_id}, expected {chain_id}")
        metadata_block = web3.eth.block_number

        for pool in iter_rysk_migration_pools(chain_id):
            replacement = _build_metadata_replacement(web3, pool, vault_db, token_cache, metadata_block)
            if replacement.action == "insert":
                inserted += 1
            replacements[replacement.spec] = replacement.row
            replacement_leads[replacement.spec] = replacement.lead
            report_rows.append((chain_id, pool.address, pool.deployment_block, replacement.action, str(replacement.row["Name"])))

    print(tabulate(report_rows, headers=("Chain", "Pool", "Deployment block", "Action", "Name"), tablefmt="rounded_outline"))
    if not dry_run:
        vault_db.rows.update(replacements)
        vault_db.leads.update(replacement_leads)
    return RyskMetadataMigrationResult(pools=len(replacements), inserted=inserted, updated=len(replacements) - inserted)


def fetch_rysk_full_backfill_range(web3: Web3, pools: tuple[RyskMigrationPool, ...]) -> tuple[int, int]:
    """Resolve the complete safe range for one fixed Rysk chain scope.

    Rysk value events cannot predate their pool deployments. The common safe
    head leaves the provider-specific confirmation margin used by ordinary
    historical scans.

    :param web3:
        Ethereum or HyperEVM connection.
    :param pools:
        Reviewed targets on the connected chain.
    :return:
        Inclusive earliest deployment and exclusive reorg-safe end block.
    """

    if not pools:
        message = "Rysk backfill range needs at least one reviewed pool"
        raise ValueError(message)
    return min(pool.deployment_block for pool in pools), get_almost_latest_block_number(web3)


def _backfill_rysk_history_chain(
    *,
    chain_id: int,
    price_database: Path,
    context_database: Path,
    token_cache_path: Path,
    timestamp_cache_path: Path,
    max_workers: int,
) -> None:
    """Backfill every reviewed Rysk pool on one migration chain.

    :param chain_id:
        Fixed Ethereum or HyperEVM chain identifier.
    :param price_database:
        Common raw historical-price Parquet output.
    :param context_database:
        Shared contextual-history DuckDB output.
    :param token_cache_path:
        Token metadata cache path.
    :param timestamp_cache_path:
        Per-chain execution-block timestamp-cache directory.
    :param max_workers:
        Common historical writer thread count.
    :return:
        None.
    """

    rpc_url = read_json_rpc_url(chain_id)
    web3 = create_multi_provider_web3(rpc_url)
    if web3.eth.chain_id != chain_id:
        raise RuntimeError(f"Rysk migration RPC returned chain ID {web3.eth.chain_id}, expected {chain_id}")

    pools = tuple(iter_rysk_migration_pools(chain_id))
    start_block, end_block = fetch_rysk_full_backfill_range(web3, pools)
    hypersync_client = configure_hypersync_from_env(web3).hypersync_client
    if hypersync_client is None:
        raise RuntimeError(f"Rysk Premium backfill on chain {chain_id} requires Hypersync")

    prefill = fetch_and_store_rysk_premium_history(
        web3=web3,
        hypersync_client=hypersync_client,
        pool_start_blocks={pool.address: pool.deployment_block for pool in pools},
        end_block=end_block,
        context_path=context_database,
        timestamp_cache_path=timestamp_cache_path,
    )

    token_cache = TokenDiskCache(token_cache_path)
    try:
        vaults = []
        for pool in pools:
            vault = RyskVault(web3, VaultSpec(chain_id, pool.address), token_cache=token_cache, features=RYSK_MIGRATION_FEATURES)
            vault.first_seen_at_block = pool.deployment_block
            vault.historical_context_path = context_database
            vaults.append(vault)

        price_database.parent.mkdir(parents=True, exist_ok=True)
        result = scan_historical_prices_to_parquet(
            output_fname=price_database,
            web3=web3,
            web3factory=MultiProviderWeb3Factory(rpc_url),
            vaults=vaults,
            token_cache=token_cache,
            start_block=start_block,
            end_block=end_block,
            max_workers=max_workers,
            frequency=RYSK_BACKFILL_FREQUENCY,
            hypersync_client=hypersync_client,
            timestamp_cache_file=timestamp_cache_path,
            vault_addresses={vault.address.lower() for vault in vaults},
        )
        token_cache.commit()
    finally:
        token_cache.close()

    print(f"Rysk chain {chain_id}: pools={len(pools)}, final epochs fetched={prefill.observations_fetched}, inserted={prefill.observations_inserted}\n{pformat_scan_result(result)}")


def backfill_rysk_history(
    *,
    price_database: Path,
    context_database: Path,
    token_cache_path: Path,
    timestamp_cache_path: Path,
    max_workers: int,
) -> None:
    """Backfill historical prices for the complete reviewed Rysk scope.

    The two chains run in fixed order and share the same selected output
    stores. Each chain writes only its four reviewed pool addresses through the
    common historical writer.

    :param price_database:
        Common raw historical-price Parquet output.
    :param context_database:
        Shared contextual-history DuckDB output.
    :param token_cache_path:
        Token metadata cache path.
    :param timestamp_cache_path:
        Per-chain execution-block timestamp-cache directory.
    :param max_workers:
        Common historical writer thread count.
    :return:
        None.
    """

    for chain_id in RYSK_MIGRATION_CHAIN_IDS:
        _backfill_rysk_history_chain(
            chain_id=chain_id,
            price_database=price_database,
            context_database=context_database,
            token_cache_path=token_cache_path,
            timestamp_cache_path=timestamp_cache_path,
            max_workers=max_workers,
        )


def _prepare_dry_run_price_database(price_database: Path, temporary: Path) -> Path:
    """Copy production Parquet into the mounted temporary workspace.

    The common writer creates an atomic replacement beside its output, so the
    workspace needs room for both the copied input and rewritten output.

    :param price_database:
        Production raw historical-price Parquet path.
    :param temporary:
        Dry-run workspace on the mounted pipeline volume.
    :return:
        Temporary Parquet path, whether or not production data exists yet.
    :raises RuntimeError:
        If the mounted volume cannot hold the copy and atomic rewrite.
    """

    dry_run_price_database = temporary / price_database.name
    if not price_database.exists():
        return dry_run_price_database

    parquet_size = price_database.stat().st_size
    required_free_space = parquet_size * 2
    available_free_space = shutil.disk_usage(temporary).free
    if available_free_space < required_free_space:
        raise RuntimeError(f"Rysk dry run needs at least {required_free_space:,} free bytes on the pipeline volume to copy and rewrite {price_database}; only {available_free_space:,} bytes are available")
    logger.info(
        "Copying production Parquet (%d bytes) to %s for a non-persistent merge rehearsal; %d bytes are free",
        parquet_size,
        dry_run_price_database,
        available_free_space,
    )
    shutil.copy2(price_database, dry_run_price_database)
    logger.info("Copied production Parquet to %s", dry_run_price_database)
    return dry_run_price_database


def main() -> None:
    """Run both functions of the fixed Rysk production migration.

    Dry-run mode reads production metadata and real external services but uses
    temporary token, timestamp, context and price stores. Persistent mode
    holds one shared writer lock while it repairs metadata and then backfills
    history.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    dry_run = parse_rysk_migration_dry_run(os.environ.get("DRY_RUN"))
    max_workers = int(os.environ.get("MAX_WORKERS", str(RYSK_DEFAULT_MAX_WORKERS)))
    if max_workers <= 0:
        raise ValueError(f"MAX_WORKERS must be positive, got {max_workers}")
    pipeline_dir = get_pipeline_data_dir()
    vault_db_path = pipeline_dir / "vault-metadata-db.pickle"
    if not vault_db_path.exists():
        raise FileNotFoundError(vault_db_path)

    lock = nullcontext() if dry_run else wait_other_writers(pipeline_dir / "scan-pipeline", timeout=60)
    temporary_parent = pipeline_dir if dry_run else None
    with lock, tempfile.TemporaryDirectory(prefix="rysk-migration-", dir=temporary_parent) as temporary_directory:
        temporary = Path(temporary_directory)
        vault_db = VaultDatabase.read(vault_db_path)
        token_cache_path = temporary / "tokens.sqlite" if dry_run else TokenDiskCache.DEFAULT_TOKEN_DISK_CACHE_PATH
        token_cache = TokenDiskCache(token_cache_path)
        try:
            result = migrate_rysk_metadata(vault_db, token_cache, dry_run=dry_run)
            if not dry_run:
                vault_db.write(vault_db_path)
                token_cache.commit()
        finally:
            token_cache.close()

        outcome = "Dry run; no files changed" if dry_run else f"Written atomically to {vault_db_path}"
        print(f"Rysk metadata migration: pools={result.pools}, inserted={result.inserted}, updated={result.updated}. {outcome}.")

        price_database = pipeline_dir / "vault-prices-1h.parquet"
        if dry_run:
            price_database = _prepare_dry_run_price_database(price_database, temporary)

        backfill_rysk_history(
            price_database=price_database,
            context_database=temporary / "vault-historical-context.duckdb" if dry_run else pipeline_dir / "vault-historical-context.duckdb",
            token_cache_path=token_cache_path,
            timestamp_cache_path=temporary / "block-timestamp" if dry_run else DEFAULT_TIMESTAMP_CACHE_FOLDER,
            max_workers=max_workers,
        )

    if dry_run:
        print("Dry run complete; production metadata, reader state, prices, context and caches were not changed")
    else:
        print("Rysk metadata and historical migration complete")


if __name__ == "__main__":
    main()
