"""Wildcat version 2 market state and the values derived from it."""

import dataclasses
from decimal import Decimal

import eth_abi

from eth_defi.erc_4626.vault_protocol.wildcat.constants import WILDCAT_RAY
from eth_defi.types import Percent

#: ABI type of the version 2 ``MarketState`` struct returned by ``currentState()``.
MARKET_STATE_ABI_TYPE = "(bool,uint128,uint128,uint128,uint104,uint104,uint32,bool,uint32,uint16,uint16,uint16,uint112,uint32)"


@dataclasses.dataclass(slots=True, frozen=True)
class WildcatMarketState:
    """Version 2 ``MarketState`` returned by ``currentState()``.

    Field order follows the ``MarketState`` struct, so the decoded tuple can be
    passed positionally. Amounts are raw integers in market token units, and
    ``scaled*`` amounts must be multiplied by ``scale_factor / 1e27``.

    https://github.com/wildcat-finance/v2-protocol/blob/main/src/libraries/MarketState.sol
    """

    #: Whether the borrower has closed the market.
    is_closed: bool
    #: Deposit cap in normalised units.
    max_total_supply: int
    #: Protocol fees owed by the borrower and held in the market.
    accrued_protocol_fees: int
    #: Paid withdrawals not yet claimed by lenders.
    normalized_unclaimed_withdrawals: int
    #: Total lender supply in scaled units, including pending withdrawals.
    scaled_total_supply: int
    #: Queued, unpaid withdrawals in scaled units, across all unpaid batches.
    scaled_pending_withdrawals: int
    #: Expiry of the current withdrawal batch, or zero.
    pending_withdrawal_expiry: int
    #: Delinquency flag stored at the last state update.
    is_delinquent: bool
    #: Accumulated delinquent seconds, accrued to the read block.
    time_delinquent: int
    #: Share of interest paid to the protocol by the borrower, in bips.
    protocol_fee_bips: int
    #: Base lender APR in bips.
    annual_interest_bips: int
    #: Liquidity the borrower must keep in the market, in bips.
    reserve_ratio_bips: int
    #: Ray-precision share price.
    scale_factor: int
    #: Timestamp of the last interest accrual.
    last_interest_accrued_timestamp: int

    @classmethod
    def decode(cls, data: bytes) -> "WildcatMarketState":
        """Decode raw ``currentState()`` return data.

        :param data:
            ABI-encoded ``MarketState`` struct.
        :return:
            Decoded state.
        """
        (raw,) = eth_abi.decode([MARKET_STATE_ABI_TYPE], data)
        return cls(*raw)

    def normalise(self, scaled_amount: int) -> int:
        """Convert a scaled amount to market token units.

        Uses the contract's half-up ``rayMul`` rounding.

        :param scaled_amount:
            Amount in scaled units.
        :return:
            Amount in raw market token units.
        """
        return (scaled_amount * self.scale_factor + WILDCAT_RAY // 2) // WILDCAT_RAY

    @property
    def total_supply(self) -> int:
        """Raw lender supply, equal to the market's ``totalSupply()``."""
        return self.normalise(self.scaled_total_supply)

    @property
    def share_price(self) -> Decimal:
        """Underlying assets per scaled market token."""
        return Decimal(self.scale_factor) / Decimal(WILDCAT_RAY)

    @property
    def max_deposit(self) -> int:
        """Raw remaining deposit capacity, zero for a closed market."""
        if self.is_closed:
            return 0
        return max(0, self.max_total_supply - self.total_supply)

    def calculate_lending_metrics(self, cash: int) -> tuple[int, Percent | None]:
        """Calculate available liquidity and utilisation from market cash.

        Paid-but-unclaimed withdrawals and accrued protocol fees sit in the
        market's cash but belong to withdrawn lenders and the protocol.
        Utilisation is lent-out debt over lender supply, equal to the version 1
        ``outstandingDebt() / totalSupply()``. Pending withdrawals are deducted
        only from available liquidity, because older unpaid batches would
        otherwise pin utilisation at 100%.

        :param cash:
            Raw ``totalAssets()``, the market's asset balance.
        :return:
            ``(available liquidity, utilisation)``. Liquidity is in raw market
            token units. Utilisation is ``None`` when the market has no supply.
        """
        lender_cash = max(0, cash - self.normalized_unclaimed_withdrawals - self.accrued_protocol_fees)
        available = max(0, lender_cash - self.normalise(self.scaled_pending_withdrawals))
        total_supply = self.total_supply
        if total_supply == 0:
            return available, None
        utilisation = min(1.0, max(0.0, (total_supply - lender_cash) / total_supply))
        return available, utilisation
