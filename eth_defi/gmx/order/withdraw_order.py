"""
GMX Withdraw Order Implementation

Specialised class for removing liquidity from GMX markets.
Extends base Withdraw class to provide a convenient interface for withdrawal operations.
"""

from typing import Optional

from eth_utils import to_checksum_address
from eth_typing import ChecksumAddress

from eth_defi.gmx.liquidity_base.withdraw import Withdraw, WithdrawParams, WithdrawResult


class WithdrawOrder(Withdraw):
    """GMX Withdraw Order class for removing liquidity from markets.

    Provides a convenient interface for withdrawal operations, extending the base
    Withdraw class with position-specific initialisation.

    Example:
        TODO: add example usage
    """

    def __init__(
        self,
        config,
        market_key: ChecksumAddress,
        out_token: ChecksumAddress,
    ):
        """Initialise withdrawal order with market and output token details.

        :param config: GMX configuration
        :type config: GMXConfig
        :param market_key: Market contract address (hex)
        :type market_key: ChecksumAddress
        :param out_token: Desired output token address (hex) - must be market's long or short token
        :type out_token: ChecksumAddress
        """
        super().__init__(config)

        self.market_key = to_checksum_address(market_key)
        self.out_token = to_checksum_address(out_token)

        self.logger.debug(
            f"Initialized withdraw order for market {self.market_key}, out_token: {self.out_token}"
        )

    def create_withdraw_order(
        self,
        gm_amount: int,
        execution_buffer: float = 1.3,
        max_fee_per_gas: Optional[int] = None,
    ) -> WithdrawResult:
        """Create a withdrawal order transaction.

        Creates an unsigned transaction for removing liquidity from a GMX market.
        The transaction needs to be signed and sent by the user.

        :param gm_amount: Amount of GM tokens to burn (in wei)
        :type gm_amount: int
        :param execution_buffer: Multiplier for execution fee (default 1.3 = 30% buffer)
        :type execution_buffer: float
        :param max_fee_per_gas: Optional gas price override in wei
        :type max_fee_per_gas: Optional[int]
        :return: WithdrawResult containing unsigned transaction and execution details
        :rtype: WithdrawResult
        :raises ValueError: If parameters are invalid or market doesn't exist
        """
        params = WithdrawParams(
            market_key=self.market_key,
            gm_amount=gm_amount,
            out_token=self.out_token,
            execution_buffer=execution_buffer,
            max_fee_per_gas=max_fee_per_gas,
        )

        self.logger.debug(
            f"Creating withdraw order: gm_amount={gm_amount}",
        )

        return self.create_withdrawal(params)
