"""Populate generic vault settlement data from ERC-4626 protocol readers.

This module owns the scan orchestration for sparse settlement events.
Production scanner calls select one chain from vault metadata and the
just-completed price scan end block. Standalone backfill calls can still select
ranges from the raw price parquet. Both paths batch event reads by chain and
route returned logs back to protocol-specific row builders.
"""

import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from eth_typing import HexAddress
from web3 import Web3
from web3.datastructures import AttributeDict
from web3.exceptions import Web3Exception

from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance
from eth_defi.erc_4626.core import MIN_PRICE_SCAN_DEPOSIT_COUNT, ERC4626Feature, is_activity_filter_exempt
from eth_defi.erc_4626.settlement_events import (
    fetch_vault_settlement_logs_for_addresses,
    normalise_log_topic,
)
from eth_defi.erc_4626.vault_protocol.d2.settlement import (
    build_d2_settlement_rows_from_logs,
    get_d2_settlement_events_by_topic,
)
from eth_defi.erc_4626.vault_protocol.d2.vault import D2Vault
from eth_defi.erc_4626.vault_protocol.lagoon.settlement import (
    build_settlement_rows_from_logs as build_lagoon_settlement_rows_from_logs,
)
from eth_defi.erc_4626.vault_protocol.lagoon.settlement import (
    get_settlement_events_by_topic,
)
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.vault.settlement_data import (
    VaultSettlement,
    VaultSettlementDatabase,
    get_default_vault_settlement_database_path,
)
from eth_defi.vault.vaultdb import (
    DEFAULT_UNCLEANED_PRICE_DATABASE,
    DEFAULT_VAULT_DATABASE,
    VaultDatabase,
    VaultRow,
)

logger = logging.getLogger(__name__)

SUPPORTED_SETTLEMENT_FEATURES = frozenset(
    {
        ERC4626Feature.lagoon_like,
        ERC4626Feature.d2_like,
    }
)


@dataclass(slots=True, frozen=True)
class VaultSettlementScanRange:
    """Block range where one vault needs settlement events scanned.

    :param chain_id:
        EVM chain id.
    :param address:
        Lowercase vault address.
    :param start_block:
        Inclusive start block.
    :param end_block:
        Inclusive end block.
    """

    chain_id: int
    address: HexAddress | str
    start_block: int
    end_block: int


@dataclass(slots=True, frozen=True)
class VaultSettlementScanResult:
    """Summary of a settlement scan run.

    :param candidate_vaults:
        Supported protocol vaults found in both metadata and raw prices.
    :param scanned_vaults:
        Vault ranges selected for chain settlement batches.
    :param skipped_vaults:
        Candidate vaults skipped because the existing database was already
        current for the raw price block range.
    :param rows_written:
        Settlement rows written to DuckDB.
    :param scanned_chains:
        Chains whose settlement batch completed.
    :param failed_chains:
        Chains whose settlement batch failed and was skipped.
    """

    candidate_vaults: int
    scanned_vaults: int
    skipped_vaults: int
    rows_written: int
    scanned_chains: int = 0
    failed_chains: int = 0


@dataclass(slots=True, frozen=True)
class PreparedSettlementVault:
    """Vault adapter and event metadata ready for a settlement log scan.

    :param vault:
        Protocol-specific vault adapter.
    :param event_by_topic:
        Normalised event topic to event class/name mapping.
    """

    vault: LagoonVault | D2Vault
    event_by_topic: dict[str, object]


@dataclass(slots=True, frozen=True)
class ChainSettlementUpdateResult:
    """Result of one per-chain settlement event batch.

    :param rows_written:
        Settlement rows written to DuckDB.
    :param scanned_vaults:
        Vault scan-state rows advanced after the event batch completed.
    """

    rows_written: int
    scanned_vaults: int


