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


# https://docs.1delta.io/contract-addresses/aave-v3.html
ONE_DELTA_DEPLOYMENTS = {
    "polygon": {
        "broker_proxy": "0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
        "quoter": "0x36de3876ad1ef477e8f6d98EE9a162926f00463A",
    },
}
