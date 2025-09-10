"""
GMX protocol type definitions.

Common type aliases used throughout the GMX module for better type safety and documentation.
"""

from typing import TypeAlias, Literal

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
