"""Uniswap v3 price impact and price calculations"""
import math
from pprint import pp

from eth_typing import HexAddress

from eth_defi.uniswap_v3.constants import DEFAULT_TICK_SPACINGS
from eth_defi.uniswap_v3.utils import (
    get_token0_amount_in_range,
    get_token1_amount_in_range,
    run_graphql_query,
    tick_to_price,
    tick_to_sqrt_price,
)


def get_pool_state_at_block(pool_address: HexAddress, block_number: int):
    """Get a pool state (current liquidity, tick, ticks) at a given block using Uniswap V3 subgraph data"""
    batch_limit = 1000

    result = run_graphql_query(
        """
        query ($pool_id: ID!, $pool: String!, $block_number: Int, $limit: Int) {
            pool(id: $pool_id, block: {number: $block_number}) {
                token0 {
                    symbol
                    decimals
                }
                token1 {
                    symbol
                    decimals
                }
                liquidity
                tick
                feeTier
            }

            ticks(
                first: $limit,
                skip: 0,
                orderBy: tickIdx,
                orderDirection: asc,
                block: {number: $block_number},
                where: {pool: $pool, liquidityNet_not: 0}
            ) {
                tickIdx
                liquidityNet
                liquidityGross
            }
        }
        """,
        variables={
            "pool_id": pool_address,
            "pool": pool_address,  # we need a separate variable since pool_id has gql type ID instead of String
            "block_number": block_number,
            "limit": batch_limit,
        },
    )

    pool = result["pool"]
    ticks = result["ticks"]

    # query more ticks if needed
    if len(ticks) == batch_limit:
        skip = batch_limit
        while True:
            result = run_graphql_query(
                """
                query ($pool: String!, $block_number: Int, $skip: Int, $limit: Int) {
                    ticks(
                        first: $limit,
                        skip: $skip,
                        orderBy: tickIdx,
                        orderDirection: asc,
                        block: {number: $block_number},
                        where: {pool: $pool, liquidityNet_not: 0}
                    ) {
                        tickIdx
                        liquidityNet
                        liquidityGross
                    }
                }
                """,
                variables={
                    "pool": pool_address,
                    "block_number": block_number,
                    "skip": skip,
                    "limit": batch_limit,
                },
            )

            if len(result["ticks"]) == 0:
                break

            ticks += result["ticks"]
            skip += batch_limit

    return {
        "liquidity": int(pool["liquidity"]),
        "tick": int(pool["tick"]),
        "fee": int(pool["feeTier"]),
        "token0": pool["token0"],
        "token1": pool["token1"],
        "ticks": ticks,
    }