@dataclass(slots=True, frozen=True)
class SettlementRangeUpdateResult:
    """Result of executing one or more selected settlement ranges.

    :param rows_written:
        Settlement rows written to DuckDB.
    :param scanned_vaults:
        Vault scan-state rows advanced after completed event batches.
    :param scanned_chains:
        Chains whose settlement batch completed.
    :param failed_chains:
        Chains whose settlement batch failed and was skipped.
    """

    rows_written: int
    scanned_vaults: int
    scanned_chains: int
    failed_chains: int


def resolve_rpc_urls_by_chain_from_env(rpc_env_vars: list[str]) -> dict[int, str]:
    """Resolve configured JSON-RPC URLs to chain ids.

    The vault pipeline stores RPC endpoint names as environment variable names
    on ``ChainConfig``. Settlement scanning needs a chain-id keyed mapping so
    it can instantiate vault adapters for the chains found in raw price data.

    :param rpc_env_vars:
        Environment variable names containing JSON-RPC fallback URL strings.
    :return:
        Mapping ``chain_id -> RPC configuration string``.
    """
    rpc_urls_by_chain: dict[int, str] = {}
    for env_var in rpc_env_vars:
        rpc_url = os.environ.get(env_var)
        if not rpc_url:
            continue

        web3 = create_multi_provider_web3(rpc_url)
        chain_id = int(web3.eth.chain_id)
        rpc_urls_by_chain[chain_id] = rpc_url
        logger.info("Resolved %s to chain id %d for settlement scanning", env_var, chain_id)

    return rpc_urls_by_chain


def select_vault_settlement_scan_ranges(
    vault_db: VaultDatabase,
    raw_prices_df: pd.DataFrame,
    settlement_db: VaultSettlementDatabase,
    supported_features: set[ERC4626Feature] | frozenset[ERC4626Feature] = SUPPORTED_SETTLEMENT_FEATURES,
    forced_start_block: int | None = None,
    forced_end_block: int | None = None,
) -> list[VaultSettlementScanRange]:
    """Select supported vault block ranges that need settlement scans.

    The range is bounded by raw price data, because settlement markers are only
    useful where the cleaned price parquet has rows to annotate. Existing rows
    in ``vault-settlements.duckdb`` make normal scans incremental. Empty
    scans are tracked separately from sparse settlement event rows.

    :param vault_db:
        Vault metadata database.
    :param raw_prices_df:
        Raw price DataFrame with ``chain``, ``address`` and ``block_number``.
    :param settlement_db:
        Settlement database used to determine latest scanned settlement block.
    :param supported_features:
        Protocol feature flags whose event readers are available.
    :param forced_start_block:
        Optional operator-supplied inclusive start block for backfills.
    :param forced_end_block:
        Optional operator-supplied inclusive end block for backfills.
    :return:
        Scan ranges sorted by chain id and address.
    """
    if raw_prices_df.empty:
        return []

    required_columns = {"chain", "address", "block_number"}
    missing_columns = required_columns - set(raw_prices_df.columns)
    assert not missing_columns, f"Raw price DataFrame missing columns: {missing_columns}"

    raw_prices = raw_prices_df[["chain", "address", "block_number"]].copy()
    raw_prices["address"] = raw_prices["address"].astype(str).str.lower()
    raw_prices["chain"] = raw_prices["chain"].astype(int)
    raw_prices["block_number"] = raw_prices["block_number"].astype("int64")

    raw_ranges = raw_prices.groupby(["chain", "address"], sort=True)["block_number"].agg(["min", "max"])

    ranges: list[VaultSettlementScanRange] = []
    for row in vault_db.rows.values():
        features = _get_vault_features(row)
        if not features.intersection(supported_features):
            continue

        detection = row["_detection_data"]
        chain_id = int(detection.chain)
        address = str(detection.address).lower()
        key = (chain_id, address)
        if key not in raw_ranges.index:
            continue

        raw_min_block = int(raw_ranges.loc[key, "min"])
        raw_max_block = int(raw_ranges.loc[key, "max"])
        latest_scanned_block = settlement_db.get_latest_scanned_block_number(chain_id, address)
        latest_event_block = settlement_db.get_latest_block_number(chain_id, address)
        latest_known_block = _max_optional_int(latest_scanned_block, latest_event_block)

        start_block = raw_min_block
        if forced_start_block is None and latest_known_block is not None:
            start_block = max(start_block, latest_known_block + 1)
        if forced_start_block is not None:
            start_block = max(start_block, forced_start_block)

        end_block = raw_max_block
        if forced_end_block is not None:
            end_block = min(end_block, forced_end_block)

        if start_block <= end_block:
            ranges.append(
                VaultSettlementScanRange(
                    chain_id=chain_id,
                    address=address,
                    start_block=start_block,
                    end_block=end_block,
                )
            )

    return sorted(ranges, key=lambda item: (item.chain_id, str(item.address)))


