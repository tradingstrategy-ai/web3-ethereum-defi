"""1delta constants."""

from enum import Enum, IntEnum


class Exchange(IntEnum):
    UNISWAP_V3 = 0
    QUICKSWAP_V3 = 1
    SUSHISWAP_V3 = 2
    QUICKSWAP_V2 = 50
    SUSHISWAP_V2 = 51


class TradeOperation(str, Enum):
    OPEN = "open"
    TRIM = "trim"
    DEBT = "debt"
    COLLATERAL = "collateral"
    CLOSE = "close"


class TradeType(IntEnum):
    EXACT_INPUT = 0
    EXACT_OUTPUT = 1
