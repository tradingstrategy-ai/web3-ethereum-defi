"""Liquidity measuring."""
from dataclasses import dataclass
from typing import Union

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract


class UnmatchedToken(Exception):
    """Token was not in the pool."""


@dataclass
class LiquidityResult:
    """Sampled liquidity on Uniswap v2 pool.

    Reserves are returned in raw token amounts.
    """

    #: Direct Contract proxy to the pair contract
    pair_contract: Contract

    #: Side a
    token0: HexAddress

    #: Side b
    token1: HexAddress

    #: Liquidity a
    token0_reserve: int

    #: Liquidity b
    token1_reserve: int

    #: When this sample was token
    block_number: int

    def get_liquidity_for_token(self, token_address: Union[HexAddress, str]) -> int:
        """Get liquidity value of a given pair token.

        Because Uniswap liquidity tuple can be either order.

        :raise: UnmatchedToken
        """

        token_address = Web3.toChecksumAddress(token_address)

        if token_address == self.token0:
            return self.token0_reserve
        elif token_address == self.token1:
            return self.token1_reserve
        else:
            raise UnmatchedToken(f"Unknown pair token {token_address}, we have {self.token0} and {self.token1}")


def get_liquidity(web3: Web3, pair_address: Union[HexAddress, str]) -> LiquidityResult:
    """Measure Uniswap v2 pool liquidity.

    :return: The current liquidity in the pool as (token0 liquidity, token1 liquidity) tuple.

    Example:

    .. code-block:: python

        liquidity_result = get_liquidity(web3, pair_address)

        assert liquidity_result.token0 == weth.address
        assert liquidity_result.token1 == usdc.address

        assert liquidity_result.get_liquidity_for_token(weth.address) == 10 * 10**18
        assert liquidity_result.block_number > 0

    :param web3: Web3 connection
    :param pair_address: Uniswap v2 pair contract address
    """

    pair = get_deployed_contract(web3, "UniswapV2Pair.json", pair_address)

    token0 = pair.functions.token0().call()
    assert token0 != "0x0000000000000000000000000000000000000000", "Invalid pair, token0 zero address"

    token1 = pair.functions.token1().call()
    assert token1 != "0x0000000000000000000000000000000000000000", "Invalid pair, token1 zero address"

    reserve_result = pair.functions.getReserves().call()

    return LiquidityResult(
        pair,
        token0,
        token1,
        reserve_result[0],
        reserve_result[1],
        reserve_result[2],
    )
