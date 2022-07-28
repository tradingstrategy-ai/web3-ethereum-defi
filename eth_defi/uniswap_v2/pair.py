"""Uniswap v2 like pair info.

"""

from dataclasses import dataclass
from typing import Union

from eth_typing import HexAddress

from eth_defi.abi import get_deployed_contract
from eth_defi.token import TokenDetails, fetch_erc20_details


@dataclass
class PairDetails:
    """Uniswap v2 trading pair info."""

    #: Pool address
    address: HexAddress

    #: One pair of tokens
    token0: TokenDetails

    #: One pair of tokens
    token1: TokenDetails

    def convert_price_to_human(self,
                               reserve0: int,
                               reserve1: int,
                               reverse_token_order=False):
        """Convert the price obtained through Sync event

        :param reverse_token_order:
            Decide token order for human (base, quote token) order.
            If set, assume quote token is token0.
        """
        token0_amount = self.token0.convert_to_decimals(reserve0)
        token1_amount = self.token1.convert_to_decimals(reserve1)

        if reverse_token_order:
            return token0_amount / token1_amount
        else:
            return token1_amount / token0_amount


def fetch_pair_details(web3, pair_contact_address: Union[str, HexAddress]) -> PairDetails:
    """Get pair info for PancakeSwap, others.

    :param web3:
        Web3 instance

    :param pair_contact_address:
        Smart contract address of trading pair

    """
    pool = get_deployed_contract(web3, "UniswapV2Pair.json", pair_contact_address)
    token0_address = pool.functions.token0().call()
    token1_address = pool.functions.token1().call()

    token0 = fetch_erc20_details(web3, token0_address)
    token1 = fetch_erc20_details(web3, token1_address)

    return PairDetails(
        pool.address,
        token0,
        token1,
    )