def select_vault_settlement_scan_ranges_for_chain(
    vault_db: VaultDatabase,
    settlement_db: VaultSettlementDatabase,
    chain_id: int,
    end_block: int,
    supported_features: set[ERC4626Feature] | frozenset[ERC4626Feature] = SUPPORTED_SETTLEMENT_FEATURES,
    forced_start_block: int | None = None,
    forced_end_block: int | None = None,
) -> list[VaultSettlementScanRange]:
    """Select settlement scan ranges from vault metadata and chain scan state.

    Production per-chain scans already know the latest scanned chain block from
    the completed price scan. Using that block avoids re-reading the raw price
    parquet just to rediscover the chain-level end block.

    :param vault_db:
        Vault metadata database.
    :param settlement_db:
        Settlement database used to determine latest scanned settlement block.
    :param chain_id:
        Chain id to select.
    :param end_block:
        Latest block reached by the successful chain price scan.
    :param supported_features:
        Protocol feature flags whose event readers are available.
    :param forced_start_block:
        Optional operator-supplied inclusive start block for backfills.
    :param forced_end_block:
        Optional operator-supplied inclusive end block for backfills.
    :return:
        Scan ranges sorted by address.
    """
    assert end_block >= 0, f"Bad end block: {end_block}"

    ranges: list[VaultSettlementScanRange] = []
    for row in vault_db.rows.values():
        features = _get_vault_features(row)
        if not features.intersection(supported_features):
            continue

        detection = row["_detection_data"]
        if int(detection.chain) != chain_id:
            continue
        if not _is_price_scan_candidate(row):
            continue

        address = str(detection.address).lower()
        first_seen_block = int(detection.first_seen_at_block)
        if first_seen_block > end_block:
            continue

        latest_scanned_block = settlement_db.get_latest_scanned_block_number(chain_id, address)
        latest_event_block = settlement_db.get_latest_block_number(chain_id, address)
        latest_known_block = _max_optional_int(latest_scanned_block, latest_event_block)

        start_block = first_seen_block
        if forced_start_block is None and latest_known_block is not None:
            start_block = max(start_block, latest_known_block + 1)
        if forced_start_block is not None:
            start_block = max(start_block, forced_start_block)

        range_end_block = end_block
        if forced_end_block is not None:
            range_end_block = min(range_end_block, forced_end_block)

        if start_block <= range_end_block:
            ranges.append(
                VaultSettlementScanRange(
                    chain_id=chain_id,
                    address=address,
                    start_block=start_block,
                    end_block=range_end_block,
                )
            )

    return sorted(ranges, key=lambda item: str(item.address))


