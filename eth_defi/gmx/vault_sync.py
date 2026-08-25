"""Synchronise the onchain GMX V2 catalogue into the common vault database."""

import datetime
import logging
from dataclasses import dataclass

from eth_typing import HexAddress
from joblib import Parallel, delayed
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.gmx.links import get_gmx_pool_details_link
from eth_defi.gmx.vault_catalog import GMXVaultProduct, fetch_gmx_v2_vault_products
from eth_defi.token import TokenDiskCache, fetch_erc20_details
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Deposit-closure reason exported for disabled GMX products.
GMX_DISABLED_DEPOSIT_REASON: str = "GMX product disabled"


@dataclass(slots=True, frozen=True)
class GMXVaultCatalogueSyncResult:
    """Summarise one GMX metadata reconciliation.

    Separates the complete current catalogue size from newly inserted and
    refreshed rows for scanner diagnostics.
    """

    #: Number of GM and GLV products returned by GMX.
    products: int

    #: Number of products newly inserted into the common vault database.
    inserted: int

    #: Number of existing products refreshed in the common vault database.
    updated: int


def _feature_for_product(product: GMXVaultProduct) -> ERC4626Feature:
    """Map an onchain product kind to its persisted adapter feature."""

    return ERC4626Feature.gmx_gm if product.product_type == "gm" else ERC4626Feature.gmx_glv


def _fetch_token_symbol(web3: Web3, chain_id: int, address: HexAddress, token_cache: TokenDiskCache) -> str:
    """Fetch an ERC-20 symbol for GMX product-name construction."""

    token = fetch_erc20_details(web3, address, chain_id=chain_id, cache=token_cache, cause_diagnostics_message="Naming GMX V2 vault products")
    return str(token.symbol or address[:8])


def _format_gmx_product_name(web3: Web3, product: GMXVaultProduct, token_cache: TokenDiskCache) -> str:
    """Build the concise GMX trading-pair display name.

    The common vault database identifies a product by its chain ID and share
    token address, not its display name. Keeping only the product type and
    long-short pair makes the public catalogue scannable while the
    migration can safely update existing rows without a name-derived key.

    :param web3:
        Product-chain Web3 connection used to resolve token symbols.
    :param product:
        Current GM or GLV product definition.
    :param token_cache:
        Shared ERC-20 metadata cache.
    :return:
        Product-type and long-short token-pair label, for example
        ``"GM WBTC-USDC"`` or ``"GLV WBTC-USDC"``.
    """

    symbols = tuple(_fetch_token_symbol(web3, product.chain_id, address, token_cache) for address in product.accepted_deposit_tokens)
    product_label = "GM" if product.product_type == "gm" else "GLV"
    return f"{product_label} {'-'.join(symbols)}"


def _normalise_gmx_row(row: VaultRow, *, product: GMXVaultProduct, name: str) -> VaultRow:
    """Apply current GMX catalogue identity to a scanner row."""

    row["Name"] = name
    row["Protocol"] = "GMX"
    row["Denomination"] = "USDC"
    row["Link"] = get_gmx_pool_details_link(product.chain_id, product.token_address)
    row["_short_description"] = None
    row["_synthetic_usd_denomination"] = False
    row["_gmx_product_type"] = product.product_type
    if not product.is_enabled:
        row["_deposits_open"] = False
    return row


def fetch_and_sync_gmx_vault_catalogue(
    *,
    web3: Web3,
    vault_db: VaultDatabase,
    token_cache: TokenDiskCache,
    block_number: int | None = None,
    max_workers: int = 8,
) -> GMXVaultCatalogueSyncResult:
    """Upsert GM and GLV products without disturbing unrelated vault rows.

    New products record the block at which this catalogue first observes them.

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
    product_names = {product.token_address.lower(): _format_gmx_product_name(web3, product, token_cache) for product in products}

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
        scan_failed = str(row.get("Name", "")).startswith("<broken:")
        row["_gmx_component_addresses"] = tuple(address.lower() for address in product.component_addresses)
        row["_gmx_accepted_deposit_tokens"] = tuple(address.lower() for address in product.accepted_deposit_tokens)
        row["_gmx_enabled"] = product.is_enabled
        row["_deposit_closed_reason"] = None if product.is_enabled else GMX_DISABLED_DEPOSIT_REASON
        row = _normalise_gmx_row(row, product=product, name=product_names[product.token_address.lower()])
        if existing is not None:
            merged_row = existing.copy()
            if scan_failed:
                # A transient row-level RPC failure produces placeholder scan
                # fields. Keep the last healthy metadata while still applying
                # current catalogue identity, composition and enabled status.
                catalogue_fields = {
                    key: row[key]
                    for key in (
                        "_detection_data",
                        "_gmx_component_addresses",
                        "_gmx_accepted_deposit_tokens",
                        "_gmx_enabled",
                        "_deposit_closed_reason",
                        "_gmx_product_type",
                        "Name",
                        "Protocol",
                        "Denomination",
                        "Link",
                        "_short_description",
                        "_synthetic_usd_denomination",
                        "_deposits_open",
                    )
                    if key in row
                }
                merged_row.update(catalogue_fields)
            else:
                # Refresh scanner-owned facts without discarding manual or
                # downstream enrichment fields attached to a healthy row.
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
