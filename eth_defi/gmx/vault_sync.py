"""Synchronise the onchain GMX V2 catalogue into the common vault database."""

import datetime
import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from eth_typing import HexAddress
from joblib import Parallel, delayed
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.gmx.links import get_gmx_pool_details_link
from eth_defi.gmx.symbols import SYMBOL_NORMALISE
from eth_defi.gmx.vault_catalog import GMXVaultProduct, fetch_gmx_v2_vault_products
from eth_defi.token import TokenDiskCache, fetch_erc20_details
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import GMX_SINGLE_SIDED_USDC_NOTE
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Deposit-closure reason exported for disabled GMX products.
GMX_DISABLED_DEPOSIT_REASON: str = "GMX product disabled"

#: Number of equal candidate names required before adding an address suffix.
GMX_NAME_COLLISION_SIZE: int = 2

#: GMX Reader uses this value for the absent index token of a swap-only pool.
GMX_ZERO_ADDRESS: HexAddress = "0x0000000000000000000000000000000000000000"

#: GMX index markets tracking a real-world commodity, equity or financial index.
GMX_TRADFI_INDEX_MARKETS: frozenset[str] = frozenset(
    {
        "BRENTOIL/USD",
        "GOLD/USD",
        "NATGAS/USD",
        "QQQ/USD",
        "SILVER/USD",
        "SPCX/USD",
        "SPY/USD",
        "WTIOIL/USD",
    }
)


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


def _format_gmx_product_name(
    product: GMXVaultProduct,
    *,
    long_token_symbol: str,
    short_token_symbol: str,
    market_labels: Mapping[str, str],
) -> str:
    """Build a concise GMX product name that includes the index market.

    The common vault database identifies a product by its chain ID and share
    token address, not its display name. GM markets must nevertheless show
    their index market: multiple risk-isolated markets may share the same
    long-short backing-token pair.

    :param product:
        Current GM or GLV product definition.
    :param long_token_symbol:
        Symbol of the backing asset for profitable long positions.
    :param short_token_symbol:
        Symbol of the backing asset for profitable short positions.
    :param market_labels:
        Lower-case GM market address to index-market label mapping.
    :return:
        Compact label, for example ``"GM DOGE [WBTC-USDC]"`` or
        ``"GLV [WBTC-USDC]"``.
    """

    backing_pair = f"{long_token_symbol}-{short_token_symbol}"
    if product.product_type == "gm":
        market_address = product.component_addresses[0]
        market_label = _format_market_label(market_address, market_labels)
        if market_label is not None:
            return f"GM {market_label.removesuffix('/USD')} [{backing_pair}]"
        return f"GM swap [{backing_pair}]"
    return f"GLV [{backing_pair}]"


def _disambiguate_gmx_product_names(candidate_names: Mapping[str, str]) -> dict[str, str]:
    """Add the smallest stable share-address suffix required for duplicate labels.

    The normal GM format includes the full economic identity.  A suffix is
    added only when two catalogue products still resolve to the same compact
    label, preserving short names while guaranteeing unique display strings.

    :param candidate_names:
        Lower-case GM or GLV share-token address to compact name mapping.
    :return:
        Display-name mapping with duplicate candidates disambiguated.
    """

    addresses_by_name: defaultdict[str, list[str]] = defaultdict(list)
    for address, name in candidate_names.items():
        addresses_by_name[name].append(address)

    result = dict(candidate_names)
    for name, addresses in addresses_by_name.items():
        if len(addresses) < GMX_NAME_COLLISION_SIZE:
            continue

        suffix_length = next(length for length in range(4, 41) if len({address.removeprefix("0x")[-length:] for address in addresses}) == len(addresses))
        for address in addresses:
            suffix = address.removeprefix("0x")[-suffix_length:]
            result[address] = f"{name} · {suffix}"
    return result


def _format_market_label(market_address: HexAddress, market_labels: Mapping[str, str]) -> str | None:
    """Look up the index-market label for one GM market.

    A missing label means that the GMX Reader reported a swap-only pool with no
    index token.  Keeping that distinction as ``None`` avoids inventing an
    index market from a contract address and lets the caller describe the
    product as swap liquidity instead of perpetual-trading liquidity.

    :param market_address:
        GM market-token address.
    :param market_labels:
        Lower-case GM market address to canonical ``ASSET/USD`` label mapping.
    :return:
        Canonical index-market label, or ``None`` for a swap-only pool.
    """

    return market_labels.get(market_address.lower())


