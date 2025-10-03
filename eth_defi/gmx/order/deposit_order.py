"""
GMX Deposit Order Implementation

Specialised class for adding liquidity to GMX markets.
Extends base Deposit class to provide a convenient interface for deposit operations.
"""

from typing import Optional

from eth_utils import to_checksum_address
from eth_typing import ChecksumAddress

from eth_defi.gmx.liquidity_base.deposit import Deposit, DepositParams, DepositResult


class DepositOrder(Deposit):
    """GMX Deposit Order class for adding liquidity to markets.

    Provides a convenient interface for deposit operations, extending the base
    Deposit class with position-specific initialisation.

    Example:
        TODO: add example usage
    """

    def __init__(
        self,
        config,
        market_key: ChecksumAddress,
        initial_long_token: ChecksumAddress,
        initial_short_token: ChecksumAddress,
    ):
        """Initialise deposit order with market and token details.

        :param config: GMX configuration
        :type config: GMXConfig
        :param market_key: Market contract address (hex)
        :type market_key: ChecksumAddress
        :param initial_long_token: Long token address to deposit (hex)
        :type initial_long_token: ChecksumAddress
        :param initial_short_token: Short token address to deposit (hex)
        :type initial_short_token: ChecksumAddress
        """
        super().__init__(config)

        self.market_key = to_checksum_address(market_key)
        self.initial_long_token = to_checksum_address(initial_long_token)
        self.initial_short_token = to_checksum_address(initial_short_token)

        self.logger.debug(
            f"Initialized deposit order for market {self.market_key}, long_token: {self.initial_long_token}, short_token: {self.initial_short_token}",
        )

    def create_deposit_order(
        self,
        long_token_amount: int,
        short_token_amount: int,
        execution_buffer: float = 1.3,
        max_fee_per_gas: Optional[int] = None,
    ) -> DepositResult:
        """Create a deposit order transaction.

        Creates an unsigned transaction for adding liquidity to a GMX market.
        The transaction needs to be signed and sent by the user.

        :param long_token_amount: Amount of long tokens to deposit (in wei)
        :type long_token_amount: int
        :param short_token_amount: Amount of short tokens to deposit (in wei)
        :type short_token_amount: int
        :param execution_buffer: Multiplier for execution fee (default 1.3 = 30% buffer)
        :type execution_buffer: float
        :param max_fee_per_gas: Optional gas price override in wei
        :type max_fee_per_gas: Optional[int]
        :return: DepositResult containing unsigned transaction and execution details
        :rtype: DepositResult
        :raises ValueError: If parameters are invalid or market doesn't exist
        """
        params = DepositParams(
            market_key=self.market_key,
            initial_long_token=self.initial_long_token,
            initial_short_token=self.initial_short_token,
            long_token_amount=long_token_amount,
            short_token_amount=short_token_amount,
            execution_buffer=execution_buffer,
            max_fee_per_gas=max_fee_per_gas,
        )

        self.logger.debug(
            f"Creating deposit order: long_amount={long_token_amount}, short_amount={short_token_amount}",
        )

        return self.create_deposit(params)
