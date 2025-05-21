"""
GMX Trading Module

This module provides functionality for trading on GMX.
"""

from typing import Optional, Any, Dict, List, Union

from gmx_python_sdk.scripts.v2.order.create_increase_order import IncreaseOrder
from gmx_python_sdk.scripts.v2.order.create_decrease_order import DecreaseOrder
from gmx_python_sdk.scripts.v2.order.create_swap_order import SwapOrder
from gmx_python_sdk.scripts.v2.order.order_argument_parser import OrderArgumentParser

from eth_defi.gmx.config import GMXConfig

# TODO: Add option to Return tx_hash from all of the on-chain operations


class GMXTrading:
    """
    Trading functionality for GMX protocol.
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize trading module.

        Args:
            config: GMX configuration object
        """
        self.config = config

    def open_position(
        self,
        market_symbol: str,
        collateral_symbol: str,
        start_token_symbol: str,
        is_long: bool,
        size_delta_usd: float,
        leverage: float,
        slippage_percent: Optional[float] = 0.003,
        debug_mode: Optional[bool] = False,
        **kwargs,
    ) -> IncreaseOrder:
        """
        Open a new position on GMX.

        Args:
            market_symbol: Symbol of the market (e.g., "ETH")
            collateral_symbol: Symbol of the collateral token (e.g., "USDC")
            start_token_symbol: Symbol of the token to start with (e.g., "USDC")
            is_long: Whether this is a long position
            size_delta_usd: Position size in USD
            leverage: Leverage multiplier
            slippage_percent: Slippage tolerance as a decimal
            debug_mode: Run in debug mode without submitting transaction
            **kwargs: Additional parameters to pass to IncreaseOrder (see GMX Python SDK docs)
                Some common parameters include:
                - auto_cancel: Auto-cancel order if it can't be fulfilled
                - execution_buffer: Buffer for execution fee (default 1.3)
                - max_fee_per_gas: Maximum fee per gas unit

        Returns:
            IncreaseOrder instance with transaction details
        """
        # Ensure we have write access
        write_config = self.config.get_write_config()

        # Prepare parameters dictionary
        parameters = {
            "chain": self.config.get_chain(),
            "index_token_symbol": market_symbol,
            "collateral_token_symbol": collateral_symbol,
            "start_token_symbol": start_token_symbol,
            "is_long": is_long,
            "size_delta_usd": size_delta_usd,
            "leverage": leverage,
            "slippage_percent": slippage_percent,
        }

        # Process parameters
        order_parameters = OrderArgumentParser(write_config, is_increase=True).process_parameters_dictionary(parameters)

        # Create order with any additional parameters
        return IncreaseOrder(
            config=write_config,
            market_key=order_parameters["market_key"],
            collateral_address=order_parameters["collateral_address"],
            index_token_address=order_parameters["index_token_address"],
            is_long=order_parameters["is_long"],
            size_delta=order_parameters["size_delta"],
            initial_collateral_delta_amount=order_parameters["initial_collateral_delta"],
            slippage_percent=order_parameters["slippage_percent"],
            swap_path=order_parameters["swap_path"],
            debug_mode=debug_mode,
            **kwargs,
        )

    def close_position(
        self,
        market_symbol: str,
        collateral_symbol: str,
        start_token_symbol: str,
        is_long: bool,
        size_delta_usd: float,
        initial_collateral_delta: float,
        slippage_percent: Optional[float] = 0.003,
        debug_mode: Optional[bool] = False,
        **kwargs,
    ) -> DecreaseOrder:
        """
        Close a position on GMX.

        Args:
            market_symbol: Symbol of the market (e.g., "ETH")
            collateral_symbol: Symbol of the collateral token (e.g., "USDC")
            start_token_symbol: Symbol of the token to start with (e.g., "USDC")
            is_long: Whether this is a long position
            size_delta_usd: Position size in USD to close
            initial_collateral_delta: Amount of collateral to remove
            slippage_percent: Slippage tolerance as a decimal
            debug_mode: Run in debug mode without submitting transaction
            **kwargs: Additional parameters to pass to DecreaseOrder (see GMX Python SDK docs)
                Some common parameters include:
                - auto_cancel: Auto-cancel order if it can't be fulfilled
                - execution_buffer: Buffer for execution fee (default 1.3)
                - max_fee_per_gas: Maximum fee per gas unit

        Returns:
            DecreaseOrder instance with transaction details
        """
        # Ensure we have write access
        write_config = self.config.get_write_config()

        # Prepare parameters dictionary
        parameters = {
            "chain": self.config.get_chain(),
            "index_token_symbol": market_symbol,
            "collateral_token_symbol": collateral_symbol,
            "start_token_symbol": start_token_symbol,
            "is_long": is_long,
            "size_delta_usd": size_delta_usd,
            "initial_collateral_delta": initial_collateral_delta,
            "slippage_percent": slippage_percent,
        }

        # Process parameters
        order_parameters = OrderArgumentParser(write_config, is_decrease=True).process_parameters_dictionary(parameters)

        # Create order with any additional parameters
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
            **kwargs,
        )

    def swap_tokens(
        self,
        out_token_symbol: str,
        start_token_symbol: str,
        amount: float,
        position_usd: Optional[float] = 0,
        slippage_percent: Optional[float] = 0.02,
        debug_mode: Optional[bool] = False,
        **kwargs,
    ) -> SwapOrder:
        """
        Swap tokens on GMX.

        Args:
            out_token_symbol: Symbol of the token to receive
            start_token_symbol: Symbol of the token to swap
            amount: Amount of start token to swap
            position_usd: Position size in USD (for size-based swaps)
            slippage_percent: Slippage tolerance as a decimal
            debug_mode: Run in debug mode without submitting transaction
            **kwargs: Additional parameters to pass to SwapOrder (see GMX Python SDK docs)
                Some common parameters include:
                - auto_cancel: Auto-cancel order if it can't be fulfilled
                - execution_buffer: Buffer for execution fee (default 1.3)
                - max_fee_per_gas: Maximum fee per gas unit

        Returns:
            SwapOrder instance with transaction details
        """
        # Ensure we have write access
        write_config = self.config.get_write_config()

        # Prepare parameters dictionary
        parameters = {
            "chain": self.config.get_chain(),
            "out_token_symbol": out_token_symbol,
            "start_token_symbol": start_token_symbol,
            "is_long": False,
            "size_delta_usd": position_usd,
            "initial_collateral_delta": amount,
            "slippage_percent": slippage_percent,
        }

        # Process parameters
        order_parameters = OrderArgumentParser(write_config, is_swap=True).process_parameters_dictionary(parameters)
        print(f"{write_config.get_signer().get_address()=}")
        print(f"{write_config.get_web3_connection().provider.endpoint_uri=}")
        # Create order with any additional parameters
        return SwapOrder(
            config=write_config,
            market_key=order_parameters["swap_path"][-1],
            start_token=order_parameters["start_token_address"],
            out_token=order_parameters["out_token_address"],
            collateral_address=order_parameters["start_token_address"],
            index_token_address=order_parameters["out_token_address"],
            is_long=order_parameters["is_long"],
            size_delta=order_parameters["size_delta_usd"],
            initial_collateral_delta_amount=order_parameters["initial_collateral_delta"],
            slippage_percent=order_parameters["slippage_percent"],
            swap_path=order_parameters["swap_path"],
            debug_mode=debug_mode,
            **kwargs,
        )