def _format_tradfi_synthetic_exposure_explanation(index_markets: tuple[str, ...]) -> str:
    """Explain the synthetic nature of a GMX TradFi market when applicable.

    :param index_markets:
        Canonical index-market labels associated with one GM or GLV product.
    :return:
        Empty string for crypto markets, otherwise a sentence clarifying that
        the pool does not custody the referenced real-world asset.
    """

    tradfi_markets = tuple(dict.fromkeys(market for market in index_markets if market in GMX_TRADFI_INDEX_MARKETS))
    if not tradfi_markets:
        return ""
    if len(tradfi_markets) == 1:
        return f" This pool provides synthetic exposure to the {tradfi_markets[0]} reference price; it does not hold the underlying real-world asset or financial instrument."
    return f" For its {', '.join(tradfi_markets)} markets, this pool provides synthetic price exposure rather than holding the underlying real-world assets or financial instruments."


def _format_backing_token_explanation(long_token_symbol: str, short_token_symbol: str, *, is_perpetual_market: bool) -> tuple[str, tuple[str, ...]]:
    """Describe whether a GMX pool uses one or two backing assets.

    :param long_token_symbol:
        Symbol of the long backing asset.
    :param short_token_symbol:
        Symbol of the short backing asset.
    :param is_perpetual_market:
        Whether the product backs a perpetual index market rather than swaps.
    :return:
        Introductory liquidity sentence fragment and Markdown bullet points.
    """

    if not is_perpetual_market:
        if long_token_symbol == short_token_symbol:
            return (
                f"Liquidity providers supply {long_token_symbol}",
                (f"- **Pool token:** {long_token_symbol} — the asset supplied as swap liquidity.",),
            )
        return (
            f"Liquidity providers supply {long_token_symbol} and {short_token_symbol}",
            (
                f"- **First pool token:** {long_token_symbol} — one asset supplied as swap liquidity.",
                f"- **Second pool token:** {short_token_symbol} — the other asset supplied as swap liquidity.",
            ),
        )

    if long_token_symbol == short_token_symbol:
        return (
            f"Liquidity providers supply {long_token_symbol}",
            (f"- **Long and short backing token:** {long_token_symbol} — this single asset backs profitable long and short positions.",),
        )
    return (
        f"Liquidity providers supply {long_token_symbol} and {short_token_symbol}",
        (
            f"- **Long backing token:** {long_token_symbol} — backs and settles profitable long positions.",
            f"- **Short backing token:** {short_token_symbol} — backs and settles profitable short positions.",
        ),
    )


def _format_gmx_product_description(
    product: GMXVaultProduct,
    *,
    long_token_symbol: str,
    short_token_symbol: str,
    market_labels: Mapping[str, str],
) -> str:
    """Build the complete human-readable liquidity-provider description.

    The GMX share token alone does not say which perpetual market it backs.
    Describe the market and both pool assets explicitly so that equal-looking
    ``WETH-USDC`` products remain distinguishable in the public catalogue.

    :param product:
        Current GM or GLV composition returned by the GMX Reader contracts.
    :param long_token_symbol:
        Symbol of the asset backing profitable long positions.
    :param short_token_symbol:
        Symbol of the asset backing profitable short positions.
    :param market_labels:
        Lower-case GM market address to index-market label mapping.
    :return:
        Markdown description suitable for the common vault ``_description``
        field.
    """

    is_glv = product.product_type == "glv"
    market_addresses = product.component_addresses[3:] if is_glv else product.component_addresses[:1]
    index_markets = tuple(label for address in market_addresses if (label := _format_market_label(address, market_labels)) is not None)
    is_swap_only_gm = not is_glv and not index_markets
    liquidity_intro, backing_token_bullets = _format_backing_token_explanation(
        long_token_symbol,
        short_token_symbol,
        is_perpetual_market=not is_swap_only_gm,
    )
    if is_swap_only_gm:
        description = f"{liquidity_intro} to provide liquidity for GMX token swaps. They earn a share of swap fees and bear changes in the value of the pool tokens."
        index_market_bullet = "- **Activity:** Swap-only market — this pool has no index market and does not back perpetual positions."
    else:
        description = f"{liquidity_intro} to provide liquidity for GMX perpetual trading and swaps. They earn a share of trading, borrowing, liquidation and swap fees, and benefit when traders make net losses. They bear net trader profits and changes in the value of the backing tokens."
    if is_glv:
        description += " This GMX Liquidity Vault allocates the supplied liquidity across its compatible GM markets according to GMX configuration."
        index_market_bullet = f"- **Supported index markets:** {', '.join(index_markets)} — the price markets for which the underlying GM pools back trader positions."
    elif not is_swap_only_gm:
        index_market_bullet = f"- **Index market:** {index_markets[0]} — the price market for which traders take long and short positions."
    description += _format_tradfi_synthetic_exposure_explanation(index_markets)
    return "\n".join((description, "", index_market_bullet, *backing_token_bullets))


