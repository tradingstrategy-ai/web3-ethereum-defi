"""Midas product metadata.

Midas products are ERC-20 mTokens with separate issuance, redemption, oracle,
and datafeed contracts. They are not ERC-4626 vault contracts, so the shared
vault integration needs product-level metadata to connect the token to its NAV
feed and operational vault contracts.
"""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress


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
    oracle: HexAddress

    #: Midas issuance vault contract.
    issuance_vault: HexAddress

    #: Midas redemption vault contract.
    redemption_vault: HexAddress

    #: First block where the mToken bytecode exists.
    first_seen_at_block: int

    #: Timestamp of :py:attr:`first_seen_at_block` as naive UTC datetime.
    first_seen_at: datetime.datetime

    #: Human-readable NAV denomination. The initial integration supports USD products.
    denomination: str = "USD"


#: mTBILL on Ethereum.
MIDAS_MTBILL_ETHEREUM = MidasProduct(
    chain_id=1,
    token=HexAddress("0xdd629e5241cbc5919847783e6c96b2de4754e438"),
    symbol="mTBILL",
    product_name="Midas US Treasury Bill Token",
    data_feed=HexAddress("0xfcee9754e8c375e145303b7ce7beca3201734a2b"),
    oracle=HexAddress("0x056339c044055819e8db84e71f5f2e1f536b2e5b"),
    issuance_vault=HexAddress("0x99361435420711723af805f08187c9e6bf796683"),
    redemption_vault=HexAddress("0xf6e51d24f4793ac5e71e0502213a9bbe3a6d4517"),
    first_seen_at_block=18_691_255,
    first_seen_at=datetime.datetime(2023, 12, 1, 11, 25, 59, tzinfo=datetime.UTC).replace(tzinfo=None),
)

#: mBASIS on Ethereum.
MIDAS_MBASIS_ETHEREUM = MidasProduct(
    chain_id=1,
    token=HexAddress("0x2a8c22e3b10036f3aef5875d04f8441d4188b656"),
    symbol="mBASIS",
    product_name="Midas Basis Trading Token",
    data_feed=HexAddress("0x1615cbc603192ae8a9ff20e98dd0e40a405d76e4"),
    oracle=HexAddress("0xe4f2ae539442e1d3fb40f03ceebf4a372a390d24"),
    issuance_vault=HexAddress("0xa8a5c4ff4c86a459ebbdc39c5be77833b3a15d88"),
    redemption_vault=HexAddress("0x19ab19e61a930bc5c7b75bf06cdd954218ca9f0b"),
    first_seen_at_block=20_068_373,
    first_seen_at=datetime.datetime(2024, 6, 11, 11, 46, 11, tzinfo=datetime.UTC).replace(tzinfo=None),
)


#: Initial set of supported Midas products.
MIDAS_PRODUCTS: dict[tuple[int, HexAddress], MidasProduct] = {
    (MIDAS_MTBILL_ETHEREUM.chain_id, MIDAS_MTBILL_ETHEREUM.token): MIDAS_MTBILL_ETHEREUM,
    (MIDAS_MBASIS_ETHEREUM.chain_id, MIDAS_MBASIS_ETHEREUM.token): MIDAS_MBASIS_ETHEREUM,
}

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
