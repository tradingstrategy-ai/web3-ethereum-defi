"""Franklin Templeton Benji Ethereum product registry.

The Benji share tokens are permissioned fund interests.  The registry is
deliberately Ethereum-only: Stellar BENJI and other chain deployments use
distinct asset identifiers and must not be inferred from these addresses.
"""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class FranklinProduct:
    """One reviewed Franklin Templeton Benji fund-token deployment.

    :param chain_id:
        EVM chain hosting the fund-share token.
    :param token:
        Fund-token proxy address.
    :param symbol:
        ERC-20 share-token symbol.
    :param product_name:
        Human-readable fund name.
    :param short_description:
        Compact public fund description.
    :param description:
        Longer public fund description.
    :param first_seen_at_block:
        Proxy creation block from Blockscout.
    :param first_seen_at:
        Proxy creation timestamp as a naive UTC datetime.
    """

    chain_id: int
    token: HexAddress
    symbol: str
    product_name: str
    short_description: str
    description: str
    first_seen_at_block: int
    first_seen_at: datetime.datetime


#: Ethereum mainnet chain id.
ETHEREUM_CHAIN_ID = 1

#: Franklin OnChain Institutional Liquidity Fund Ltd. (iBENJI) on Ethereum.
#:
#: Source: https://digitalassets.franklintempleton.com/benji/benji-contracts/
IBENJI_ETHEREUM = FranklinProduct(
    chain_id=ETHEREUM_CHAIN_ID,
    token=HexAddress("0x90276e9d4a023b5229e0c2e9d4b2a83fe3a2b48c"),
    symbol="iBENJI",
    product_name="Franklin OnChain Institutional Liquidity Fund Ltd.",
    short_description="Tokenised institutional liquidity fund share",
    description="Permissioned tokenised shares in Franklin Templeton's institutional liquidity fund.",
    first_seen_at_block=22_118_491,
    first_seen_at=datetime.datetime(2025, 3, 24, 18, 15, 35, tzinfo=datetime.UTC).replace(tzinfo=None),
)

#: Franklin OnChain U.S. Government Money Fund (BENJI) on Ethereum.
#:
#: Source: https://digitalassets.franklintempleton.com/benji/benji-contracts/
BENJI_ETHEREUM = FranklinProduct(
    chain_id=ETHEREUM_CHAIN_ID,
    token=HexAddress("0x3ddc84940ab509c11b20b76b466933f40b750dc9"),
    symbol="BENJI",
    product_name="Franklin OnChain U.S. Government Money Fund",
    short_description="Tokenised U.S. government money fund share",
    description="Permissioned tokenised shares in Franklin Templeton's U.S. government money fund.",
    first_seen_at_block=20_587_120,
    first_seen_at=datetime.datetime(2024, 8, 22, 22, 25, 47, tzinfo=datetime.UTC).replace(tzinfo=None),
)

#: Product lookup used by the adapter and chain-aware classification.
FRANKLIN_PRODUCTS: dict[tuple[int, HexAddress], FranklinProduct] = {(product.chain_id, product.token): product for product in (IBENJI_ETHEREUM, BENJI_ETHEREUM)}

#: Address-only lookup for hardcoded routing after chain verification.
FRANKLIN_PRODUCTS_BY_TOKEN: dict[HexAddress, FranklinProduct] = {product.token: product for product in FRANKLIN_PRODUCTS.values()}

#: Leads for share tokens that do not emit ERC-4626 discovery events.
FRANKLIN_HARDCODED_LEADS = tuple((product.chain_id, product.token, product.first_seen_at_block, product.first_seen_at) for product in FRANKLIN_PRODUCTS.values())
