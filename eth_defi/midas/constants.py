"""Midas product metadata.

Midas products are ERC-20 mTokens with separate issuance, redemption, oracle,
and datafeed contracts. They are not ERC-4626 vault contracts, so the shared
vault integration needs product-level metadata to connect the token to its NAV
feed and operational vault contracts.
"""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress

from eth_defi.midas.registry import MidasRegistryProduct, iter_midas_registry_products


@dataclass(slots=True, frozen=True)
class MidasProduct:
    """Midas on-chain product metadata."""

    #: EVM chain id.
    chain_id: int

    #: ERC-20 mToken address. This is the share token and primary vault id.
    token: HexAddress

    #: mToken symbol.
    symbol: str

    #: Human-readable product name.
    product_name: str

    #: Midas ``IDataFeed`` contract exposing ``getDataInBase18()``.
    data_feed: HexAddress

    #: Chainlink-compatible public oracle contract for the same NAV feed.
    oracle: HexAddress | None

    #: Midas issuance vault contract.
    issuance_vault: HexAddress | None

    #: Midas redemption vault contract.
    redemption_vault: HexAddress | None

    #: First block where the mToken bytecode exists.
    first_seen_at_block: int

    #: Timestamp of :py:attr:`first_seen_at_block` as naive UTC datetime.
    first_seen_at: datetime.datetime

    #: Human-readable NAV denomination. The initial integration supports USD products.
    denomination: str = "USD"

    #: Whether this particular product is a regulated tokenised fund.
    #:
    #: Midas also issues crypto-strategy products, so this must remain a
    #: product-level decision instead of a property of the shared adapter.
    is_tokenised_fund: bool = False


def _optional_hex_address(address: str | None) -> HexAddress | None:
    """Convert an optional registry address to a lower-case hex address.

    :param address:
        Address from the Pythonised Midas registry.
    :return:
        Lower-case hex address or ``None``.
    """

    if address is None:
        return None

    return HexAddress(address.lower())


def create_midas_product_from_registry(product: MidasRegistryProduct) -> MidasProduct:
    """Create adapter metadata from a registry product row.

    The shared vault scanner only needs the mToken, Midas datafeed and
    deployment metadata to scan historical share price and TVL. Operational
    issuance/redemption contracts are kept as optional diagnostics because some
    registry products use specialised vault variants or omit public vault
    contracts.

    :param product:
        Registry product promoted by
        :py:meth:`eth_defi.midas.registry.MidasRegistryProduct.has_required_adapter_data`.
    :return:
        Midas adapter product metadata.
    """

    assert product.token is not None
    assert product.data_feed is not None
    assert product.first_seen_at_block is not None
    assert product.first_seen_at is not None

    return MidasProduct(
        chain_id=product.chain_id,
        token=HexAddress(product.token.lower()),
        symbol=product.symbol,
        product_name=f"Midas {product.symbol}",
        data_feed=HexAddress(product.data_feed.lower()),
        oracle=_optional_hex_address(product.custom_feed),
        issuance_vault=_optional_hex_address(product.deposit_vault),
        redemption_vault=_optional_hex_address(product.redemption_vault),
        first_seen_at_block=product.first_seen_at_block,
        first_seen_at=product.first_seen_at,
        is_tokenised_fund=product.symbol == "mTBILL",
    )


#: All registry products supported by the Midas vault scanner adapter.
MIDAS_PRODUCTS: dict[tuple[int, HexAddress], MidasProduct] = {
    (product.chain_id, product.token): product
    for product in (
        create_midas_product_from_registry(registry_product)
        for registry_product in iter_midas_registry_products(
            require_historical_contracts=True,
            require_adapter_data=True,
        )
    )
}

#: mTBILL on Ethereum.
MIDAS_MTBILL_ETHEREUM = MIDAS_PRODUCTS[1, HexAddress("0xdd629e5241cbc5919847783e6c96b2de4754e438")]

#: mBASIS on Ethereum.
MIDAS_MBASIS_ETHEREUM = MIDAS_PRODUCTS[1, HexAddress("0x2a8c22e3b10036f3aef5875d04f8441d4188b656")]

#: Lookup by token address for single-chain call sites.
MIDAS_PRODUCTS_BY_TOKEN: dict[HexAddress, MidasProduct] = {product.token: product for product in MIDAS_PRODUCTS.values()}

#: Hardcoded lead data for the shared vault discovery scanner.
MIDAS_HARDCODED_LEADS = tuple(
    (
        product.chain_id,
        product.token,
        product.first_seen_at_block,
        product.first_seen_at,
    )
    for product in MIDAS_PRODUCTS.values()
)
