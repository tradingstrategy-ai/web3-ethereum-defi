"""
GMX protocol type definitions.

Common type aliases used throughout the GMX module for better type safety and documentation.
"""

from typing import Literal, NotRequired, TypeAlias, TypedDict

from eth_typing import HexAddress

#: Token symbol (e.g., "BTC", "ETH", "USDC")
TokenSymbol: TypeAlias = str

#: Market symbol (e.g., "BTC", "ETH/USD", "DOGE")
MarketSymbol: TypeAlias = str

#: Market address as hexadecimal string
MarketAddress: TypeAlias = str

#: USD-denominated price or value
USDAmount: TypeAlias = float

#: Annual Percentage Rate as decimal (e.g., 0.05 for 5%)
APRDecimal: TypeAlias = float

#: Position side identifier ("long" or "short")
PositionSide: TypeAlias = Literal["long", "short"]

#: Liquidity data for a specific side/market
LiquidityData: TypeAlias = dict[MarketSymbol, USDAmount]

#: Market data mapping
MarketData: TypeAlias = dict[MarketSymbol, float]

#: Position side data mapping
PositionSideData: TypeAlias = dict[PositionSide, MarketData]

#: Price data mapping
PriceData: TypeAlias = dict[TokenSymbol, USDAmount]

#: TVL data mapping
TVLData: TypeAlias = dict[TokenSymbol, USDAmount]

#: Interest data mapping
InterestData: TypeAlias = dict[TokenSymbol, APRDecimal]


class OraclePricePayload(TypedDict):
    """One token's raw signed price entry from :meth:`eth_defi.gmx.core.oracle.OraclePrices.get_recent_prices`.

    GMX's signed-price API omits ``minPriceFull``/``maxPriceFull`` for a
    token it has no live price for, rather than including them with a null
    value -- hence :class:`~typing.NotRequired` on both.
    """

    minPriceFull: NotRequired[str | int]
    maxPriceFull: NotRequired[str | int]


#: Live GMX oracle prices keyed by token address, as returned by
#: :meth:`eth_defi.gmx.core.oracle.OraclePrices.get_recent_prices`. Keys are
#: not guaranteed to arrive checksummed -- look them up case-insensitively.
OraclePriceMap: TypeAlias = dict[HexAddress, OraclePricePayload]

#: A GMX market's ``(index_token, long_token, short_token)`` address triple.
MarketTokenAddresses: TypeAlias = tuple[HexAddress, HexAddress, HexAddress]

#: A token's raw ``(min, max)`` GMX oracle price, in GMX's native fixed-point precision.
RawOraclePriceRange: TypeAlias = tuple[int, int]

#: One ``MarketUtils.MarketPrices`` tuple -- the ``(index, long, short)`` price
#: ranges GMX's ``Reader.getAccountPositionInfoList()`` takes per market.
MarketPriceTuple: TypeAlias = tuple[RawOraclePriceRange, RawOraclePriceRange, RawOraclePriceRange]
