"""Address-scoped Frankencoin Shares metadata and historical backfill.

Only Ethereum FCS is reconciled. Existing discovery counts and timestamps,
unrelated vaults, chain discovery progress and scheduled reader state survive.
New leads use targeted Hypersync events; FPS wrapping is counted as
configuration activity rather than fresh ZCHF deposits.
"""

import dataclasses
import logging
from collections import Counter
from pathlib import Path

import duckdb
import hypersync
import pyarrow.compute as pc
import pyarrow.parquet as pq
from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.frankencoin.constants import (
    FRANKENCOIN_EQUITY_ADDRESS,
    FRANKENCOIN_SHARES_ADDRESS,
    FRANKENCOIN_SHARES_CHAIN_ID,
    FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK,
    FRANKENCOIN_SHARES_DEPLOYMENT_TIME,
    FRANKENCOIN_ZCHF_ADDRESS,
)
from eth_defi.erc_4626.vault_protocol.frankencoin.shares import FrankencoinSharesVault
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flow_events import fetch_vault_flow_logs_hypersync, normalise_event_topic
from eth_defi.vault.historical import ParquetScanResult, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Fixed address scope for every metadata and price write.
FRANKENCOIN_SHARES_SPEC = VaultSpec(FRANKENCOIN_SHARES_CHAIN_ID, FRANKENCOIN_SHARES_ADDRESS)


def fetch_frankencoin_shares_detection(
    vault: FrankencoinSharesVault,
    vault_db: VaultDatabase,
    hypersync_client: hypersync.HypersyncClient,
    metadata_block: int,
) -> ERC4262VaultDetection:
    """Reclassify existing discovery or fetch a new target-only lead.

    Preserve observed discovery history when available. A previously absent
    deployment is seeded at its reviewed creation block. Event counts come
    from configured Hypersync, using the verified FCS event interface; see
    https://docs.frankencoin.com/pool-shares/fcs for wrapping mechanics.

    :param vault: Validated Ethereum FCS adapter.
    :param vault_db: Existing materialised metadata, never mutated here.
    :param hypersync_client: Configured Ethereum Hypersync client for new leads.
    :param metadata_block: Inclusive block at which to stop event counting.
    :return: FCS detection with its dedicated classification.
    """
    features = {ERC4626Feature.frankencoin_fcs_like}
    existing_row = vault_db.rows.get(FRANKENCOIN_SHARES_SPEC, {})
    existing = existing_row.get("_detection_data")
    if existing is not None:
        if not isinstance(existing, ERC4262VaultDetection) or existing.get_spec() != FRANKENCOIN_SHARES_SPEC:
            msg = "FCS metadata contains a mismatched detection record"
            raise ValueError(msg)
        return dataclasses.replace(existing, features=features, updated_at=native_datetime_utc_now())

    lead = vault_db.leads.get(FRANKENCOIN_SHARES_SPEC)
    if lead is None:
        contract = vault.vault_contract
        deposit_topic = normalise_event_topic(get_topic_signature_from_event(contract.events.Deposit))
        withdraw_topic = normalise_event_topic(get_topic_signature_from_event(contract.events.Withdraw))
        wrapping_topics = {
            normalise_event_topic(get_topic_signature_from_event(contract.events.Wrapped)),
            normalise_event_topic(get_topic_signature_from_event(contract.events.Unwrapped)),
        }
        logger.info("Reading FCS deposit, withdrawal and migration events from deployment to %s", metadata_block)
        logs = fetch_vault_flow_logs_hypersync(
            hypersync_client=hypersync_client,
            vault_address=FRANKENCOIN_SHARES_ADDRESS,
            topic0_list=[deposit_topic, withdraw_topic, *sorted(wrapping_topics)],
            start_block=FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK,
            end_block=metadata_block,
        )
        topics = Counter(normalise_event_topic(log.topics[0]) for log in logs)
        lead = PotentialVaultMatch(
            chain=FRANKENCOIN_SHARES_CHAIN_ID,
            address=FRANKENCOIN_SHARES_ADDRESS,
            first_seen_at_block=FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK,
            first_seen_at=FRANKENCOIN_SHARES_DEPLOYMENT_TIME,
            deposit_count=topics[deposit_topic],
            withdrawal_count=topics[withdraw_topic],
            configuration_count=sum(topics[topic] for topic in wrapping_topics),
        )
    if lead.chain != FRANKENCOIN_SHARES_CHAIN_ID or lead.address.lower() != FRANKENCOIN_SHARES_ADDRESS:
        msg = "FCS metadata contains a mismatched lead"
        raise ValueError(msg)
    return ERC4262VaultDetection(
        chain=lead.chain,
        address=FRANKENCOIN_SHARES_ADDRESS,
        first_seen_at_block=lead.first_seen_at_block,
        first_seen_at=lead.first_seen_at,
        features=features,
        updated_at=native_datetime_utc_now(),
        deposit_count=lead.deposit_count,
        redeem_count=lead.withdrawal_count,
        configuration_count=lead.configuration_count,
    )


