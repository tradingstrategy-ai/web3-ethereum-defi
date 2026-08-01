"""Build runtime Asseto adapter products from the persisted public registry.

The recurring scanner persists Asseto vault rows across process restarts, while
the adapter needs an in-memory product record.  This module rebuilds that
runtime registry before a daily scan and registers newly published supported
products from the same fresh API snapshot.
"""

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path

from eth_typing import HexAddress
from web3 import Web3
from web3.exceptions import Web3Exception

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.asseto.constants import AssetoProduct, install_asseto_runtime_products
from eth_defi.tokenised_fund.asseto.offchain_api import AssetoOffchainProduct
from eth_defi.tokenised_fund.asseto.offchain_metadata import DEFAULT_ASSETO_REGISTRY_CACHE_PATH, fetch_asseto_registry
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AssetoRegistryRefreshResult:
    """Summary of runtime registry preparation for one scanner invocation.

    :ivar status:
        Registry source status, either ``fresh`` or ``stale``.
    :ivar runtime_product_count:
        Number of products installed for adapter construction.
    :ivar registered_product_count:
        Number of newly persisted vault metadata records.
    :ivar diagnostics:
        Non-fatal skipped-product and stale-source diagnostics.
    """

    #: Registry source status, either ``fresh`` or ``stale``.
    status: str
    #: Number of products installed for adapter construction.
    runtime_product_count: int
    #: Number of newly persisted vault metadata records.
    registered_product_count: int
    #: Non-fatal skipped-product and stale-source diagnostics.
    diagnostics: tuple[str, ...]


def resolve_asseto_denomination_symbol(product: AssetoOffchainProduct) -> str | None:
    """Resolve the accounting denomination from Asseto's public product data."""

    if product.denomination_symbol:
        return product.denomination_symbol.upper()
    return "USD" if product.product_type == "stoken" else None


def create_asseto_runtime_product(
    product: AssetoOffchainProduct,
    first_seen_at_block: int,
    first_seen_at: datetime.datetime,
) -> AssetoProduct:
    """Create one adapter product from API identity and onchain discovery data."""

    return AssetoProduct(
        chain_id=product.chain_id,
        token=product.contract_address,
        symbol=product.symbol or product.product_name,
        product_name=product.full_name or product.product_name,
        manager=None,
        pricer=None,
        collateral=product.denomination_address,
        first_seen_at_block=first_seen_at_block,
        first_seen_at=first_seen_at,
        denomination_symbol=resolve_asseto_denomination_symbol(product),
        offchain_product_id=product.product_id,
        offchain_product_name=product.product_name,
        description=product.introduction,
    )


def fetch_asseto_deployment_block(web3: Web3, address: HexAddress, end_block: int) -> int:
    """Find the first token-code block for a newly registered product."""

    checksum_address = Web3.to_checksum_address(address)
    if not web3.eth.get_code(checksum_address, block_identifier=end_block):
        raise ValueError(f"No contract code for Asseto product {address} at block {end_block}")
    low = 0
    high = end_block
    while low < high:
        middle = (low + high) // 2
        if web3.eth.get_code(checksum_address, block_identifier=middle):
            high = middle
        else:
            low = middle + 1
    return low


def _create_detection(product: AssetoProduct) -> ERC4262VaultDetection:
    """Create the minimal synthetic detection needed for metadata upserts."""

    return ERC4262VaultDetection(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        features={ERC4626Feature.asseto_like},
        updated_at=native_datetime_utc_now(),
        deposit_count=0,
        redeem_count=0,
    )


def fetch_asseto_registry_preparation(  # noqa: PLR0914 - explicit product registration state keeps database writes auditable.
    *,
    vault_db_path: Path,
    enabled_chain_ids: frozenset[int],
    cache_path: Path = DEFAULT_ASSETO_REGISTRY_CACHE_PATH,
    token_cache: TokenDiskCache | None = None,
) -> AssetoRegistryRefreshResult:
    """Refresh Asseto metadata, register new products and rebuild adapters.

    Fresh API data may register products and update their metadata.  Stale data
    is deliberately limited to rebuilding runtime entries for already-persisted
    Asseto rows, so an outage cannot write old API values into the vault
    database.

    :param vault_db_path:
        Persistent vault metadata database used by the recurring scanner.
    :param enabled_chain_ids:
        Chains available to the operator in this invocation.
    :param cache_path:
        Shared Asseto registry cache path.
    :param token_cache:
        Optional shared token cache for metadata reads.
    :return:
        Preparation outcome and non-fatal product diagnostics.
    """

    registry = fetch_asseto_registry(cache_path=cache_path)
    if not registry.is_usable:
        raise RuntimeError(f"Asseto registry is unavailable: {registry.diagnostics}")
    vault_db = VaultDatabase.read(vault_db_path) if vault_db_path.exists() else VaultDatabase()
    diagnostics: list[str] = [registry.diagnostics] if registry.diagnostics else []
    runtime_products: list[AssetoProduct] = []
    new_rows = {}
    metadata_updated = False
    registered_count = 0
    owned_cache = token_cache is None
    if token_cache is None:
        token_cache = TokenDiskCache()
    try:
        for offchain_product in registry.products:
            if offchain_product.chain_id not in enabled_chain_ids:
                diagnostics.append(f"chain {offchain_product.chain_id} is disabled for {offchain_product.contract_address}")
                continue
            if resolve_asseto_denomination_symbol(offchain_product) is None:
                diagnostics.append(f"no denomination for {offchain_product.contract_address}")
                continue
            spec = VaultSpec(offchain_product.chain_id, offchain_product.contract_address)
            existing_row = vault_db.rows.get(spec)
            if existing_row is not None:
                detection = existing_row["_detection_data"]
                runtime_products.append(create_asseto_runtime_product(offchain_product, detection.first_seen_at_block, detection.first_seen_at))
                if registry.status == "fresh" and offchain_product.introduction and existing_row.get("Description") != offchain_product.introduction:
                    existing_row["Description"] = offchain_product.introduction
                    metadata_updated = True
                continue
            if registry.status != "fresh":
                continue
            try:
                web3 = create_multi_provider_web3(read_json_rpc_url(offchain_product.chain_id))
                end_block = web3.eth.block_number
                deployment_block = fetch_asseto_deployment_block(web3, offchain_product.contract_address, end_block)
                timestamp = web3.eth.get_block(deployment_block)["timestamp"]
                first_seen_at = datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC).replace(tzinfo=None)
                runtime_product = create_asseto_runtime_product(offchain_product, deployment_block, first_seen_at)
                # ``create_vault_scan_record()`` constructs the normal adapter,
                # so register this one product before its first metadata read.
                install_asseto_runtime_products([runtime_product])
                row = create_vault_scan_record(web3, detection=_create_detection(runtime_product), block_identifier=end_block, token_cache=token_cache)
            except (OSError, ValueError, RuntimeError, Web3Exception) as exc:
                diagnostics.append(f"could not register {offchain_product.contract_address}: {exc}")
                logger.warning("Could not register Asseto product %s: %s", offchain_product.contract_address, exc)
                continue
            runtime_products.append(runtime_product)
            new_rows[spec] = row
            registered_count += 1

        install_asseto_runtime_products(runtime_products)
        if registry.status == "fresh" and (new_rows or metadata_updated):
            if new_rows:
                vault_db._merge_rows(new_rows)
            vault_db_path.parent.mkdir(parents=True, exist_ok=True)
            vault_db.write(vault_db_path)
    finally:
        if owned_cache:
            token_cache.commit()

    return AssetoRegistryRefreshResult(registry.status, len(runtime_products), registered_count, tuple(diagnostics))
