"""Uniswap v3 pool data."""
from dataclasses import dataclass
from typing import Union

from eth_typing import HexAddress

from eth_defi.abi import get_contract, get_deployed_contract
from eth_defi.token import TokenDetails, fetch_erc20_details


@dataclass
class PoolDetails:

    #: Pool address
    address: HexAddress

    #: One pair of tokens
    token0: TokenDetails

    #: One pair of tokens
    token1: TokenDetails

    #: Pool fee in BPS
    raw_fee: int

    #: Pool fee as % multiplier, 1 = 100%
    fee: float


def fetch_pool_details(web3, pool_contact_address: Union[str, HexAddress]) -> PoolDetails:
    pool = get_deployed_contract(web3, "uniswap_v3/UniswapV3Pool.json", pool_contact_address)
    token0_address = pool.functions.token0().call()
    token1_address = pool.functions.token1().call()

    token0 = fetch_erc20_details(web3, token0_address)
    token1 = fetch_erc20_details(web3, token1_address)

    raw_fee = pool.functions.fee().call()

    return PoolDetails(
        pool.address,
        token0,
        token1,
        raw_fee,
        raw_fee / 1_000_000,
    )