def fetch_and_store_vault_settlements(
    vault_db_path: Path = DEFAULT_VAULT_DATABASE,
    raw_price_path: Path = DEFAULT_UNCLEANED_PRICE_DATABASE,
    settlement_db_path: Path | None = None,
    rpc_urls_by_chain: dict[int, str] | None = None,
    supported_features: set[ERC4626Feature] | frozenset[ERC4626Feature] = SUPPORTED_SETTLEMENT_FEATURES,
    forced_start_block: int | None = None,
    forced_end_block: int | None = None,
    use_hypersync: bool | None = None,
    chunk_size: int = 50_000,
    chain_ids: set[int] | frozenset[int] | None = None,
    fail_gracefully: bool = True,
) -> VaultSettlementScanResult:
    """Fetch and store settlement/open-state events for supported protocols.

    This standalone/backfill helper reads raw price parquet to bound scan
    ranges. The production scanner loop uses
    :py:func:`fetch_and_store_vault_settlements_for_chain` to avoid re-reading
    raw price parquet during each chain cycle. Currently supported protocol
    readers are Lagoon and D2 Finance.

    :param vault_db_path:
        Vault metadata pickle path.
    :param raw_price_path:
        Raw price parquet path.
    :param settlement_db_path:
        Settlement DuckDB path. ``None`` uses the default pipeline path.
    :param rpc_urls_by_chain:
        Mapping ``chain_id -> JSON-RPC configuration string``.
    :param supported_features:
        Protocol feature flags whose event readers are available.
    :param forced_start_block:
        Optional inclusive backfill start block.
    :param forced_end_block:
        Optional inclusive backfill end block.
    :param use_hypersync:
        Passed to protocol readers. ``None`` lets each reader auto-detect.
    :param chunk_size:
        JSON-RPC ``eth_getLogs`` chunk size for fallback reads.
    :param chain_ids:
        Optional chain id filter for manual or backfill scans.
    :param fail_gracefully:
        If ``True``, one failed chain settlement batch is logged and counted
        without aborting the caller.
    :return:
        Scan summary.
    """
    settlement_db_path = settlement_db_path or get_default_vault_settlement_database_path()
    rpc_urls_by_chain = rpc_urls_by_chain or {}

    assert vault_db_path.exists(), f"Vault metadata database does not exist: {vault_db_path}"
    assert raw_price_path.exists(), f"Raw price parquet does not exist: {raw_price_path}"

    vault_db = VaultDatabase.read(vault_db_path)
    raw_prices_df = _read_raw_price_projection(raw_price_path, chain_ids=chain_ids)

    db = VaultSettlementDatabase(settlement_db_path)
    try:
        ranges = select_vault_settlement_scan_ranges(
            vault_db=vault_db,
            raw_prices_df=raw_prices_df,
            settlement_db=db,
            supported_features=supported_features,
            forced_start_block=forced_start_block,
            forced_end_block=forced_end_block,
        )
        if chain_ids is not None:
            ranges = [item for item in ranges if item.chain_id in chain_ids]

        candidate_count = _count_supported_vaults_with_raw_prices(
            vault_db,
            raw_prices_df,
            supported_features=supported_features,
            chain_ids=chain_ids,
        )
        skipped_count = max(0, candidate_count - len(ranges))

        if not ranges:
            db.save()
            return VaultSettlementScanResult(
                candidate_vaults=candidate_count,
                scanned_vaults=0,
                skipped_vaults=skipped_count,
                rows_written=0,
            )

        update_result = _fetch_and_store_settlement_ranges(
            database=db,
            vault_db=vault_db,
            ranges=ranges,
            rpc_urls_by_chain=rpc_urls_by_chain,
            use_hypersync=use_hypersync,
            chunk_size=chunk_size,
            fail_gracefully=fail_gracefully,
        )

        db.save()
        return VaultSettlementScanResult(
            candidate_vaults=candidate_count,
            scanned_vaults=update_result.scanned_vaults,
            skipped_vaults=skipped_count,
            rows_written=update_result.rows_written,
            scanned_chains=update_result.scanned_chains,
            failed_chains=update_result.failed_chains,
        )
    finally:
        db.close()


