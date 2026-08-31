"""Reviewed Ethereum YieldBasis deployments.

Addresses and launch blocks are taken from the official
`YieldBasis yb-core source and deployment record <https://github.com/yield-basis/yb-core/tree/5082fa6c31c1ec3168a9d56f04131bf1716bd6a4>`__
and the ``MarketParameters`` events emitted by the Factory. The Factory remains
the runtime source of component addresses; this registry is a review allow-list,
not a substitute for validating the live tuple.
"""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress
from eth_utils import to_checksum_address

#: Ethereum YieldBasis Factory.
YIELD_BASIS_FACTORY: HexAddress = to_checksum_address("0x370a449FeBb9411c95bf897021377fe0B7D100c0")

#: Ethereum crvUSD used as the stable side of every reviewed market.
YIELD_BASIS_STABLECOIN: HexAddress = to_checksum_address("0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E")


@dataclass(frozen=True, slots=True)
class YieldBasisMarketReview:
    """Reviewed identity of one YieldBasis Earn market.

    The immutable record gates automatic publication. Runtime component
    addresses still come from the Factory pre-scan.
    """

    #: Append-only Factory market ID.
    market_id: int
    #: LT/yb-LP share token.
    lt_address: HexAddress
    #: Volatile collateral token.
    asset_address: HexAddress
    #: First reviewed Factory event block.
    first_seen_at_block: int
    #: First reviewed Factory event timestamp.
    first_seen_at: datetime.datetime
    #: Human-readable underlying symbol.
    asset_symbol: str
    #: ERC-20 decimal precision of the underlying asset.
    asset_decimals: int


#: The four active LT markets covered by this integration.
YIELD_BASIS_ACTIVE_MARKETS: dict[int, YieldBasisMarketReview] = {
    7: YieldBasisMarketReview(
        7,
        to_checksum_address("0x651D4b8168488FA163D85304662E8278d4c55BAa"),
        to_checksum_address("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"),
        25_170_799,
        datetime.datetime(2026, 5, 25, 7, 23, 23, tzinfo=datetime.UTC).replace(tzinfo=None),
        "WBTC",
        8,
    ),
    8: YieldBasisMarketReview(
        8,
        to_checksum_address("0x722FC3640BA007C3E9867CCdB0dCa59F2e2F29F9"),
        to_checksum_address("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"),
        25_170_801,
        datetime.datetime(2026, 5, 25, 7, 23, 47, tzinfo=datetime.UTC).replace(tzinfo=None),
        "cbBTC",
        8,
    ),
    9: YieldBasisMarketReview(
        9,
        to_checksum_address("0x771F7290428d830ECd41E980745c327e507823Ec"),
        to_checksum_address("0x18084fbA666a33d37592fA2633fD49a74DD93a88"),
        25_170_802,
        datetime.datetime(2026, 5, 25, 7, 23, 59, tzinfo=datetime.UTC).replace(tzinfo=None),
        "tBTC",
        18,
    ),
    10: YieldBasisMarketReview(
        10,
        to_checksum_address("0x2B9c9f3BdcEb5d8E36a4704F08a78Fca53343cEa"),
        to_checksum_address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083c756Cc2"),
        25_170_804,
        datetime.datetime(2026, 5, 25, 7, 24, 23, tzinfo=datetime.UTC).replace(tzinfo=None),
        "WETH",
        18,
    ),
}

#: Lowercase LT address to reviewed market ID.
YIELD_BASIS_MARKET_ID_BY_LT: dict[str, int] = {review.lt_address.lower(): market_id for market_id, review in YIELD_BASIS_ACTIVE_MARKETS.items()}
