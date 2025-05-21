"""
GMX Order Management Module

This module provides functionality for managing orders on GMX.
"""

from typing import Any, Optional, Union

from gmx_python_sdk.scripts.v2.get.get_open_positions import GetOpenPositions
from gmx_python_sdk.scripts.v2.order.create_decrease_order import DecreaseOrder
from gmx_python_sdk.scripts.v2.order.order_argument_parser import OrderArgumentParser
from eth_typing import ChecksumAddress as Address

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.utils import transform_open_position_to_order_parameters


class GMXOrderManager:
    """
    Order management functionality for GMX protocol.
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize order manager module.

        Args:
            config: GMX configuration object
        """
        self.config = config

    def get_open_positions(self, address: Optional[Union[str, Address]] = None) -> dict[str, Any]:
        """
        Get open positions for a user.

        Args:
            address: Wallet address (uses configured address if None)

        Returns:
            Dictionary of open positions
        """
        if address is None:
            address = self.config.get_wallet_address()

        if not address:
            raise ValueError("No wallet address provided")

        read_config = self.config.get_read_config()
        return GetOpenPositions(read_config, address=address).get_data()

    def close_position(self, parameters: dict, debug_mode: bool = False) -> DecreaseOrder:
        """
        Close a position using direct parameter dictionary.

        This method allows closing a position by providing parameters directly to the OrderArgumentParser,
        which is useful for more complex order configurations.

        Args:
            parameters: Dictionary of parameters including:
                - chain: Chain name ('arbitrum' or 'avalanche')
                - index_token_symbol: Symbol of the index token (e.g., "SOL")
                - collateral_token_symbol: Symbol of the collateral token (e.g., "SOL")
                - start_token_symbol: Symbol of the starting token (usually same as collateral)
                - is_long: Boolean indicating if position is long
                - size_delta_usd: Amount of position to close in USD
                - initial_collateral_delta: Amount of tokens to remove as collateral
                - slippage_percent: Slippage tolerance as a decimal
            debug_mode: Run in debug mode without submitting transaction

        Returns:
            DecreaseOrder object

        Example:
            ```python
            parameters = {
                "chain": 'arbitrum',
                "index_token_symbol": "SOL",
                "collateral_token_symbol": "SOL",
                "start_token_symbol": "SOL",
                "is_long": True,
                "size_delta_usd": 3,
                "initial_collateral_delta": 0.027,
                "slippage_percent": 0.03
            }
            order = order_manager.close_position_with_parameters(parameters, debug_mode=True)
            ```
        """
        # Ensure we have write access
        write_config = self.config.get_write_config()

        # Validate required parameters
        required_params = [
            "index_token_symbol",
            "collateral_token_symbol",
            "is_long",
            "size_delta_usd",
        ]
        for param in required_params:
            if param not in parameters:
                raise ValueError(f"Missing required parameter: {param}")

        # Set chain parameter if not provided
        if "chain" not in parameters:
            parameters["chain"] = self.config.get_chain()

        # Process parameters through the OrderArgumentParser
        order_parameters = OrderArgumentParser(write_config, is_decrease=True).process_parameters_dictionary(parameters)

        # Create decrease order
        return DecreaseOrder(
            config=write_config,
            market_key=order_parameters["market_key"],
            collateral_address=order_parameters["collateral_address"],
            index_token_address=order_parameters["index_token_address"],
            is_long=order_parameters["is_long"],
            size_delta=order_parameters["size_delta"],
            initial_collateral_delta_amount=order_parameters["initial_collateral_delta"],
            slippage_percent=order_parameters["slippage_percent"],
            swap_path=order_parameters.get("swap_path", []),
            debug_mode=debug_mode,
        )

    def close_position_by_key(
        self,
        position_key: str,
        out_token_symbol: str,
        amount_of_position_to_close: float = 1.0,
        amount_of_collateral_to_remove: float = 1.0,
        slippage_percent: float = 0.003,
        debug_mode: bool = False,
        address: Optional[Union[str, Address]] = None,
    ) -> DecreaseOrder:
        """
        Close a position by its key.

        Args:
            position_key: Key identifying the position to close
            out_token_symbol: Symbol of token to receive
            amount_of_position_to_close: Portion of position to close (0-1)
            amount_of_collateral_to_remove: Portion of collateral to remove (0-1)
            slippage_percent: Slippage tolerance as a decimal
            debug_mode: Run in debug mode without submitting transaction
            address: address of the wallet with opened position/s

        Returns:
            Transaction receipt or debug information
        """
        # Ensure we have write access
        write_config = self.config.get_write_config()

        # Get positions
        if address:
            positions = self.get_open_positions(address)
        else:
            # if address is not passed get the address of the user from config
            positions = self.get_open_positions()

        if position_key not in positions.keys():
            raise ValueError(f"Position with key {position_key} not found")

        # key will be like this `ETH_short`
        # Split the key to get market and direction
        parts = position_key.split("_")
        if len(parts) != 2:
            raise ValueError(f"Invalid position key format: {position_key}")

        market_symbol = parts[0]
        direction = parts[1]
        is_long = direction.lower() == "long"

        # Transform position to order parameters
        order_parameters = transform_open_position_to_order_parameters(
            config=write_config,
            positions=positions,
            market_symbol=market_symbol,
            is_long=is_long,
            slippage_percent=slippage_percent,
            out_token=out_token_symbol,
            amount_of_position_to_close=amount_of_position_to_close,
            amount_of_collateral_to_remove=amount_of_collateral_to_remove,
        )

        # Create decrease order
        return DecreaseOrder(
            config=write_config,
            market_key=order_parameters["market_key"],
            collateral_address=order_parameters["collateral_address"],
            index_token_address=order_parameters["index_token_address"],
            is_long=order_parameters["is_long"],
            size_delta=order_parameters["size_delta"],
            initial_collateral_delta_amount=order_parameters["initial_collateral_delta"],
            slippage_percent=order_parameters["slippage_percent"],
            swap_path=order_parameters.get("swap_path", []),
            debug_mode=debug_mode,
        )