def fetch_and_store_vault_settlements_for_chain(
    *,
    vault_db: VaultDatabase,
    chain_id: int,
    rpc_url: str,
    end_block: int,
    settlement_db_path: Path | None = None,
    supported_features: set[ERC4626Feature] | frozenset[ERC4626Feature] = SUPPORTED_SETTLEMENT_FEATURES,
    forced_start_block: int | None = None,
    forced_end_block: int | None = None,
    use_hypersync: bool | None = None,
    chunk_size: int = 50_000,
    fail_gracefully: bool = True,
) -> VaultSettlementScanResult:
    """Fetch and store settlement events for one already-scanned chain.

    This helper is used by the production scanner loop. It avoids re-reading the
    vault pickle and raw price parquet by consuming the already-loaded vault
    metadata and the end block reported by the just-completed chain scan.

    :param vault_db:
        Vault metadata database.
    :param chain_id:
        Chain id to scan.
    :param rpc_url:
        JSON-RPC configuration string for the chain.
    :param end_block:
        Latest block reached by the successful chain scan.
    :param settlement_db_path:
        Settlement DuckDB path. ``None`` uses the default pipeline path.
    :param supported_features:
        Protocol feature flags whose event readers are available.
    :param forced_start_block:
        Optional inclusive backfill start block.
    :param forced_end_block:
        Optional inclusive backfill end block.
    :param use_hypersync:
        Passed to protocol readers. ``None`` lets each reader auto-detect.
    :param chunk_size:
        JSON-RPC ``eth_getLogs`` chunk size for fallback reads.
    :param fail_gracefully:
        If ``True``, a failed chain settlement batch is logged and counted
        without aborting the caller.
    :return:
        Scan summary.
    """
    settlement_db_path = settlement_db_path or get_default_vault_settlement_database_path()
    db = VaultSettlementDatabase(settlement_db_path)
    try:
        ranges = select_vault_settlement_scan_ranges_for_chain(
            vault_db=vault_db,
            settlement_db=db,
            chain_id=chain_id,
            end_block=end_block,
            supported_features=supported_features,
            forced_start_block=forced_start_block,
            forced_end_block=forced_end_block,
        )
        candidate_count = _count_supported_vaults_for_chain(
            vault_db=vault_db,
            chain_id=chain_id,
            end_block=end_block,
            supported_features=supported_features,
        )
        skipped_count = max(0, candidate_count - len(ranges))

        if not ranges:
            db.save()
            return VaultSettlementScanResult(
                candidate_vaults=candidate_count,
                scanned_vaults=0,
                skipped_vaults=skipped_count,
                rows_written=0,
            )

        update_result = _fetch_and_store_settlement_ranges(
            database=db,
            vault_db=vault_db,
            ranges=ranges,
            rpc_urls_by_chain={chain_id: rpc_url},
            use_hypersync=use_hypersync,
            chunk_size=chunk_size,
            fail_gracefully=fail_gracefully,
        )
        db.save()
        return VaultSettlementScanResult(
            candidate_vaults=candidate_count,
            scanned_vaults=update_result.scanned_vaults,
            skipped_vaults=skipped_count,
            rows_written=update_result.rows_written,
            scanned_chains=update_result.scanned_chains,
            failed_chains=update_result.failed_chains,
        )
    finally:
        db.close()


def _get_vault_features(row: VaultRow) -> set[ERC4626Feature]:
    """Read feature flags from a vault metadata row."""
    features = row.get("features") or row["_detection_data"].features
    return set(features)


def _max_optional_int(*values: int | None) -> int | None:
    """Return the largest non-null integer value.

    :param values:
        Values that may include ``None``.
    :return:
        Largest integer value, or ``None`` if all values are ``None``.
    """
    known_values = [value for value in values if value is not None]
    return max(known_values) if known_values else None


def _read_raw_price_projection(raw_price_path: Path, chain_ids: set[int] | frozenset[int] | None) -> pd.DataFrame:
    """Read the raw price columns needed for settlement scan selection.

    Per-chain scanner calls pass a single ``chain_ids`` value. Pandas forwards
    parquet filters to the engine, allowing predicate pushdown where the
    underlying parquet metadata supports it.

    :param raw_price_path:
        Raw price parquet path.
    :param chain_ids:
        Optional chain id filter.
    :return:
        Raw price DataFrame with ``chain``, ``address`` and ``block_number``.
    """
    read_kwargs: dict[str, object] = {
        "columns": ["chain", "address", "block_number"],
    }
    if chain_ids is not None:
        read_kwargs["filters"] = [("chain", "in", sorted(chain_ids))]
    return pd.read_parquet(raw_price_path, **read_kwargs)