def _build_market_labels(products: tuple[GMXVaultProduct, ...], token_symbols: Mapping[str, str]) -> dict[str, str]:
    """Build canonical index labels directly from the GMX Reader catalogue.

    The Reader supplies the index-token address for every perpetual GM pool,
    including single-token and delisted products omitted by the trading REST
    catalogue.  This avoids the trading adapter's internal ``2`` suffix and
    keeps a REST outage from overwriting public vault metadata with fallbacks.

    :param products:
        Complete same-chain GM and GLV product catalogue from the GMX Reader.
    :param token_symbols:
        Lower-case token-address to ERC-20 symbol mapping for the catalogue.
    :return:
        Lower-case GM market address to canonical ``ASSET/USD`` label mapping.
    """

    labels = {}
    for product in products:
        if product.product_type != "gm":
            continue
        market_address, index_token, _long_token, _short_token = product.component_addresses
        if index_token.lower() == GMX_ZERO_ADDRESS:
            continue
        index_symbol = token_symbols[index_token.lower()]
        labels[market_address.lower()] = f"{SYMBOL_NORMALISE.get(index_symbol, index_symbol)}/USD"
    return labels


def _normalise_gmx_row(row: VaultRow, *, product: GMXVaultProduct, name: str, description: str) -> VaultRow:
    """Apply current GMX catalogue identity to a scanner row."""

    row["Name"] = name
    row["Protocol"] = "GMX"
    row["Denomination"] = "USDC"
    row["Link"] = get_gmx_pool_details_link(product.chain_id, product.token_address)
    row["_notes"] = GMX_SINGLE_SIDED_USDC_NOTE
    row["_short_description"] = None
    row["_description"] = description
    row["_synthetic_usd_denomination"] = False
    row["_gmx_product_type"] = product.product_type
    row["_deposits_open"] = None if product.is_enabled else False
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
    token_addresses = {address for product in products for address in (*product.accepted_deposit_tokens, *(product.component_addresses[1:2] if product.product_type == "gm" and product.component_addresses[1].lower() != GMX_ZERO_ADDRESS else ()))}
    token_symbols = {address.lower(): _fetch_token_symbol(web3, web3.eth.chain_id, address, token_cache) for address in token_addresses}
    market_labels = _build_market_labels(products, token_symbols)
    backing_token_symbols = {product.token_address.lower(): tuple(token_symbols[address.lower()] for address in product.accepted_deposit_tokens) for product in products}
    product_names = _disambiguate_gmx_product_names(
        {
            product.token_address.lower(): _format_gmx_product_name(
                product,
                long_token_symbol=backing_token_symbols[product.token_address.lower()][0],
                short_token_symbol=backing_token_symbols[product.token_address.lower()][1],
                market_labels=market_labels,
            )
            for product in products
        }
    )
    product_descriptions = {
        product.token_address.lower(): _format_gmx_product_description(
            product,
            long_token_symbol=backing_token_symbols[product.token_address.lower()][0],
            short_token_symbol=backing_token_symbols[product.token_address.lower()][1],
            market_labels=market_labels,
        )
        for product in products
    }

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
        row = _normalise_gmx_row(
            row,
            product=product,
            name=product_names[product.token_address.lower()],
            description=product_descriptions[product.token_address.lower()],
        )
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
                        "_notes",
                        "_short_description",
                        "_description",
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
