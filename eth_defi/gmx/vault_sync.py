"""Synchronise the onchain GMX V2 catalogue into the common vault database."""

import datetime
import logging
from dataclasses import dataclass

from joblib import Parallel, delayed
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.gmx.vault_catalog import GMXVaultProduct, fetch_gmx_v2_vault_products
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

GMX_DISABLED_DEPOSIT_REASON = "GMX product disabled"


@dataclass(slots=True, frozen=True)
class GMXVaultCatalogueSyncResult:
    """Summary of one GMX metadata reconciliation."""

    products: int
    inserted: int
    updated: int


def _feature_for_product(product: GMXVaultProduct) -> ERC4626Feature:
    """Map an onchain product kind to its persisted adapter feature."""

    return ERC4626Feature.gmx_gm if product.product_type == "gm" else ERC4626Feature.gmx_glv


def fetch_and_sync_gmx_vault_catalogue(
    *,
    web3: Web3,
    vault_db: VaultDatabase,
    token_cache: TokenDiskCache,
    block_number: int | None = None,
    max_workers: int = 8,
) -> GMXVaultCatalogueSyncResult:
    """Upsert GM and GLV products without disturbing unrelated vault rows.

    New products start at the catalogue scan block.  The explicit migration
    script can later scan older value events without changing the scheduled
    catalogue cursor; scheduled scans therefore remain incremental.

    :param web3:
        Arbitrum One or Avalanche connection.
    :param vault_db:
        Existing common metadata database to update in memory.
    :param token_cache:
        Shared ERC-20 metadata cache.
    :param block_number:
        Optional fixed catalogue block; defaults to the latest head.
    :param max_workers:
        Maximum metadata-read worker threads.
    :return:
        Product, insertion and update counts.
    """

    if max_workers <= 0:
        raise ValueError(f"GMX catalogue max_workers must be positive, got {max_workers}")
    block_number = block_number if block_number is not None else web3.eth.block_number
    block = web3.eth.get_block(block_number)
    observed_at = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.UTC).replace(tzinfo=None)
    updated_at = native_datetime_utc_now()
    products = tuple(fetch_gmx_v2_vault_products(web3, block_identifier=block_number, token_cache=token_cache))

    def fetch_product_row(product: GMXVaultProduct) -> tuple[VaultSpec, VaultRow, bool]:
        """Fetch one product's common metadata row."""

        spec = VaultSpec(product.chain_id, product.token_address)
        existing = vault_db.rows.get(spec)
        if existing is None:
            first_seen_at_block = block_number
            first_seen_at = observed_at
        else:
            old_detection = existing["_detection_data"]
            first_seen_at_block = old_detection.first_seen_at_block
            first_seen_at = old_detection.first_seen_at
        feature = _feature_for_product(product)
        detection = ERC4262VaultDetection(
            chain=product.chain_id,
            address=product.token_address.lower(),
            first_seen_at_block=first_seen_at_block,
            first_seen_at=first_seen_at,
            features={feature, ERC4626Feature.share_price_equivalence},
            updated_at=updated_at,
            deposit_count=0,
            redeem_count=0,
        )
        row = create_vault_scan_record(
            web3,
            detection,
            block_number,
            token_cache,
        )
        row["_gmx_component_addresses"] = tuple(address.lower() for address in product.component_addresses)
        row["_gmx_accepted_deposit_tokens"] = tuple(address.lower() for address in product.accepted_deposit_tokens)
        row["_gmx_enabled"] = product.is_enabled
        row["_deposit_closed_reason"] = None if product.is_enabled else GMX_DISABLED_DEPOSIT_REASON
        if existing is not None:
            # Refresh scanner-owned facts without discarding manual or
            # downstream enrichment fields attached to a healthy row.
            merged_row = existing.copy()
            merged_row.update(row)
            row = merged_row
        return spec, row, existing is None

    results = Parallel(n_jobs=max_workers, backend="threading")(delayed(fetch_product_row)(product) for product in products)
    rows = {spec: row for spec, row, _is_new in results}
    inserted = sum(is_new for _spec, _row, is_new in results)
    updated = len(results) - inserted

    vault_db._merge_rows(rows)
    logger.info("GMX V2 catalogue sync: %d products, %d inserted, %d updated", len(products), inserted, updated)
    return GMXVaultCatalogueSyncResult(len(products), inserted, updated)
