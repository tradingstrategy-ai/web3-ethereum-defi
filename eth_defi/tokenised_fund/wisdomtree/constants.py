"""Reviewed WisdomTree tokenised-fund product registry."""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress

from eth_defi.types import Percent


@dataclass(slots=True, frozen=True)
class WisdomTreeProduct:
    """Static facts for a WisdomTree tokenised fund deployment."""

    chain_id: int
    token: HexAddress
    symbol: str
    product_name: str
    homepage: str
    first_seen_at_block: int
    first_seen_at: datetime.datetime
    expense_ratio: Percent | None


#: Ethereum mainnet chain id.
ETHEREUM_CHAIN_ID = 1

#: WisdomTree Treasury Money Market Digital Fund token on Ethereum.
#:
#: Official address list: https://www.wisdomtreeconnect.com/digital-funds/money-market/wtgxx
#: The first code block was located with archive-node ``eth_getCode`` on
#: 2026-07-17. It is deliberately the token-proxy deployment block, not the
#: off-chain fund inception date.
WTGXX_ETHEREUM = WisdomTreeProduct(
    chain_id=ETHEREUM_CHAIN_ID,
    token=HexAddress("0x1fecf3d9d4fee7f2c02917a66028a48c6706c179"),
    symbol="WTGXX",
    product_name="WisdomTree Treasury Money Market Digital Fund",
    homepage="https://www.wisdomtreeconnect.com/digital-funds/money-market/wtgxx",
    first_seen_at_block=20_750_716,
    first_seen_at=datetime.datetime(2024, 9, 15, 1, 51, 27, tzinfo=datetime.UTC).replace(tzinfo=None),
    expense_ratio=0.0025,
)

WISDOMTREE_PRODUCTS: dict[tuple[int, HexAddress], WisdomTreeProduct] = {
    (WTGXX_ETHEREUM.chain_id, WTGXX_ETHEREUM.token): WTGXX_ETHEREUM,
}

WISDOMTREE_PRODUCTS_BY_TOKEN: dict[HexAddress, WisdomTreeProduct] = {product.token: product for product in WISDOMTREE_PRODUCTS.values()}

#: Leads for contracts which do not emit ERC-4626 discovery events.
WISDOMTREE_HARDCODED_LEADS = tuple((product.chain_id, product.token, product.first_seen_at_block, product.first_seen_at) for product in WISDOMTREE_PRODUCTS.values())
