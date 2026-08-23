"""Onchain catalogue for GMX V2 GM and GLV share tokens.

GMX market tokens and GLV tokens are ERC-20 shares, but not ERC-4626 vaults.
They must therefore be enumerated from GMX's Reader contracts rather than the
generic Deposit-event discovery path.  This module is deliberately limited to
current catalogue facts; historical first-usable-block evidence and historical
valuation are separate stages.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from eth_typing import BlockIdentifier, HexAddress
from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.gmx.contracts import get_contract_addresses, get_datastore_contract, get_glv_reader_contract, get_reader_contract
from eth_defi.gmx.keys import is_glv_market_disabled_key, is_market_disabled_key
from eth_defi.token import fetch_erc20_details

logger = logging.getLogger(__name__)


GMX_CHAIN_NAMES_BY_ID = {
    42161: "arbitrum",
    43114: "avalanche",
}


@dataclass(slots=True, frozen=True)
class GMXVaultProduct:
    """Current onchain identity and composition of one GMX V2 share token.

    :param chain_id:
        EVM chain where the product is deployed.
    :param token_address:
        GM market-token or GLV share-token address.
    :param product_type:
        ``"gm"`` for a market token or ``"glv"`` for a multi-market share.
    :param symbol:
        ERC-20 share-token symbol.
    :param name:
        ERC-20 share-token name.
    :param decimals:
        ERC-20 share-token decimals.
    :param component_addresses:
        GM: market, index, long and short token addresses. GLV: GLV, long and
        short token addresses followed by supported GM market addresses.
    :param accepted_deposit_tokens:
        GMX long and short token addresses accepted for this product.
    :param is_enabled:
        Current GMX DataStore status. Disabled products remain present so a
        later metadata synchronisation does not orphan historical rows.
    """

    chain_id: int
    token_address: HexAddress
    product_type: str
    symbol: str
    name: str
    decimals: int
    component_addresses: tuple[HexAddress, ...]
    accepted_deposit_tokens: tuple[HexAddress, ...]
    is_enabled: bool


def _paginate_reader_call(reader_call, *, page_size: int, block_identifier: BlockIdentifier) -> Iterator[tuple]:
    """Yield every item returned by one GMX Reader pagination function.

    :param reader_call:
        Contract-function factory accepting ``(start, end)`` indices.
    :param page_size:
        Positive maximum number of products requested in one call.
    :param block_identifier:
        Block at which the catalogue is read.
    :return:
        Reader items in their onchain order.
    """
    assert page_size > 0, f"Invalid page size: {page_size}"

    start = 0
    while True:
        page = reader_call(start, start + page_size).call(block_identifier=block_identifier)
        if not page:
            return

        yield from page
        if len(page) < page_size:
            return
        start += page_size


def _fetch_token_metadata(web3: Web3, chain_id: int, token_address: HexAddress, token_cache: dict | None) -> tuple[str, str, int]:
    """Fetch the small ERC-20 metadata subset required for a vault row.

    :param web3:
        Web3 connection for the product chain.
    :param chain_id:
        Product chain ID, passed to the shared token cache.
    :param token_address:
        GM or GLV share-token address.
    :param token_cache:
        Optional shared token-details cache.
    :return:
        Share-token symbol, name and decimals.
    """
    token = fetch_erc20_details(
        web3,
        token_address,
        chain_id=chain_id,
        cache=token_cache,
        cause_diagnostics_message="Enumerating GMX V2 vault products",
    )
    return token.symbol, token.name, token.decimals


def _fetch_market_enabled(datastore, market_address: HexAddress, block_identifier: BlockIdentifier) -> bool:
    """Read the current enablement flag for one GM market token.

    :param datastore:
        GMX DataStore contract.
    :param market_address:
        GM market-token address.
    :param block_identifier:
        Block at which the catalogue is read.
    :return:
        ``True`` when GMX has not disabled the market.
    """
    return not datastore.functions.getBool(is_market_disabled_key(market_address)).call(block_identifier=block_identifier)


def _fetch_glv_enabled(datastore, glv_address: HexAddress, markets: tuple[HexAddress, ...], block_identifier: BlockIdentifier) -> bool:
    """Determine whether a GLV has at least one currently enabled market.

    :param datastore:
        GMX DataStore contract.
    :param glv_address:
        GLV token address.
    :param markets:
        Current GLV-supported GM market tokens.
    :param block_identifier:
        Block at which the catalogue is read.
    :return:
        ``True`` when at least one supported market is enabled for the GLV.
    """
    return any(not datastore.functions.getBool(is_glv_market_disabled_key(glv_address, market_address)).call(block_identifier=block_identifier) for market_address in markets)


def _fetch_gm_products(
    *,
    web3: Web3,
    chain_id: int,
    datastore,
    reader,
    datastore_address: HexAddress,
    page_size: int,
    block_identifier: BlockIdentifier,
    token_cache: dict | None,
) -> Iterator[GMXVaultProduct]:
    """Yield GM market-token products from a paginated GMX Reader.

    :param web3:
        Web3 connection for the product chain.
    :param chain_id:
        Product chain ID.
    :param datastore:
        GMX DataStore contract.
    :param reader:
        GMX SyntheticsReader contract.
    :param datastore_address:
        Address accepted by the Reader.
    :param page_size:
        Positive Reader page size.
    :param block_identifier:
        Block at which the catalogue is read.
    :param token_cache:
        Optional shared token-details cache.
    :return:
        Every GM market-token product, including disabled markets.
    """

    def market_call(start: int, end: int):
        """Build one page request for the SyntheticsReader."""
        return reader.functions.getMarkets(datastore_address, start, end)

    for raw_market in _paginate_reader_call(market_call, page_size=page_size, block_identifier=block_identifier):
        market_address, index_token, long_token, short_token = (to_checksum_address(address) for address in raw_market)
        symbol, name, decimals = _fetch_token_metadata(web3, chain_id, market_address, token_cache)
        yield GMXVaultProduct(
            chain_id=chain_id,
            token_address=market_address,
            product_type="gm",
            symbol=symbol,
            name=name,
            decimals=decimals,
            component_addresses=(market_address, index_token, long_token, short_token),
            accepted_deposit_tokens=(long_token, short_token),
            is_enabled=_fetch_market_enabled(datastore, market_address, block_identifier),
        )


def _fetch_glv_products(
    *,
    web3: Web3,
    chain_id: int,
    datastore,
    glv_reader,
    datastore_address: HexAddress,
    page_size: int,
    block_identifier: BlockIdentifier,
    token_cache: dict | None,
) -> Iterator[GMXVaultProduct]:
    """Yield GLV products from a paginated GMX GlvReader.

    :param web3:
        Web3 connection for the product chain.
    :param chain_id:
        Product chain ID.
    :param datastore:
        GMX DataStore contract.
    :param glv_reader:
        GMX GlvReader contract.
    :param datastore_address:
        Address accepted by the GlvReader.
    :param page_size:
        Positive Reader page size.
    :param block_identifier:
        Block at which the catalogue is read.
    :param token_cache:
        Optional shared token-details cache.
    :return:
        Every GLV product, including products without enabled constituent markets.
    """

    def glv_call(start: int, end: int):
        """Build one page request for the GlvReader."""
        return glv_reader.functions.getGlvInfoList(datastore_address, start, end)

    for raw_glv_info in _paginate_reader_call(glv_call, page_size=page_size, block_identifier=block_identifier):
        raw_glv, raw_markets = raw_glv_info
        glv_address, long_token, short_token = (to_checksum_address(address) for address in raw_glv)
        markets = tuple(to_checksum_address(address) for address in raw_markets)
        symbol, name, decimals = _fetch_token_metadata(web3, chain_id, glv_address, token_cache)
        yield GMXVaultProduct(
            chain_id=chain_id,
            token_address=glv_address,
            product_type="glv",
            symbol=symbol,
            name=name,
            decimals=decimals,
            component_addresses=(glv_address, long_token, short_token, *markets),
            accepted_deposit_tokens=(long_token, short_token),
            is_enabled=_fetch_glv_enabled(datastore, glv_address, markets, block_identifier),
        )


def fetch_gmx_v2_vault_products(
    web3: Web3,
    *,
    page_size: int = 100,
    block_identifier: BlockIdentifier = "latest",
    token_cache: dict | None = None,
) -> Iterator[GMXVaultProduct]:
    """Enumerate GMX V2 GM and GLV share tokens from the current chain state.

    This uses the onchain Reader and GlvReader as the identity source. REST
    data is intentionally not consulted: API listing status is useful later as
    enrichment, but cannot be allowed to add or remove a vault identity.

    :param web3:
        Archive-capable Web3 connection to Arbitrum One or Avalanche.
    :param page_size:
        Positive Reader page size. The reader is paged instead of relying on
        today's market count or GLV count.
    :param block_identifier:
        Block at which all product facts are read.
    :param token_cache:
        Optional shared token-details cache.
    :return:
        GM products followed by GLV products, each retaining disabled entries.
    :raises ValueError:
        If the connected chain is not a supported GMX V2 deployment.
    """
    chain_id = web3.eth.chain_id
    try:
        chain_name = GMX_CHAIN_NAMES_BY_ID[chain_id]
    except KeyError:
        raise ValueError(f"GMX V2 vault catalogue is unsupported on chain {chain_id}") from None

    addresses = get_contract_addresses(chain_name)
    datastore = get_datastore_contract(web3, chain_name)
    reader = get_reader_contract(web3, chain_name)
    glv_reader = get_glv_reader_contract(web3, chain_name)

    logger.info("Enumerating GMX V2 GM and GLV products on %s", chain_name)
    yield from _fetch_gm_products(
        web3=web3,
        chain_id=chain_id,
        datastore=datastore,
        reader=reader,
        datastore_address=addresses.datastore,
        page_size=page_size,
        block_identifier=block_identifier,
        token_cache=token_cache,
    )
    yield from _fetch_glv_products(
        web3=web3,
        chain_id=chain_id,
        datastore=datastore,
        glv_reader=glv_reader,
        datastore_address=addresses.datastore,
        page_size=page_size,
        block_identifier=block_identifier,
        token_cache=token_cache,
    )
