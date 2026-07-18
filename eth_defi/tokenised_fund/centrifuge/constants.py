"""Reviewed Centrifuge permissioned tranche-token products.

The token registry is deliberately separate from Centrifuge ``LiquidityPool``
vault support. A ``Tranche`` is a transferable share token and its linked
ERC-7540 vault, when one exists for an asset, owns subscriptions and
redemptions.
"""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class CentrifugeTrancheProduct:
    """Reviewed Centrifuge tranche-share token metadata.

    :param chain_id:
        EVM chain hosting the Tranche token.
    :param token:
        Direct ``Tranche`` token address.
    :param first_seen_at_block:
        Token deployment block used for targeted discovery.
    :param first_seen_at:
        Token deployment time as a naive UTC datetime.
    """

    chain_id: int
    token: HexAddress
    symbol: str
    product_name: str
    manager_name: str
    curator_slug: str
    homepage: str
    first_seen_at_block: int
    first_seen_at: datetime.datetime


#: Ethereum mainnet chain id.
ETHEREUM_CHAIN_ID = 1

#: Janus Henderson Anemoy Treasury Fund shares.
#:
#: Direct ``Tranche`` deployment. Sourcify exact-match deployment record:
#: https://sourcify.dev/server/v2/contract/1/0x8c213ee79581ff4984583c6a801e5263418c4b86
JTRSY_ETHEREUM = CentrifugeTrancheProduct(
    chain_id=ETHEREUM_CHAIN_ID,
    token=HexAddress("0x8c213ee79581ff4984583c6a801e5263418c4b86"),
    symbol="JTRSY",
    # The direct token's on-chain ``name()`` omits the Anemoy platform brand.
    product_name="Janus Henderson Treasury Fund",
    manager_name="Janus Henderson",
    curator_slug="janus-henderson-anemoy",
    homepage="https://www.anemoy.io/funds/jtrsy",
    first_seen_at_block=20_460_672,
    first_seen_at=datetime.datetime(2024, 8, 5, 6, 47, 59, tzinfo=datetime.UTC).replace(tzinfo=None),
)

#: Product lookup for chain-aware direct-token classification.
CENTRIFUGE_TRANCHE_PRODUCTS: dict[tuple[int, HexAddress], CentrifugeTrancheProduct] = {
    (JTRSY_ETHEREUM.chain_id, JTRSY_ETHEREUM.token): JTRSY_ETHEREUM,
}

#: Address-only lookup used after a chain-aware match has been established.
CENTRIFUGE_TRANCHE_PRODUCTS_BY_TOKEN: dict[HexAddress, CentrifugeTrancheProduct] = {product.token: product for product in CENTRIFUGE_TRANCHE_PRODUCTS.values()}

#: Direct token leads for products which do not emit ERC-4626 flow events.
CENTRIFUGE_TRANCHE_HARDCODED_LEADS = tuple((product.chain_id, product.token, product.first_seen_at_block, product.first_seen_at) for product in CENTRIFUGE_TRANCHE_PRODUCTS.values())
