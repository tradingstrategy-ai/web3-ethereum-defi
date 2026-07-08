"""Populate generic vault settlement data from protocol-specific readers.

This module owns the production scan orchestration for sparse settlement
events. Protocol modules know how to read their event logs; this module
selects vaults and block ranges from the existing vault metadata and raw price
parquet.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from eth_typing import HexAddress

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.d2.settlement import update_d2_settlement_database
from eth_defi.erc_4626.vault_protocol.d2.vault import D2Vault
from eth_defi.erc_4626.vault_protocol.lagoon.settlement import update_lagoon_settlement_database
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.vault.settlement_data import (
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
        Vault ranges that were scanned.
    :param skipped_vaults:
        Candidate vaults skipped because the existing database was already
        current for the raw price block range.
    :param rows_written:
        Settlement rows written to DuckDB.
    """

    candidate_vaults: int
    scanned_vaults: int
    skipped_vaults: int
    rows_written: int


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
    in ``vault-settlements.duckdb`` make normal scans incremental.

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
        latest_stored_block = settlement_db.get_latest_block_number(chain_id, address)

        start_block = raw_min_block
        if forced_start_block is None and latest_stored_block is not None:
            start_block = max(start_block, latest_stored_block + 1)
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
) -> VaultSettlementScanResult:
    """Fetch and store settlement/open-state events for supported protocols.

    Currently supported protocol readers are Lagoon and D2 Finance.

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
    :return:
        Scan summary.
    """
    settlement_db_path = settlement_db_path or get_default_vault_settlement_database_path()
    rpc_urls_by_chain = rpc_urls_by_chain or {}

    assert vault_db_path.exists(), f"Vault metadata database does not exist: {vault_db_path}"
    assert raw_price_path.exists(), f"Raw price parquet does not exist: {raw_price_path}"

    vault_db = VaultDatabase.read(vault_db_path)
    raw_prices_df = pd.read_parquet(raw_price_path, columns=["chain", "address", "block_number"])

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
        candidate_count = _count_supported_vaults_with_raw_prices(
            vault_db,
            raw_prices_df,
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

        rows_by_key = {(int(row["_detection_data"].chain), str(row["_detection_data"].address).lower()): row for row in vault_db.rows.values()}
        web3_by_chain = {}
        token_cache = TokenDiskCache()
        rows_written = 0

        for scan_range in ranges:
            rpc_url = rpc_urls_by_chain.get(scan_range.chain_id)
            if not rpc_url:
                raise RuntimeError(f"No JSON-RPC URL configured for chain {scan_range.chain_id} needed by vault {scan_range.address}")

            web3 = web3_by_chain.get(scan_range.chain_id)
            if web3 is None:
                web3 = create_multi_provider_web3(rpc_url)
                web3_by_chain[scan_range.chain_id] = web3

            row = rows_by_key[(scan_range.chain_id, str(scan_range.address).lower())]
            detection = row["_detection_data"]
            vault = create_vault_instance(
                web3,
                detection.address,
                _get_vault_features(row),
                token_cache=token_cache,
            )
            if vault is None:
                raise RuntimeError(f"Could not create vault instance for settlement scan: {detection.address}")
            rows_written += _update_settlement_database_for_vault(
                database=db,
                vault=vault,
                scan_range=scan_range,
                use_hypersync=use_hypersync,
                chunk_size=chunk_size,
            )

        db.save()
        return VaultSettlementScanResult(
            candidate_vaults=candidate_count,
            scanned_vaults=len(ranges),
            skipped_vaults=skipped_count,
            rows_written=rows_written,
        )
    finally:
        db.close()


def _get_vault_features(row: VaultRow) -> set[ERC4626Feature]:
    """Read feature flags from a vault metadata row."""
    features = row.get("features") or row["_detection_data"].features
    return set(features)


def _count_supported_vaults_with_raw_prices(
    vault_db: VaultDatabase,
    raw_prices_df: pd.DataFrame,
    supported_features: set[ERC4626Feature] | frozenset[ERC4626Feature] = SUPPORTED_SETTLEMENT_FEATURES,
) -> int:
    """Count supported protocol metadata rows that also have raw price rows."""
    raw_keys = {(int(row["chain"]), str(row["address"]).lower()) for row in raw_prices_df[["chain", "address"]].drop_duplicates().to_dict("records")}
    return sum(1 for row in vault_db.rows.values() if _get_vault_features(row).intersection(supported_features) and (int(row["_detection_data"].chain), str(row["_detection_data"].address).lower()) in raw_keys)


def _update_settlement_database_for_vault(
    *,
    database: VaultSettlementDatabase,
    vault,
    scan_range: VaultSettlementScanRange,
    use_hypersync: bool | None,
    chunk_size: int,
) -> int:
    """Route a vault instance to its protocol-specific event reader."""
    logger.info(
        "Scanning %s settlement/open-state events for %s on chain %d, blocks %d - %d",
        type(vault).__name__,
        scan_range.address,
        scan_range.chain_id,
        scan_range.start_block,
        scan_range.end_block,
    )

    if isinstance(vault, LagoonVault):
        return update_lagoon_settlement_database(
            database=database,
            vault=vault,
            start_block=scan_range.start_block,
            end_block=scan_range.end_block,
            use_hypersync=use_hypersync,
            chunk_size=chunk_size,
        )

    if isinstance(vault, D2Vault):
        return update_d2_settlement_database(
            database=database,
            vault=vault,
            start_block=scan_range.start_block,
            end_block=scan_range.end_block,
            use_hypersync=use_hypersync,
            chunk_size=chunk_size,
        )

    raise RuntimeError(f"Unsupported settlement scanner vault type: {type(vault)}")
