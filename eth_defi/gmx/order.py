"""
GMX Order Management Module

This module provides functionality for managing orders on GMX.
"""

from typing import Dict, Any, List, Optional, Union

from gmx_python_sdk.scripts.v2.get.get_open_positions import GetOpenPositions
from gmx_python_sdk.scripts.v2.order.create_decrease_order import DecreaseOrder

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

    def get_open_positions(self, address: Optional[str] = None) -> Dict[str, Any]:
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

    def close_position_by_key(self, position_key: str, out_token_symbol: str, amount_of_position_to_close: float = 1.0, amount_of_collateral_to_remove: float = 1.0, slippage_percent: float = 0.003, debug_mode: bool = False) -> DecreaseOrder:
        """
        Close a position by its key.

        Args:
            position_key: Key identifying the position to close
            out_token_symbol: Symbol of token to receive
            amount_of_position_to_close: Portion of position to close (0-1)
            amount_of_collateral_to_remove: Portion of collateral to remove (0-1)
            slippage_percent: Slippage tolerance as a decimal
            debug_mode: Run in debug mode without submitting transaction

        Returns:
            Transaction receipt or debug information
        """
        # Ensure we have write access
        write_config = self.config.get_write_config()

        # Get positions
        positions = self.get_open_positions()

        if position_key not in positions:
            raise ValueError(f"Position with key {position_key} not found")

        # Split the key to get market and direction
        parts = position_key.split("_")
        if len(parts) != 2:
            raise ValueError(f"Invalid position key format: {position_key}")

        market_symbol = parts[0]
        direction = parts[1]
        is_long = direction.lower() == "long"

        # Transform position to order parameters
        order_parameters = transform_open_position_to_order_parameters(config=write_config, positions=positions, market_symbol=market_symbol, is_long=is_long, slippage_percent=slippage_percent, out_token=out_token_symbol, amount_of_position_to_close=amount_of_position_to_close, amount_of_collateral_to_remove=amount_of_collateral_to_remove)

        # Create decrease order
        return DecreaseOrder(config=write_config, market_key=order_parameters["market_key"], collateral_address=order_parameters["collateral_address"], index_token_address=order_parameters["index_token_address"], is_long=order_parameters["is_long"], size_delta=order_parameters["size_delta"], initial_collateral_delta_amount=order_parameters["initial_collateral_delta"], slippage_percent=order_parameters["slippage_percent"], swap_path=order_parameters.get("swap_path", []), debug_mode=debug_mode)