def _fetch_and_store_settlement_ranges(
    *,
    database: VaultSettlementDatabase,
    vault_db: VaultDatabase,
    ranges: list[VaultSettlementScanRange],
    rpc_urls_by_chain: dict[int, str],
    use_hypersync: bool | None,
    chunk_size: int,
    fail_gracefully: bool,
) -> SettlementRangeUpdateResult:
    """Execute selected settlement ranges grouped by chain.

    Both public settlement scan entry points select ranges differently, but
    once ranges exist they should share Web3 construction, token-cache reuse
    and graceful-failure accounting.

    :param database:
        Generic settlement database.
    :param vault_db:
        Vault metadata database.
    :param ranges:
        Selected per-vault scan ranges.
    :param rpc_urls_by_chain:
        JSON-RPC configuration strings keyed by chain id.
    :param use_hypersync:
        Whether to use Hypersync. ``None`` auto-detects.
    :param chunk_size:
        JSON-RPC fallback chunk size.
    :param fail_gracefully:
        If ``True``, failed chain batches are logged and counted without
        aborting the caller.
    :return:
        Aggregate update summary.
    """
    rows_by_key = {(int(row["_detection_data"].chain), str(row["_detection_data"].address).lower()): row for row in vault_db.rows.values()}
    web3_by_chain: dict[int, Web3] = {}
    token_cache = TokenDiskCache()
    rows_written = 0
    scanned_vaults = 0
    scanned_chains = 0
    failed_chains = 0

    ranges_by_chain: dict[int, list[VaultSettlementScanRange]] = defaultdict(list)
    for scan_range in ranges:
        ranges_by_chain[scan_range.chain_id].append(scan_range)

    for chain_id, chain_ranges in sorted(ranges_by_chain.items()):
        try:
            rpc_url = rpc_urls_by_chain.get(chain_id)
            if not rpc_url:
                raise RuntimeError(f"No JSON-RPC URL configured for chain {chain_id}")

            web3 = web3_by_chain.get(chain_id)
            if web3 is None:
                web3 = create_multi_provider_web3(rpc_url)
                web3_by_chain[chain_id] = web3

            chain_update = _update_settlement_database_for_chain(
                database=database,
                web3=web3,
                chain_id=chain_id,
                ranges=chain_ranges,
                rows_by_key=rows_by_key,
                token_cache=token_cache,
                use_hypersync=use_hypersync,
                chunk_size=chunk_size,
            )
            rows_written += chain_update.rows_written
            scanned_vaults += chain_update.scanned_vaults
            scanned_chains += 1
        except Exception:
            failed_chains += 1
            logger.exception("Vault settlement scan failed for chain %d", chain_id)
            if not fail_gracefully:
                raise

    return SettlementRangeUpdateResult(
        rows_written=rows_written,
        scanned_vaults=scanned_vaults,
        scanned_chains=scanned_chains,
        failed_chains=failed_chains,
    )


def _count_supported_vaults_with_raw_prices(
    vault_db: VaultDatabase,
    raw_prices_df: pd.DataFrame,
    supported_features: set[ERC4626Feature] | frozenset[ERC4626Feature] = SUPPORTED_SETTLEMENT_FEATURES,
    chain_ids: set[int] | frozenset[int] | None = None,
) -> int:
    """Count supported protocol metadata rows that also have raw price rows."""
    raw_keys = {(int(row["chain"]), str(row["address"]).lower()) for row in raw_prices_df[["chain", "address"]].drop_duplicates().to_dict("records")}
    if chain_ids is not None:
        raw_keys = {key for key in raw_keys if key[0] in chain_ids}
    return sum(1 for row in vault_db.rows.values() if _get_vault_features(row).intersection(supported_features) and (int(row["_detection_data"].chain), str(row["_detection_data"].address).lower()) in raw_keys)