def fetch_frankencoin_shares_metadata(
    web3: Web3,
    vault_db: VaultDatabase,
    token_cache: TokenDiskCache,
    hypersync_client: hypersync.HypersyncClient,
    metadata_block: int,
) -> tuple[FrankencoinSharesVault, ERC4262VaultDetection, VaultRow]:
    """Validate FCS and prepare a replacement row without changing the cache.

    Validate the chain, underlying FPS and ZCHF pointers against the reviewed
    deployment catalogue before any metadata or historical file write:
    https://github.com/Frankencoin-ZCHF/Frankencoin/blob/8b4c4ab67bb361b91d58c474b87f4608fc4c0566/exports/address.config.ts.

    :param web3: Ethereum RPC connection.
    :param vault_db: Existing metadata, retained during preparation.
    :param token_cache: Temporary or configured ERC-20 metadata cache.
    :param hypersync_client: Configured Hypersync client for missing leads.
    :param metadata_block: Consistent current-state metadata block.
    :return: Validated adapter, detection and merged metadata row.
    :raises ValueError: If chain, pointers or the rebuilt metadata are invalid.
    """
    if web3.eth.chain_id != FRANKENCOIN_SHARES_CHAIN_ID:
        msg = "Frankencoin Shares backfill requires Ethereum mainnet"
        raise ValueError(msg)
    if metadata_block < FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK:
        msg = "Metadata block precedes FCS deployment"
        raise ValueError(msg)
    vault = FrankencoinSharesVault(web3, FRANKENCOIN_SHARES_SPEC, token_cache=token_cache, features={ERC4626Feature.frankencoin_fcs_like}, default_block_identifier=metadata_block)
    contract = vault.vault_contract
    if contract.functions.asset().call(block_identifier=metadata_block).lower() != FRANKENCOIN_ZCHF_ADDRESS:
        msg = "FCS asset does not match reviewed ZCHF"
        raise ValueError(msg)
    if contract.functions.FPS1().call(block_identifier=metadata_block).lower() != FRANKENCOIN_EQUITY_ADDRESS:
        msg = "FCS underlying equity does not match reviewed FPS"
        raise ValueError(msg)
    detection = fetch_frankencoin_shares_detection(vault, vault_db, hypersync_client, metadata_block)
    rebuilt = create_vault_scan_record(web3, detection, metadata_block, token_cache)
    if rebuilt.get("Name") != "Frankencoin Shares" or rebuilt.get("Protocol") != "Frankencoin" or rebuilt.get("NAV") is None:
        msg = "FCS metadata refresh failed; existing files were not updated"
        raise ValueError(msg)
    row = vault_db.rows.get(FRANKENCOIN_SHARES_SPEC, {}) | rebuilt
    vault.first_seen_at_block = FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK
    return vault, detection, row


def apply_frankencoin_shares_metadata(vault_db: VaultDatabase, detection: ERC4262VaultDetection, row: VaultRow) -> None:
    """Apply one prepared FCS row while preserving discovery progress.

    Update only the reviewed identity. Existing leads, including their
    observed event counters, are preserved; a missing lead is reconstructed
    from the prepared detection. Chain cursors are never advanced.

    :param vault_db: Metadata database to update in memory.
    :param detection: Validated FCS discovery record.
    :param row: Prepared scanner metadata row.
    :return: ``None``; the caller controls persistence.
    """
    if detection.get_spec() != FRANKENCOIN_SHARES_SPEC:
        msg = "Cannot apply another vault through the FCS migration"
        raise ValueError(msg)
    vault_db.rows[FRANKENCOIN_SHARES_SPEC] = row
    vault_db.leads.setdefault(
        FRANKENCOIN_SHARES_SPEC,
        PotentialVaultMatch(
            chain=detection.chain,
            address=FRANKENCOIN_SHARES_ADDRESS,
            first_seen_at_block=detection.first_seen_at_block,
            first_seen_at=detection.first_seen_at,
            deposit_count=detection.deposit_count,
            withdrawal_count=detection.redeem_count,
            configuration_count=detection.configuration_count,
        ),
    )


