from decimal import Decimal
from dataclasses import dataclass
from typing import List, Optional

from eth_typing import HexAddress

from eth_defi.token import TokenDetails


@dataclass
class TradeResult:
    """A base class for Success/Fail trade result."""

    #: How many units of gas we burned
    gas_used: int

    #: What as the gas price used in wei.
    #: Set to `0` if not available.
    effective_gas_price: int

    def get_effective_gas_price_gwei(self) -> Decimal:
        return Decimal(self.effective_gas_price) / Decimal(10**9)

    def get_cost_of_gas(self) -> Decimal:
        """This will return the gas cost of the transaction in blockchain's native currency e.g. in ETH on Ethereum."""
        return Decimal(self.gas_used) * Decimal(self.effective_gas_price) / Decimal(10**18)


@dataclass
class TradeSuccess(TradeResult):
    """Describe the result of a successful Uniswap swap.

    See :py:func:`eth_defi.uniswap_v2.analysis.analyse_trade_by_receipt`
    """

    #: Routing path that was used for this trade
    path: List[HexAddress] | None

    amount_in: int
    amount_out_min: int | None
    amount_out: int

    #: The price of the trade in some order.
    #:
    #: - Uniswap v2: Overall price paid as in token (first in the path) to out token (last in the path).
    #:
    #: - Uniswap v3: depends on ticks and order of token0 and token1 in the underlying pool smart contract
    #:
    #: Price includes any fees paid during the order routing path.
    #:
    #: Note that you get inverse price, if you route ETH-USD or USD-ETH e.g. are you doing buy or sell.
    #:
    #: See also :py:meth:`get_human_price`
    price: Decimal

    #: Token information bookkeeping
    amount_in_decimals: int

    #: Token information bookkeeping
    amount_out_decimals: int

    #: Uniswap v3 pool token 0
    #:
    #: Needed to calculate reverse token order.
    token0: TokenDetails | None

    #: Uniswap v3 pool token 1
    #:
    #: Needed to calculate reverse token order.
    token1: TokenDetails | None

    #: How much was the LP fee
    #:
    #: Note: this is the raw amount in terms of the amount in token
    lp_fee_paid: float | None

    def __post_init__(self):
        if self.price is not None:
            assert isinstance(self.price, Decimal)

    def get_human_price(self, reverse_token_order=False) -> Decimal:
        """Get the executed price of this trade in a human-readable form.

        This depends on:

        - If we are on Uniswap v2 or v3

        - If we do buy or sell

        - If quote token is token0 or token1 in Uniswap v3 pool

        Example:

        .. code-block:: python

            # TODO
            pass

        :param reverse_token_order:
            Base and quote token order.

            Quote token should be natural quote token  like USD or ETH based token of the trade.
            If `reverse_token_order` is set quote token is `token0` of the pool,
            otherwise `token1`.
        """
        if reverse_token_order:
            return Decimal(1) / self.price
        else:
            return self.price


@dataclass
class TradeFail(TradeResult):
    """Describe the result of a failed Uniswap swap.

    The transaction reverted for a reason or another.
    """

    #: Revert reason if we managed to extract one
    revert_reason: Optional[str] = None
