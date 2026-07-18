"""Reviewed Theo iToken deployments."""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class TheoITokenProduct:
    """A reviewed Theo multi-asset iToken product.

    :param chain_id: EVM chain hosting the canonical iToken.
    :param token: iToken ERC-20 share address.
    :param first_seen_at_block: First Ethereum block containing proxy bytecode.
    :param first_seen_at: Deployment time as a naive UTC datetime.
    """

    chain_id: int
    token: HexAddress
    symbol: str
    product_name: str
    homepage: str
    first_seen_at_block: int
    first_seen_at: datetime.datetime


#: Ethereum mainnet chain id.
ETHEREUM_CHAIN_ID = 1

#: Theo Short Duration US Treasury Fund iToken proxy on Ethereum.
#:
#: Official deployment registry:
#: https://docs.theo.xyz/technical-reference/deployments
THBILL_ETHEREUM = TheoITokenProduct(
    chain_id=ETHEREUM_CHAIN_ID,
    token=HexAddress("0x5fa487bca6158c64046b2813623e20755091da0b"),
    symbol="thBILL",
    product_name="Theo Short Duration US Treasury Fund",
    homepage="https://docs.theo.xyz/thbill",
    first_seen_at_block=22_976_986,
    first_seen_at=datetime.datetime(2025, 7, 22, 20, 5, 35, tzinfo=datetime.UTC).replace(tzinfo=None),
)

#: Chain-aware product registry. Theo's OFT representations are deliberately
#: excluded: they are bridges of this canonical fund token, not fund products.
THEO_ITOKEN_PRODUCTS: dict[tuple[int, HexAddress], TheoITokenProduct] = {
    (THBILL_ETHEREUM.chain_id, THBILL_ETHEREUM.token): THBILL_ETHEREUM,
}

#: Address lookup used only after a chain-aware check.
THEO_ITOKEN_PRODUCTS_BY_TOKEN: dict[HexAddress, TheoITokenProduct] = {product.token: product for product in THEO_ITOKEN_PRODUCTS.values()}

#: Hardcoded lead for a product without canonical ERC-4626 deposit events.
THEO_ITOKEN_HARDCODED_LEADS = tuple((product.chain_id, product.token, product.first_seen_at_block, product.first_seen_at) for product in THEO_ITOKEN_PRODUCTS.values())