def fetch_frankencoin_shares_prices(
    vault: FrankencoinSharesVault,
    *,
    rpc_url: str,
    output_path: Path,
    token_cache: TokenDiskCache,
    hypersync_client: hypersync.HypersyncClient,
    timestamp_cache_path: Path,
    start_block: int,
    end_block: int,
    max_workers: int,
) -> ParquetScanResult:
    """Backfill FCS alone through the common hourly Parquet writer.

    The writer preserves other addresses and rows outside the half-open block
    range. This stateless invocation never reads or changes scanner progress.
    Use a staged output copy so failed or empty scans cannot replace good data.
    Accounting semantics: https://docs.frankencoin.com/pool-shares/fcs.

    :param vault: Reviewed Ethereum FCS adapter.
    :param rpc_url: Project-format space-separated Ethereum RPC URLs.
    :param output_path: Staged Parquet containing any retained existing rows.
    :param token_cache: ERC-20 metadata cache.
    :param hypersync_client: Configured Ethereum Hypersync client.
    :param timestamp_cache_path: Preserved dense per-chain timestamp directory.
    :param start_block: Inclusive boundary, at or after deployment.
    :param end_block: Exclusive boundary after ``start_block``.
    :param max_workers: Maximum archive-reading workers.
    :return: Common writer report with FCS price observations.
    :raises ValueError: If scope, range, workers or timestamp cache are invalid.
    :raises RuntimeError: If output is empty or core accounting contains failed reads.
    """
    if vault.spec != FRANKENCOIN_SHARES_SPEC:
        msg = "FCS price backfill received an unreviewed vault"
        raise ValueError(msg)
    if start_block < FRANKENCOIN_SHARES_DEPLOYMENT_BLOCK or end_block <= start_block:
        msg = "FCS backfill requires a non-empty range at or after deployment"
        raise ValueError(msg)
    if max_workers < 1:
        msg = "MAX_WORKERS must be positive"
        raise ValueError(msg)
    cache_file = timestamp_cache_path / "1-timestamps.duckdb"
    if not cache_file.is_file():
        raise ValueError(f"Restore or prepopulate the dense Ethereum timestamp cache before backfill: {cache_file}")
    # Check through the exclusive boundary because the shared timestamp
    # prefetcher includes that boundary when checking cache coverage.
    logger.info("Checking dense Ethereum timestamp coverage for blocks %s-%s", start_block, end_block)
    connection = duckdb.connect(str(cache_file), read_only=True)
    try:
        cached_blocks = connection.execute(
            "SELECT COUNT(DISTINCT block_number) FROM block_timestamps WHERE block_number >= ? AND block_number <= ? AND timestamp IS NOT NULL",
            [start_block, end_block],
        ).fetchone()[0]
    finally:
        connection.close()
    if cached_blocks != end_block - start_block + 1:
        raise ValueError(f"Prepopulate dense Ethereum timestamp coverage for blocks {start_block}-{end_block} before FCS backfill")
    result = scan_historical_prices_to_parquet(
        output_fname=output_path,
        web3=vault.web3,
        web3factory=MultiProviderWeb3Factory(rpc_url),
        vaults=[vault],
        token_cache=token_cache,
        start_block=start_block,
        end_block=end_block,
        max_workers=max_workers,
        frequency="1h",
        require_multicall_result=True,
        reader_states=None,
        hypersync_client=hypersync_client,
        timestamp_cache_file=timestamp_cache_path,
        vault_addresses={FRANKENCOIN_SHARES_ADDRESS},
    )
    if result["price_rows_written_by_vault"].get(FRANKENCOIN_SHARES_ADDRESS, 0) == 0:
        msg = "FCS backfill produced no valid prices; do not publish the staged output"
        raise RuntimeError(msg)
    # A non-empty scan can still include failed contract calls. Reject missing
    # accounting before replacing historical rows; finite zero values are valid
    # for blocks where FCS has no backing or issued shares yet.
    columns = ["share_price", "total_assets", "total_supply"]
    observations = pq.read_table(
        output_path,
        columns=columns,
        filters=[("chain", "=", FRANKENCOIN_SHARES_CHAIN_ID), ("address", "=", FRANKENCOIN_SHARES_ADDRESS), ("block_number", ">=", start_block), ("block_number", "<", end_block)],
    )
    if observations.num_rows == 0 or not all(pc.all(pc.fill_null(pc.is_finite(observations[column]), False)).as_py() for column in columns):
        msg = "FCS backfill contains failed accounting reads; do not publish the staged output"
        raise RuntimeError(msg)
    return result
