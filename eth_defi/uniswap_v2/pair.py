"""Uniswap v2 like pair info.

"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Union, Optional

from eth_typing import HexAddress
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.token import TokenDetails, fetch_erc20_details


@dataclass(frozen=True, slots=True)
class PairDetails:
    """Uniswap v2 trading pair info."""

    #: Pool contract
    #:
    #: https://docs.uniswap.org/contracts/v2/reference/smart-contracts/pair#getreserves
    contract: Contract

    #: One pair of tokens
    token0: TokenDetails

    #: One pair of tokens
    token1: TokenDetails

    #: Store the human readable token order on this data.
    #:
    #: If false then pair reads as token0 symbol (WETH) - token1 symbol (USDC).
    #:
    #: If true then pair reads as token1 symbol (USDC) - token0 symbol (WETH).
    reverse_token_order: Optional[bool] = None

    def __eq__(self, other):
        """Implemented for set()"""
        assert isinstance(other, Uniswap)
        return self.address == other.address

    def __hash__(self) -> int:
        """Implemented for set()"""
        return int(self.address, 16)

    def __repr__(self):
        return f"<Pair {self.get_base_token().symbol}-{self.get_quote_token().symbol} at {self.address}>"

    @property
    def address(self) -> HexAddress:
        """Get pair contract address"""
        return self.contract.address

    @property
    def checksum_free_address(self) -> str:
        """Get pair contract address, all lowercase."""
        return self.contract.address.lower()

    def get_base_token(self):
        """Get human-ordered base token."""
        assert self.reverse_token_order is not None, "Reverse token order flag must be check before this operation is possible"
        if self.reverse_token_order:
            return self.token1
        else:
            return self.token0

    def get_quote_token(self):
        """Get human-ordered quote token."""
        assert self.reverse_token_order is not None, "Reverse token order flag must be check before this operation is possible"
        if self.reverse_token_order:
            return self.token0
        else:
            return self.token1

    def convert_price_to_human(self, reserve0: int, reserve1: int, reverse_token_order=None):
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

    def get_current_mid_price(self) -> Decimal:
        """Return the price in this pool.

        Calls `getReserves()` over JSON-RPC and calculate
        the current price basede on the pair reserves.

        See https://docs.uniswap.org/contracts/v2/reference/smart-contracts/pair#getreserves

        :return:
            Quote token / base token price in human digestible form
        """
        assert self.reverse_token_order is not None, "Reverse token order must be set to get the natural price"
        reserve0, reserve1, timestamp = self.contract.functions.getReserves().call()
        return self.convert_price_to_human(reserve0, reserve1, self.reverse_token_order)


def fetch_pair_details(
    web3,
    pair_contact_address: Union[str, HexAddress],
    reverse_token_order: Optional[bool] = None,
    base_token_address: Optional[str] = None,
    quote_token_address: Optional[str] = None,
) -> PairDetails:
    """Get pair info for PancakeSwap, others.

    :param web3:
        Web3 instance

    :param pair_contact_address:
        Smart contract address of trading pair

    :param reverse_token_order:
        Set the human readable token order.

        See :py:class`PairDetails` for more info.

    :param base_token_address:
        Automatically determine token order from addresses.

    :param quote_token_address:
        Automatically determine token order from addresses.

    """

    if base_token_address or quote_token_address:
        assert reverse_token_order is None, f"Give either (base_token_address, quote_token_address) or reverse_token_order"
        reverse_token_order = int(base_token_address, 16) > int(quote_token_address, 16)

    pool = get_deployed_contract(web3, "UniswapV2Pair.json", pair_contact_address)
    token0_address = pool.functions.token0().call()
    token1_address = pool.functions.token1().call()

    token0 = fetch_erc20_details(web3, token0_address)
    token1 = fetch_erc20_details(web3, token1_address)

    return PairDetails(
        pool,
        token0,
        token1,
        reverse_token_order=reverse_token_order,
    )
