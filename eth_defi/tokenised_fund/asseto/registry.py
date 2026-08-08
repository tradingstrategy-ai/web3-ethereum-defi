"""Build runtime Asseto adapter products from the persisted public registry.

The recurring scanner persists Asseto vault rows across process restarts, while
the adapter needs an in-memory product record.  This module rebuilds that
runtime registry before a daily scan and registers newly published supported
products from the same fresh API snapshot.
"""

import datetime
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from eth_typing import HexAddress
from web3 import Web3
from web3.exceptions import Web3Exception

from eth_defi.compat import native_datetime_utc_now
from eth_defi.currency_api.constants import SOURCE_NAME
from eth_defi.currency_api.database import CurrencyRateDatabase
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.tokenised_fund.asseto.constants import ASSETO_PRODUCTS, ASSETO_PRODUCTS_BY_TOKEN, ASSETO_USD_DENOMINATIONS, AssetoProduct, install_asseto_runtime_products
from eth_defi.tokenised_fund.asseto.offchain_api import AssetoOffchainProduct
from eth_defi.tokenised_fund.asseto.offchain_metadata import DEFAULT_ASSETO_REGISTRY_CACHE_PATH, fetch_asseto_registry
from eth_defi.tokenised_fund.asseto.vault import create_asseto_short_description
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.data_file_export import resolve_exchange_rate_database_path
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
    :ivar available_specs:
        Persisted products with a runtime adapter installed for this cycle.
    """

    #: Registry source status, either ``fresh`` or ``stale``.
    status: str
    #: Number of products installed for adapter construction.
    runtime_product_count: int
    #: Number of newly persisted vault metadata records.
    registered_product_count: int
    #: Non-fatal skipped-product and stale-source diagnostics.
    diagnostics: tuple[str, ...]
    #: Persisted products with a runtime adapter installed for this cycle.
    available_specs: frozenset[VaultSpec] = frozenset()


def resolve_asseto_denomination_symbol(product: AssetoOffchainProduct) -> str | None:
    """Resolve the accounting denomination from Asseto's public product data.

    Asseto omits the symbol on its ``stoken`` products even though their NAV
    series is USD-denominated. All other products need an explicit symbol so
    the scanner can safely select a conversion history.

    :param product:
        Normalised product read from Asseto's public registry.
    :return:
        Upper-case accounting currency, or ``None`` when it is not usable.
    """

    if product.denomination_symbol:
        return product.denomination_symbol.upper()
    return "USD" if product.product_type and product.product_type.casefold() == "stoken" else None


def load_usd_exchange_rates(
    database_path: Path,
    denomination_symbols: Iterable[str | None],
) -> dict[str, tuple[tuple[int, Decimal], ...]]:
    """Load historical quote-currency-per-USD rates for Asseto products.

    Asseto NAV values in non-USD currencies are divided by these rates before
    entering the shared USD history.  The targeted backfill and daily scanner
    intentionally share this exact conversion rule.

    :param database_path:
        Currency API DuckDB produced by ``scan-currencies``.
    :param denomination_symbols:
        Accounting currencies requested by the selected products.
    :return:
        Exchange-rate history keyed by upper-case currency symbol.
    :raise RuntimeError:
        If required currency history is unavailable.
    """

    required = {symbol.upper() for symbol in denomination_symbols if symbol and symbol.upper() not in ASSETO_USD_DENOMINATIONS}
    if not required:
        return {}
    if not database_path.exists():
        currencies = ", ".join(sorted(required))
        raise RuntimeError(f"Asseto products require {currencies}/USD history, but the currency database does not exist at {database_path}; run scan-currencies first")

    database = CurrencyRateDatabase(database_path)
    try:
        rates_df = database.get_rates_dataframe(base_currency="usd", source=SOURCE_NAME)
    finally:
        database.close()

    result: dict[str, tuple[tuple[int, Decimal], ...]] = {}
    for symbol in sorted(required):
        selected = rates_df.loc[rates_df["quote_currency"].str.casefold() == symbol.casefold()].sort_values("date")
        if selected.empty:
            raise RuntimeError(f"Asseto product history requires {symbol}/USD rates in {database_path}; run scan-currencies with QUOTE_CURRENCIES including {symbol.lower()}")
        result[symbol] = tuple(
            (
                int(datetime.datetime.combine(row.date, datetime.time.min, tzinfo=datetime.UTC).timestamp()),
                Decimal(str(row.rate)),
            )
            for row in selected.itertuples(index=False)
        )
    return result


def create_asseto_runtime_product(
    product: AssetoOffchainProduct,
    first_seen_at_block: int,
    first_seen_at: datetime.datetime,
    usd_exchange_rates: tuple[tuple[int, Decimal], ...] = (),
) -> AssetoProduct:
    """Create one adapter product from API identity and onchain discovery.

    The scheduled and manual paths use this constructor so product identity,
    offchain NAV lookup and non-USD conversion state cannot diverge.

    :param product:
        Normalised public Asseto product entry.
    :param first_seen_at_block:
        First onchain block containing the token's code.
    :param first_seen_at:
        Naive UTC token deployment timestamp.
    :param usd_exchange_rates:
        Historical units of the denomination currency per USD, if required.
    :return:
        Adapter-ready Asseto product record.
    """

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
        usd_exchange_rates=usd_exchange_rates,
        offchain_product_id=product.product_id,
        offchain_product_name=product.product_name,
        description=product.introduction,
    )


def fetch_asseto_deployment_block(web3: Web3, address: HexAddress, end_block: int) -> int:
    """Find the first token-code block for a newly registered product.

    The public registry has no deployment block, so a binary search over an
    archive-capable provider supplies the start boundary for history scans.

    :param web3:
        Archive-capable product-chain connection.
    :param address:
        Asseto token contract address.
    :param end_block:
        Latest block at which the token must have deployed code.
    :return:
        Earliest block containing token runtime code.
    :raise ValueError:
        If the registry address has no code at ``end_block``.
    """

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


def create_asseto_detection(product: AssetoProduct) -> ERC4262VaultDetection:
    """Create the minimal synthetic detection needed for metadata upserts.

    Asseto products do not emit ERC-4626 discovery events, so their metadata
    read uses this equivalent persisted detection record.

    :param product:
        Adapter-ready Asseto product record.
    :return:
        Detection compatible with the shared vault metadata scanner.
    """

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


def create_asseto_lead(product: AssetoProduct) -> PotentialVaultMatch:
    """Create a synthetic discovery lead for one Asseto product.

    :param product:
        Fully resolved Asseto adapter product.
    :return:
        Lead compatible with the persistent vault metadata database.
    """

    return PotentialVaultMatch(
        chain=product.chain_id,
        address=product.token,
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        deposit_count=0,
        withdrawal_count=0,
    )


def _is_usable_metadata_row(row: dict[str, object]) -> bool:
    """Return whether a metadata read produced a publishable Asseto row.

    :param row:
        Record returned by the shared vault metadata scanner.
    :return:
        ``True`` when the row has a live NAV and is not a broken placeholder.
    """

    return row.get("NAV") is not None and not str(row.get("Name", "")).startswith("<broken:")


def _restore_runtime_product(
    product: AssetoProduct,
    previous_product: AssetoProduct | None,
    previous_product_by_token: AssetoProduct | None,
) -> None:
    """Restore runtime mappings after an unsuccessful temporary registration.

    :param product:
        Product temporarily installed for shared adapter construction.
    :param previous_product:
        Mapping value before the temporary installation, if any.
    :param previous_product_by_token:
        Address mapping value before the temporary installation, if any.
    :return:
        None.
    """

    key = (product.chain_id, product.token)
    if previous_product is None:
        ASSETO_PRODUCTS.pop(key, None)
    else:
        ASSETO_PRODUCTS[key] = previous_product
    if previous_product_by_token is None:
        ASSETO_PRODUCTS_BY_TOKEN.pop(product.token, None)
    else:
        ASSETO_PRODUCTS_BY_TOKEN[product.token] = previous_product_by_token


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
    diagnostics: list[str] = [registry.diagnostics] if registry.diagnostics else []
    if not vault_db_path.exists():
        diagnostics.append(f"vault metadata database does not exist: {vault_db_path}")
        return AssetoRegistryRefreshResult(registry.status, 0, 0, tuple(diagnostics))

    vault_db = VaultDatabase.read(vault_db_path)
    runtime_products: list[AssetoProduct] = []
    new_rows = {}
    metadata_updated = False
    registered_count = 0
    available_specs: set[VaultSpec] = set()
    exchange_rate_database_path = resolve_exchange_rate_database_path(vault_db_path.parent)
    exchange_rates_by_symbol: dict[str, tuple[tuple[int, Decimal], ...]] = {}
    unusable_symbols: set[str] = set()
    web3_by_chain: dict[int, Web3] = {}
    end_blocks: dict[int, int] = {}
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
            denomination_symbol = resolve_asseto_denomination_symbol(offchain_product)
            assert denomination_symbol is not None
            if denomination_symbol not in ASSETO_USD_DENOMINATIONS and denomination_symbol not in exchange_rates_by_symbol and denomination_symbol not in unusable_symbols:
                try:
                    exchange_rates_by_symbol.update(load_usd_exchange_rates(exchange_rate_database_path, (denomination_symbol,)))
                except RuntimeError as exc:
                    unusable_symbols.add(denomination_symbol)
                    diagnostics.append(f"no {denomination_symbol}/USD history for {offchain_product.contract_address}: {exc}")
            if denomination_symbol in unusable_symbols:
                continue
            spec = VaultSpec(offchain_product.chain_id, offchain_product.contract_address)
            existing_row = vault_db.rows.get(spec)
            if existing_row is not None:
                detection = existing_row["_detection_data"]
                runtime_products.append(create_asseto_runtime_product(offchain_product, detection.first_seen_at_block, detection.first_seen_at, exchange_rates_by_symbol.get(denomination_symbol, ())))
                available_specs.add(spec)
                if registry.status == "fresh" and offchain_product.introduction and existing_row.get("_description") != offchain_product.introduction:
                    existing_row["_description"] = offchain_product.introduction
                    existing_row["_short_description"] = create_asseto_short_description(offchain_product.introduction)
                    metadata_updated = True
                continue
            if registry.status != "fresh":
                continue
            runtime_product: AssetoProduct | None = None
            previous_product: AssetoProduct | None = None
            previous_product_by_token: AssetoProduct | None = None
            try:
                web3 = web3_by_chain.get(offchain_product.chain_id)
                if web3 is None:
                    web3 = create_multi_provider_web3(read_json_rpc_url(offchain_product.chain_id))
                    web3_by_chain[offchain_product.chain_id] = web3
                    end_blocks[offchain_product.chain_id] = web3.eth.block_number
                end_block = end_blocks[offchain_product.chain_id]
                deployment_block = fetch_asseto_deployment_block(web3, offchain_product.contract_address, end_block)
                timestamp = web3.eth.get_block(deployment_block)["timestamp"]
                first_seen_at = datetime.datetime.fromtimestamp(timestamp, tz=datetime.UTC).replace(tzinfo=None)
                runtime_product = create_asseto_runtime_product(offchain_product, deployment_block, first_seen_at, exchange_rates_by_symbol.get(denomination_symbol, ()))
                # ``create_vault_scan_record()`` constructs the normal adapter,
                # so register this one product before its first metadata read.
                previous_product = ASSETO_PRODUCTS.get((runtime_product.chain_id, runtime_product.token))
                previous_product_by_token = ASSETO_PRODUCTS_BY_TOKEN.get(runtime_product.token)
                install_asseto_runtime_products([runtime_product])
                row = create_vault_scan_record(web3, detection=create_asseto_detection(runtime_product), block_identifier=end_block, token_cache=token_cache)
            except (OSError, ValueError, RuntimeError, Web3Exception) as exc:
                if runtime_product is not None:
                    _restore_runtime_product(runtime_product, previous_product, previous_product_by_token)
                diagnostics.append(f"could not register {offchain_product.contract_address}: {exc}")
                logger.warning("Could not register Asseto product %s: %s", offchain_product.contract_address, exc)
                continue
            if not _is_usable_metadata_row(row):
                assert runtime_product is not None
                _restore_runtime_product(runtime_product, previous_product, previous_product_by_token)
                diagnostics.append(f"metadata read for {offchain_product.contract_address} did not produce a usable NAV")
                continue
            runtime_products.append(runtime_product)
            new_rows[spec] = row
            available_specs.add(spec)
            registered_count += 1

        install_asseto_runtime_products(runtime_products)
        if registry.status == "fresh" and (new_rows or metadata_updated):
            if new_rows:
                vault_db._merge_rows(new_rows)
                vault_db.leads.update({VaultSpec(product.chain_id, product.token): create_asseto_lead(product) for product in runtime_products if VaultSpec(product.chain_id, product.token) in new_rows})
            vault_db_path.parent.mkdir(parents=True, exist_ok=True)
            vault_db.write(vault_db_path)
    finally:
        if owned_cache:
            token_cache.commit()

    return AssetoRegistryRefreshResult(registry.status, len(runtime_products), registered_count, tuple(diagnostics), frozenset(available_specs))