def _count_supported_vaults_for_chain(
    *,
    vault_db: VaultDatabase,
    chain_id: int,
    end_block: int,
    supported_features: set[ERC4626Feature] | frozenset[ERC4626Feature] = SUPPORTED_SETTLEMENT_FEATURES,
) -> int:
    """Count supported protocol metadata rows for one scanned chain."""
    return sum(1 for row in vault_db.rows.values() if _get_vault_features(row).intersection(supported_features) and int(row["_detection_data"].chain) == chain_id and int(row["_detection_data"].first_seen_at_block) <= end_block and _is_price_scan_candidate(row))


def _is_price_scan_candidate(row: VaultRow) -> bool:
    """Return ``True`` if the production price scanner would consider a vault.

    The production settlement selector does not read the raw price parquet, so
    it mirrors the price scanner's low-activity filter to avoid settlement log
    reads for vaults that cannot receive price rows in the same chain cycle.
    """
    detection = row["_detection_data"]
    address = str(detection.address).lower()
    return int(detection.deposit_count) >= MIN_PRICE_SCAN_DEPOSIT_COUNT or address in HARDCODED_PROTOCOLS or is_activity_filter_exempt(detection)


def _prepare_settlement_vault(
    *,
    web3: Web3,
    row: VaultRow,
    token_cache: TokenDiskCache,
) -> PreparedSettlementVault:
    """Create a protocol-specific vault adapter for settlement scanning.

    :param web3:
        Web3 connection for the vault chain.
    :param row:
        Vault metadata row.
    :param token_cache:
        Shared token metadata cache.
    :return:
        Prepared vault adapter and event topic mapping.
    """
    detection = row["_detection_data"]
    vault = create_vault_instance(
        web3,
        detection.address,
        _get_vault_features(row),
        token_cache=token_cache,
    )
    if vault is None:
        raise RuntimeError(f"Could not create vault instance for settlement scan: {detection.address}")

    if isinstance(vault, LagoonVault):
        event_by_topic = get_settlement_events_by_topic(vault)
    elif isinstance(vault, D2Vault):
        event_by_topic = get_d2_settlement_events_by_topic(vault)
    else:
        raise RuntimeError(f"Unsupported settlement scanner vault type: {type(vault)}")

    return PreparedSettlementVault(
        vault=vault,
        event_by_topic=event_by_topic,
    )


def _build_settlement_rows_for_prepared_vault(
    prepared_vault: PreparedSettlementVault,
    logs: list[AttributeDict],
) -> list[VaultSettlement]:
    """Build settlement rows for one prepared protocol vault.

    :param prepared_vault:
        Vault adapter and event topic mapping.
    :param logs:
        Logs that already match this vault and its incremental block range.
    :return:
        Settlement rows ready to upsert.
    """
    vault = prepared_vault.vault
    if isinstance(vault, LagoonVault):
        return build_lagoon_settlement_rows_from_logs(vault, logs, event_by_topic=prepared_vault.event_by_topic)
    if isinstance(vault, D2Vault):
        return build_d2_settlement_rows_from_logs(vault, logs, event_by_topic=prepared_vault.event_by_topic)
    raise RuntimeError(f"Unsupported settlement scanner vault type: {type(vault)}")


