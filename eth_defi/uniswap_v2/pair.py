"""Uniswap v2 like pair info.

"""

from dataclasses import dataclass
from typing import Union, Optional

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

    #: Store the human readable token order on this data.
    #:
    #: If false then pair reads as token0 symbol - token1 symbol.
    #:
    #: If true then pair reads as token1 symbol - token0 symbol.
    reverse_token_order: Optional[bool] = None

    def get_base_token(self):
        """Get human-ordered base token."""
        assert self.reverse_token_order is None, "Reverse token order flag must be check before this operation is possible"
        if self.reverse_token_order:
            return self.token1
        else:
            return self.token0

    def get_quote_token(self):
        """Get human-ordered quote token."""
        assert self.reverse_token_order is None, "Reverse token order flag must be check before this operation is possible"
        if self.reverse_token_order:
            return self.token0
        else:
            return self.token1

    def convert_price_to_human(self,
                               reserve0: int,
                               reserve1: int,
                               reverse_token_order=None):
        """Convert the price obtained through Sync event

        :param reverse_token_order:
            Decide token order for human (base, quote token) order.
            If set, assume quote token is token0.

            IF set to None, use value from the data.

        """

        if reverse_token_order is None:
            reverse_token_order = self.reverse_token_order

        if reverse_token_order is None:
            reverse_token_order = False

        token0_amount = self.token0.convert_to_decimals(reserve0)
        token1_amount = self.token1.convert_to_decimals(reserve1)

        if reverse_token_order:
            return token0_amount / token1_amount
        else:
            return token1_amount / token0_amount



def fetch_pair_details(web3, pair_contact_address: Union[str, HexAddress], reverse_token_order: Optional[bool]=None) -> PairDetails:
    """Get pair info for PancakeSwap, others.

    :param web3:
        Web3 instance

    :param pair_contact_address:
        Smart contract address of trading pair

    :param reverse_token_order:
        Set the human readable token order.

        See :py:class`PairDetails` for more info.
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