def estimate_liquidity_depth(
    pool_address: HexAddress,
    block_number: int,
    *,
    depths: list[float] = [-5, -2, -1, -0.5, -0.2, -0.1, 0.1, 0.2, 0.5, 1, 2, 5],
    verbose: bool = False,
) -> list[tuple[float, float, float]]:
    """Calculate the liquidity at multiple depths of a pool at a given block

    `See this StackExchange question for commentary <https://ethereum.stackexchange.com/questions/120828/uniswap-v3-calculate-volume-to-reach-target-price>`_

    :param pool_address: Uniswap v3 pool address
    :param block_number: Block number when the liquidity should be measured
    :param depths: A list of depths in percentage where liquidity should be measured, default: 12 depth range from -5% to +%5
    :param verbose: Print out information to console if True, default: False
    :return: A list of liquidity depth in form of tuple: depth, amount of token needed to buy to reach current depth, adjusted amount of token (based on token decimals)
    """

    # get current pool state from subgraph data
    pool_state = get_pool_state_at_block(pool_address, block_number)
    current_tick = pool_state["tick"]
    current_liquidity = pool_state["liquidity"]
    sqrt_current_price = tick_to_sqrt_price(current_tick)
    cache_sqrt_current_price = tick_to_sqrt_price(current_tick)
    ticks = pool_state["ticks"]
    tick_spacing = DEFAULT_TICK_SPACINGS[pool_state["fee"]]
    current_price = tick_to_price(current_tick)
    base_token = pool_state["token0"]["symbol"]
    base_token_decimals = int(pool_state["token0"]["decimals"])
    quote_token = pool_state["token1"]["symbol"]
    quote_token_decimals = int(pool_state["token1"]["decimals"])

    # adjust based on decimals
    adjusted_current_price = current_price / 10 ** (quote_token_decimals - base_token_decimals)

    if verbose:
        print(f"Pool has {len(ticks)} nonzero ticks, current tick is {current_tick}. Current price is {adjusted_current_price} {quote_token} for 1 {base_token}")

    # get current tick range
    nearest_tick: dict = min([t for t in ticks if current_tick < int(t["tickIdx"])], key=lambda t: int(t["tickIdx"]))
    nearest_tick_index = ticks.index(nearest_tick)

    liquidity_depths = []

    for depth in depths:
        # calculate target price in certain depth
        target_price = current_price * (100 + depth) / 100
        sqrt_target_price = math.sqrt(target_price)
        sqrt_current_price = cache_sqrt_current_price

        assert sqrt_target_price != sqrt_current_price

        lower_tick_range = ticks[:nearest_tick_index]
        upper_tick_range = ticks[nearest_tick_index:]
        liquidity = current_liquidity
        delta_tokens = 0

        if verbose:
            print(f"> Start checking depth {depth}%")

        if sqrt_target_price > sqrt_current_price:
            # too much base token in the pool
            try:
                while sqrt_target_price > sqrt_current_price:
                    tick_item = upper_tick_range.pop(0)

                    tick_lower = int(tick_item["tickIdx"])
                    tick_upper = tick_lower + tick_spacing
                    sqrt_price_upper = tick_to_sqrt_price(tick_upper)
                    liquidity += int(tick_item["liquidityNet"])

                    if verbose:
                        print(f"Crossing tick range {tick_lower} {tick_upper} with liquidity {tick_item['liquidityNet']} and upper price {sqrt_price_upper**2}")

                    if sqrt_target_price > sqrt_price_upper:
                        # not in the current price range; use all X in the range
                        delta_tokens += get_token0_amount_in_range(liquidity, sqrt_current_price, sqrt_price_upper)

                        # adjust current price and continue looping to next tick range
                        sqrt_current_price = sqrt_price_upper
                    else:
                        # in the current price range
                        delta_tokens += get_token0_amount_in_range(liquidity, sqrt_current_price, sqrt_target_price)
                        sqrt_current_price = sqrt_target_price

                liquidity_depths.append((depth, delta_tokens, delta_tokens / 10**base_token_decimals))

                if verbose:
                    print(f"\tNeed to buy {delta_tokens / 10**base_token_decimals:_} {base_token} from pool to reach target price {target_price} (+{depth}%)\n")
            except IndexError:
                liquidity_depths.append((depth, None))

                if verbose:
                    print("\tNot enough liquidity to reach target price\n")

        else:
            # too much quote token in the pool
            try:
                while sqrt_target_price < sqrt_current_price:
                    tick_item = lower_tick_range.pop()

                    tick_lower = int(tick_item["tickIdx"])
                    tick_upper = tick_lower + tick_spacing
                    sqrt_price_lower = tick_to_sqrt_price(tick_lower)
                    sqrt_price_upper = tick_to_sqrt_price(tick_upper)
                    liquidity -= int(tick_item["liquidityNet"])

                    if verbose:
                        print(f"Crossing tick range {tick_lower} {tick_upper} with liquidity {tick_item['liquidityNet']} and lower price {sqrt_price_lower**2}")

                    if sqrt_target_price < sqrt_price_lower:
                        # not in the current price range; use all Y in the range
                        delta_tokens += get_token1_amount_in_range(liquidity, sqrt_current_price, sqrt_price_lower)

                        # adjust current price and continue looping to next tick range
                        sqrt_current_price = sqrt_price_lower
                    else:
                        # in the current price range
                        delta_tokens += get_token1_amount_in_range(liquidity, sqrt_current_price, sqrt_target_price)
                        sqrt_current_price = sqrt_target_price

                liquidity_depths.append((depth, delta_tokens, delta_tokens / 10**quote_token_decimals))

                if verbose:
                    print(f"\tNeed to buy {delta_tokens / 10**quote_token_decimals:_} {quote_token} tokens from pool to reach target price {target_price} ({depth}%)\n")
            except IndexError:
                liquidity_depths.append((depth, None))

                if verbose:
                    print("\tNot enough liquidity to reach target price\n")

    if verbose:
        print("> Result price depths:")
        pp(liquidity_depths)

    return liquidity_depths
