"""
GMX Increase Order Implementation

Specialised order class for opening or increasing positions on GMX protocol.
Extends BaseOrder to provide increased position functionality and returning unsigned transactions.
"""

import logging
from typing import Optional

from eth_utils import to_checksum_address
from eth_typing import ChecksumAddress

from eth_defi.gmx.order.base_order import BaseOrder, OrderParams, OrderResult


logger = logging.getLogger(__name__)


class IncreaseOrder(BaseOrder):
    """GMX Increase Order class for opening or increasing positions.

    Handles creation of increase position transactions on GMX protocol, providing
    unsigned transaction generation for external signing.

    Example:
        TODO: Add example usage
    """

    def __init__(
        self,
        config,
        market_key: ChecksumAddress,
        collateral_address: ChecksumAddress,
        index_token_address: ChecksumAddress,
        is_long: bool,
    ):
        """Initialise increase order with position identification.

        :param config: GMX configuration
        :type config: GMXConfig
        :param market_key: Market contract address (hex)
        :type market_key: ChecksumAddress
        :param collateral_address: Collateral token address (hex)
        :type collateral_address: ChecksumAddress
        :param index_token_address: Index token address (hex)
        :type index_token_address: ChecksumAddress
        :param is_long: True for long position, False for short
        :type is_long: bool
        """
        super().__init__(config)

        self.market_key = to_checksum_address(market_key)
        self.collateral_address = to_checksum_address(collateral_address)
        self.index_token_address = to_checksum_address(index_token_address)
        self.is_long = is_long

        logger.debug("Initialized increase order for market %s, %s position", self.market_key, "LONG" if self.is_long else "SHORT")

    def create_increase_order(
        self,
        size_delta: float,
        initial_collateral_delta_amount: int | str,
        slippage_percent: float = 0.003,
        swap_path: Optional[list[str]] = None,
        execution_buffer: float = 2.2,
        auto_cancel: bool = False,
        data_list: Optional[list[str]] = None,
        callback_gas_limit: int = 0,
        min_output_amount: int = 0,
        valid_from_time: int = 0,
    ) -> OrderResult:
        """Create an increase order transaction.

        Creates an unsigned transaction for opening or increasing a position on GMX.
        The transaction needs to be signed and sent by the user.

        :param size_delta: Position size to increase in USD
        :type size_delta: float
        :param initial_collateral_delta_amount: Amount of collateral to add (in token's smallest unit)
        :type initial_collateral_delta_amount: int | str
        :param slippage_percent: Slippage tolerance as decimal (e.g., 0.003 = 0.3%)
        :type slippage_percent: float
        :param swap_path: Optional list of market addresses for swap routing
        :type swap_path: Optional[list[str]]
        :param execution_buffer: Gas buffer multiplier for execution fee
        :type execution_buffer: float
        :param auto_cancel: Whether to auto-cancel the order if it can't execute
        :type auto_cancel: bool
        :param data_list:
        :type data_list: list
        :param callback_gas_limit: Gas limit for callback execution
        :type callback_gas_limit: int
        :param min_output_amount: Minimum output amount for swaps
        :type min_output_amount: int
        :param valid_from_time: Timestamp when order becomes valid
        :type valid_from_time: int
        :return: OrderResult containing unsigned transaction and execution details
        :rtype: OrderResult
        :raises ValueError: If parameters are invalid or market doesn't exist
        """
        if swap_path is None:
            swap_path = []
        if data_list is None:
            data_list = []

        params = OrderParams(
            market_key=self.market_key,
            collateral_address=self.collateral_address,
            index_token_address=self.index_token_address,
            is_long=self.is_long,
            size_delta=size_delta,
            initial_collateral_delta_amount=str(initial_collateral_delta_amount),
            slippage_percent=slippage_percent,
            swap_path=swap_path,
            execution_buffer=execution_buffer,
            auto_cancel=auto_cancel,
            data_list=data_list,
            callback_gas_limit=callback_gas_limit,
            min_output_amount=min_output_amount,
            valid_from_time=valid_from_time,
        )

        return self.order_builder(params, is_open=True, is_close=False, is_swap=False)
