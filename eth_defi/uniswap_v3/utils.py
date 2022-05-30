"""Uniswap v3 helper functions."""
import math
from typing import Tuple

from gql import Client, gql
from gql.transport.requests import RequestsHTTPTransport

from eth_defi.uniswap_v3.constants import (
    DEFAULT_TICK_SPACINGS,
    MAX_TICK,
    MIN_TICK,
    UNISWAP_V3_SUBGRAPH_URL,
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


def tick_to_price(tick):
    """Returns price corresponding to a tick"""
    return 1.0001**tick


def tick_to_sqrt_price(tick):
    """Returns square root price corresponding to a tick"""
    return tick_to_price(tick / 2)


def get_token0_amount_in_range(liquidity, sp, sb):
    """Returns token0 (base token) amount in a liquidity range

    This is derived formula based on: https://atiselsts.github.io/pdfs/uniswap-v3-liquidity-math.pdf

    :param liquidity: current virtual liquidity
    :param sp: square root current price
    :param sb: square root upper price
    """
    return liquidity * (sb - sp) / (sp * sb)


def get_token1_amount_in_range(liquidity, sp, sa):
    """Returns token1 (quote token) amount in a liquidity range

    This is derived formula based on: https://atiselsts.github.io/pdfs/uniswap-v3-liquidity-math.pdf

    :param liquidity: current virtual liquidity
    :param sp: square root current price
    :param sb: square root lower price
    """
    return liquidity * (sp - sa)


def run_graphql_query(query: str, *, variables: dict = {}, api_url=UNISWAP_V3_SUBGRAPH_URL) -> dict:
    """Run query on Uniswap v3 subgraph"""
    transport = RequestsHTTPTransport(url=api_url, verify=True, retries=3)
    graphql_client = Client(transport=transport, fetch_schema_from_transport=True)

    return graphql_client.execute(gql(query), variable_values=variables)
