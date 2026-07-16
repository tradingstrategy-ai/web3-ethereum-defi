"""Asseto product metadata.

Asseto AoABT is a permissioned ERC-20 tokenised fund product.  Its share
token, subscription/redemption hub and NAV pricer are separate contracts, so
the shared vault scanner needs this product-level mapping instead of ERC-4626
introspection.
"""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress

from eth_defi.types import Percent


@dataclass(slots=True, frozen=True)
class AssetoProduct:
    """Asseto on-chain product metadata.

    :param chain_id:
        EVM chain hosting the product.
    :param token:
        ERC-20 tokenised fund share address.
    :param manager:
        Asseto ``AoABTManager`` request/claim contract, when published.
    :param pricer:
        Asseto ``Pricer`` contract that publishes NAV/share in base-18 USD,
        when published.
    :param collateral:
        Stablecoin used by the manager for subscriptions and redemptions, when
        the product publishes it.
    :param first_seen_at_block:
        Token proxy deployment block.
    :param first_seen_at:
        Token proxy deployment timestamp as a naive UTC datetime.
    :param management_fee:
        Documented annual underlying-fund management fee, when available.
    :param performance_fee:
        Documented underlying-fund performance fee, when available.
    :param has_custom_fees:
        Whether the fund fee terms include conditions that cannot be expressed
        as one scalar percentage.
    """

    chain_id: int
    token: HexAddress
    symbol: str
    product_name: str
    manager: HexAddress | None
    pricer: HexAddress | None
    collateral: HexAddress | None
    first_seen_at_block: int
    first_seen_at: datetime.datetime
    management_fee: Percent | None = None
    performance_fee: Percent | None = None
    has_custom_fees: bool = False
    #: Asseto public product-registry identifier for its display NAV history.
    offchain_product_id: int | None = None
    #: Product key required by Asseto's off-chain product endpoints.
    offchain_product_name: str | None = None
    #: Informational public product description.
    description: str | None = None


#: HashKey Chain EVM chain id.
HASHKEY_CHAIN_ID = 177

#: AoABT on HashKey Chain.
#:
#: Token proxy: https://hsk.blockscout.com/address/0x80C080acd48ED66a35Ae8A24BC1198672215A9bD
#: Manager: https://hsk.blockscout.com/address/0x6dB7eA55c94fb0F4b22D6b384C18CdAa3B33d746
#: Pricer: https://hsk.blockscout.com/address/0xD72529F8b54fcB59010F2141FC328aDa5Aa72abb
ASSETO_AOABT_HASHKEY = AssetoProduct(
    chain_id=HASHKEY_CHAIN_ID,
    token=HexAddress("0x80c080acd48ed66a35ae8a24bc1198672215a9bd"),
    symbol="AoABT",
    product_name="Asseto Orient Arbitrage Token",
    manager=HexAddress("0x6db7ea55c94fb0f4b22d6b384c18cdaa3b33d746"),
    pricer=HexAddress("0xd72529f8b54fcb59010f2141fc328ada5aa72abb"),
    collateral=HexAddress("0xf1b50ed67a9e2cc94ad3c477779e2d4cbfff9029"),
    first_seen_at_block=3_068_926,
    first_seen_at=datetime.datetime(2025, 2, 25, 12, 3, 7, tzinfo=datetime.UTC).replace(tzinfo=None),
    management_fee=0.01,
    performance_fee=0.20,
    has_custom_fees=True,
)

#: Product lookup used by the adapter and chain-aware classification.
ASSETO_PRODUCTS: dict[tuple[int, HexAddress], AssetoProduct] = {
    (ASSETO_AOABT_HASHKEY.chain_id, ASSETO_AOABT_HASHKEY.token): ASSETO_AOABT_HASHKEY,
}

#: Address-only lookup used for hardcoded protocol routing.
ASSETO_PRODUCTS_BY_TOKEN: dict[HexAddress, AssetoProduct] = {product.token: product for product in ASSETO_PRODUCTS.values()}

#: Hardcoded leads for tokenised funds that do not emit ERC-4626 events.
ASSETO_HARDCODED_LEADS = tuple(
    (
        product.chain_id,
        product.token,
        product.first_seen_at_block,
        product.first_seen_at,
    )
    for product in ASSETO_PRODUCTS.values()
)