def _update_settlement_database_for_chain(
    *,
    database: VaultSettlementDatabase,
    web3: Web3,
    chain_id: int,
    ranges: list[VaultSettlementScanRange],
    rows_by_key: dict[tuple[int, str], VaultRow],
    token_cache: TokenDiskCache,
    use_hypersync: bool | None,
    chunk_size: int,
) -> ChainSettlementUpdateResult:
    """Fetch and store settlement events for all supported vaults on one chain.

    :param database:
        Generic settlement database.
    :param web3:
        Web3 connection for the scanned chain.
    :param chain_id:
        EVM chain id.
    :param ranges:
        Per-vault incremental ranges for the chain.
    :param rows_by_key:
        Vault metadata rows keyed by ``(chain_id, lowercase_address)``.
    :param token_cache:
        Shared token metadata cache for vault adapter creation.
    :param use_hypersync:
        Whether to use Hypersync. ``None`` auto-detects.
    :param chunk_size:
        JSON-RPC fallback chunk size.
    :return:
        Settlement row and scan-state update counts.
    """
    assert ranges, "Chain settlement scan needs at least one vault range"

    prepared_by_address: dict[str, PreparedSettlementVault] = {}
    range_by_address = {str(scan_range.address).lower(): scan_range for scan_range in ranges}
    topic_set: set[str] = set()

    failed_vaults = 0
    for scan_range in ranges:
        address = str(scan_range.address).lower()
        try:
            row = rows_by_key[scan_range.chain_id, address]
            prepared_vault = _prepare_settlement_vault(
                web3=web3,
                row=row,
                token_cache=token_cache,
            )
        except (RuntimeError, ValueError, KeyError, TypeError, Web3Exception):
            failed_vaults += 1
            logger.exception(
                "Skipping settlement scan for vault %s on chain %d because vault adapter preparation failed",
                address,
                chain_id,
            )
            continue

        prepared_by_address[address] = prepared_vault
        topic_set.update(prepared_vault.event_by_topic.keys())

    if not prepared_by_address:
        logger.warning("No vaults could be prepared for settlement scan on chain %d", chain_id)
        return ChainSettlementUpdateResult(rows_written=0, scanned_vaults=0)

    prepared_ranges = [range_by_address[address] for address in prepared_by_address]
    start_block = min(scan_range.start_block for scan_range in prepared_ranges)
    end_block = max(scan_range.end_block for scan_range in prepared_ranges)
    logger.info(
        "Scanning settlement/open-state events for %d vaults on chain %d, blocks %d - %d",
        len(prepared_by_address),
        chain_id,
        start_block,
        end_block,
    )

    logs = fetch_vault_settlement_logs_for_addresses(
        web3=web3,
        addresses=list(prepared_by_address.keys()),
        topic0_list=sorted(topic_set),
        start_block=start_block,
        end_block=end_block,
        use_hypersync=use_hypersync,
        chunk_size=chunk_size,
    )

    logs_by_address: dict[str, list[AttributeDict]] = defaultdict(list)
    for log in logs:
        address = str(log["address"]).lower()
        scan_range = range_by_address.get(address)
        if scan_range is None:
            continue

        block_number = int(log["blockNumber"])
        if not scan_range.start_block <= block_number <= scan_range.end_block:
            continue

        topics = log.get("topics") or []
        if not topics:
            continue
        topic0 = normalise_log_topic(topics[0])
        if topic0 not in prepared_by_address[address].event_by_topic:
            continue

        logs_by_address[address].append(log)

    settlements = []
    failed_addresses: set[str] = set()
    for address, vault_logs in sorted(logs_by_address.items()):
        try:
            settlements.extend(_build_settlement_rows_for_prepared_vault(prepared_by_address[address], vault_logs))
        except (RuntimeError, ValueError, KeyError, TypeError, Web3Exception):
            failed_vaults += 1
            failed_addresses.add(address)
            logger.exception(
                "Skipping settlement rows for vault %s on chain %d because row building failed",
                address,
                chain_id,
            )

    inserted = database.upsert_settlements(settlements)
    scan_states = [(chain_id, address, range_by_address[address].end_block) for address in prepared_by_address if address not in failed_addresses]
    scanned_vaults = database.upsert_scan_state(scan_states)
    logger.info(
        "Stored %d settlement/open-state events for %d vaults on chain %d, advanced %d scan states, skipped %d failed vaults",
        inserted,
        len(prepared_by_address),
        chain_id,
        scanned_vaults,
        failed_vaults,
    )
    return ChainSettlementUpdateResult(rows_written=inserted, scanned_vaults=scanned_vaults)
