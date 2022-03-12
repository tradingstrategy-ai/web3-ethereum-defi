"""Uniswap v3 helper functions."""
import math
from typing import Tuple

from eth_hentai.uniswap_v3.constants import (
    DEFAULT_FEES,
    DEFAULT_TICK_SPACINGS,
    MAX_TICK,
    MIN_TICK,
)


def encode_sqrt_ratio_x96(*, amount0: int, amount1: int) -> int:
    """Returns the sqrt ratio as a Q64.96 corresponding to a given ratio of amount1 and amount0

    :param int amount0: the denominator amount, i.e amount of token0
    :param int amount1: the numerator amount, i.e. amount of token1
    :return: the sqrt ratio

    `Lifted from StakeWise Oracle (AGPL license) <https://github.com/stakewise/oracle/blob/master/oracle/oracle/distributor/uniswap_v3.py#L547>`__.
    """
    numerator: int = amount1 << 192
    denominator: int = amount0
    ratio_x192: int = numerator // denominator
    return int(math.sqrt(ratio_x192))


def get_min_tick(fee: int) -> int:
    """Returns min tick for given fee.

    Adapted from https://github.com/Uniswap/v3-periphery/blob/v1.0.0/test/shared/ticks.ts
    """
    tick_spacing: int = DEFAULT_TICK_SPACINGS[fee]
    return math.ceil(MIN_TICK / tick_spacing) * tick_spacing


def get_max_tick(fee: int) -> int:
    """Returns max tick for given fee.

    Adapted from https://github.com/Uniswap/v3-periphery/blob/v1.0.0/test/shared/ticks.ts
    """
    tick_spacing: int = DEFAULT_TICK_SPACINGS[fee]
    return math.floor(MAX_TICK / tick_spacing) * tick_spacing


def get_default_tick_range(fee: int) -> Tuple[int, int]:
    """Returns min and max tick for a given fee, this is used by default if the pool
    owner doesn't want to apply concentrated liquidity initially.
    """
    min_tick = get_min_tick(fee)
    max_tick = get_max_tick(fee)

    return min_tick, max_tick
