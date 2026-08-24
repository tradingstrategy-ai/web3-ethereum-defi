"""Synchronise Rysk Premium's public option-pool catalogue into metadata."""

import datetime
from dataclasses import dataclass, replace

from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.rysk.api import fetch_rysk_premium_pools, fetch_rysk_premium_snapshots
from eth_defi.erc_4626.vault_protocol.rysk.constants import RyskPremiumPool, install_rysk_premium_runtime_pools, is_rysk_premium_test_pool
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow


@dataclass(slots=True, frozen=True)
class RyskPremiumCatalogueSyncResult:
    """Summarise one current Rysk Premium catalogue reconciliation.

    The immutable counts distinguish newly introduced products from ordinary
    refreshes of rows already present in the common metadata database.
    """

    #: Current user-facing pools on the requested chain.
    pools: int
    #: New metadata rows.
    inserted: int
    #: Existing metadata rows refreshed from the catalogue.
    updated: int


def _fetch_pool_with_first_snapshot(pool: RyskPremiumPool, fallback_block: int, fallback_time: datetime.datetime) -> RyskPremiumPool:
    """Attach the earliest available public snapshot as history evidence.

    The complete paginated stream is minimised by block and timestamp instead
    of relying on undocumented application response ordering.

    :param pool:
        Catalogue pool identity.
    :param fallback_block:
        Current block used when the new pool has no snapshots yet.
    :param fallback_time:
        Current block time used when the new pool has no snapshots yet.
    :return:
        Pool with a first-known history boundary.
    """

    first_snapshot = min(
        fetch_rysk_premium_snapshots(pool),
        default=None,
        key=lambda snapshot: (snapshot.block_number, snapshot.timestamp),
    )
    if first_snapshot is None:
        return replace(pool, first_seen_at_block=fallback_block, first_seen_at=fallback_time)
    first_seen_at = datetime.datetime.fromtimestamp(first_snapshot.timestamp, tz=datetime.UTC).replace(tzinfo=None)
    return replace(pool, first_seen_at_block=first_snapshot.block_number, first_seen_at=first_seen_at)


def fetch_and_sync_rysk_premium_catalogue(*, web3: Web3, vault_db: VaultDatabase, token_cache: TokenDiskCache, block_number: int | None = None) -> RyskPremiumCatalogueSyncResult:
    """Upsert Rysk Premium pools for one chain without touching other vaults.

    The current `public pools endpoint
    <https://premium.rysk.finance/api/pools>`__ supplies contract identities.
    Issuer-labelled internal products are excluded from production metadata.

    :param web3:
        Current Rysk chain connection.
    :param vault_db:
        Shared metadata database to update.
    :param token_cache:
        Token metadata cache used by common scan-row construction.
    :param block_number:
        Optional current metadata block.
    :return:
        Catalogue insertion and refresh counts.
    """

    chain_id = web3.eth.chain_id
    block_number = block_number if block_number is not None else web3.eth.block_number
    block = web3.eth.get_block(block_number)
    observed_at = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    catalogue_pools = tuple(pool for pool in fetch_rysk_premium_pools() if pool.chain_id == chain_id and not is_rysk_premium_test_pool(pool))
    pools = tuple(_fetch_pool_with_first_snapshot(pool, block_number, observed_at) if VaultSpec(pool.chain_id, pool.address) not in vault_db.rows else pool for pool in catalogue_pools)
    install_rysk_premium_runtime_pools(list(pools))
    rows: dict[VaultSpec, VaultRow] = {}
    inserted = 0
    for pool in pools:
        spec = VaultSpec(pool.chain_id, pool.address)
        existing = vault_db.rows.get(spec)
        first_seen_at_block = existing["_detection_data"].first_seen_at_block if existing else pool.first_seen_at_block
        first_seen_at = existing["_detection_data"].first_seen_at if existing else pool.first_seen_at
        assert first_seen_at_block is not None
        assert first_seen_at is not None
        detection = ERC4262VaultDetection(
            chain=pool.chain_id,
            address=pool.address,
            first_seen_at_block=first_seen_at_block,
            first_seen_at=first_seen_at,
            features={ERC4626Feature.rysk_premium_like, ERC4626Feature.share_price_equivalence},
            updated_at=native_datetime_utc_now(),
            deposit_count=0,
            redeem_count=0,
        )
        row = create_vault_scan_record(web3, detection, block_number, token_cache)
        row["_rysk_pool_name"] = pool.name
        row["_rysk_pool_description"] = pool.description
        row["_rysk_option_type"] = pool.option_type
        row["_rysk_registry"] = pool.registry
        row["_rysk_option_handler"] = pool.option_handler
        row["_rysk_asset"] = pool.asset
        row["_rysk_option_sale_fee_bps"] = pool.option_sale_fee_bps
        row["_rysk_authority"] = pool.authority
        if existing:
            merged = existing.copy()
            merged.update(row)
            row = merged
        else:
            inserted += 1
        rows[spec] = row
    vault_db._merge_rows(rows)
    return RyskPremiumCatalogueSyncResult(pools=len(pools), inserted=inserted, updated=len(pools) - inserted)
