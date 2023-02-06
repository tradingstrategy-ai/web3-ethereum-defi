from decimal import Decimal
from dataclasses import dataclass
from typing import List, Optional

from eth_typing import HexAddress


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


@dataclass
class TradeSuccess(TradeResult):
    """Describe the result of a successful Uniswap swap."""

    #: Routing path that was used for this trade
    path: List[HexAddress]

    amount_in: int
    amount_out_min: int
    amount_out: int

    #: Overall price paid as in token (first in the path) to out token (last in the path).
    #: Price includes any fees paid during the order routing path.
    #: Note that you get inverse price, if you route ETH-USD or USD-ETH e.g. are you doing buy or sell.
    price: Decimal

    # Token information book keeping
    amount_in_decimals: int
    amount_out_decimals: int


@dataclass
class TradeFail(TradeResult):
    """Describe the result of a failed Uniswap swap.

    The transaction reverted for a reason or another.
    """

    #: Revert reason if we managed to extract one
    revert_reason: Optional[str] = None
