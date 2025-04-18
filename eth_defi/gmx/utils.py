"""
GMX Utilities Module

This module provides utility functions for the GMX integration.
"""

import logging
from typing import Any, Optional

from eth_defi.gmx.constants import GMX_TOKEN_ADDRESSES
from decimal import Decimal

from gmx_python_sdk.scripts.v2.get.get_markets import Markets

from gmx_python_sdk.scripts.v2.get.get_open_positions import GetOpenPositions
from gmx_python_sdk.scripts.v2.gmx_utils import ConfigManager, find_dictionary_by_key_value, get_tokens_address_dict, determine_swap_route


def token_symbol_to_address(chain: str, symbol: str) -> Optional[str]:
    """
    Convert a token symbol to its address.

    Args:
        chain: Chain name (arbitrum or avalanche)
        symbol: Token symbol

    Returns:
        Token address or None if not found
    """
    chain_tokens = GMX_TOKEN_ADDRESSES.get(chain.lower(), {})
    return chain_tokens.get(symbol.upper())


def format_position_for_display(position: dict[str, Any]) -> dict[str, Any]:
    """
    Format a position for display.

    Args:
        position: Raw position data

    Returns:
        Formatted position data
    """
    # Extract and format relevant fields for display
    return {"market": position.get("market_symbol", ""), "direction": "Long" if position.get("is_long") else "Short", "size_usd": position.get("position_size", 0), "collateral": position.get("collateral_token", ""), "leverage": position.get("leverage", 0), "entry_price": position.get("entry_price", 0), "current_price": position.get("mark_price", 0), "pnl_percent": position.get("percent_profit", 0)}


def calculate_estimated_liquidation_price(entry_price: float, collateral_usd: float, size_usd: float, is_long: bool, maintenance_margin: float = 0.01) -> float:  # 1% maintenance margin
    """
    Calculate an estimated liquidation price.

    Args:
        entry_price: Entry price of the position
        collateral_usd: Collateral in USD
        size_usd: Position size in USD
        is_long: Whether this is a long position
        maintenance_margin: Maintenance margin requirement

    Returns:
        Estimated liquidation price
    """
    leverage = size_usd / collateral_usd

    if is_long:
        # For longs, liquidation happens when price drops
        liquidation_price = entry_price * (1 - (1 / leverage) + maintenance_margin)
    else:
        # For shorts, liquidation happens when price rises
        liquidation_price = entry_price * (1 + (1 / leverage) - maintenance_margin)

    return liquidation_price


def get_positions(config, address: str = None) -> dict[str, Any]:
    """
    Get open positions for an address on a given network.

    If address is not passed, it will use the address from the user's config.

    Args:
        config: GMX configuration object
        address: Address to fetch open positions for (optional)

    Returns:
        dictionary containing all open positions, keyed by position identifier

    Raises:
        Exception: If no address is provided and none is in config
    """
    if address is None:
        address = config.user_wallet_address
        if address is None:
            raise Exception("No address passed in function or config!")

    positions = GetOpenPositions(config=config, address=address).get_data()

    if len(positions) > 0:
        logging.info(f"Open Positions for {address}:")
        for key in positions.keys():
            logging.info(key)

    return positions


def transform_open_position_to_order_parameters(config, positions: dict[str, Any], market_symbol: str, is_long: bool, slippage_percent: float, out_token: str, amount_of_position_to_close: float, amount_of_collateral_to_remove: float) -> dict[str, Any]:
    """
    Transform an open position into parameters for a close order.

    Finds the specified position by market symbol and direction, then formats it
    into parameters needed to close the position.

    Args:
        config: GMX configuration object
        positions: dictionary containing all open positions
        market_symbol: Symbol of the market to close
        is_long: True for long position, False for short
        slippage_percent: Slippage tolerance as a percentage
        out_token: Symbol of token to receive upon closing
        amount_of_position_to_close: Portion of position to close (0-1)
        amount_of_collateral_to_remove: Portion of collateral to remove (0-1)

    Returns:
        dictionary of order parameters formatted to close the position

    Raises:
        Exception: If the requested position cannot be found
    """
    direction = "long" if is_long else "short"
    position_dictionary_key = f"{market_symbol.upper()}_{direction}"

    try:
        raw_position_data = positions[position_dictionary_key]
        gmx_tokens = get_tokens_address_dict(config.chain)

        # Get collateral token address
        collateral_address = find_dictionary_by_key_value(gmx_tokens, "symbol", raw_position_data["collateral_token"])["address"]

        # Get index token address
        index_address = find_dictionary_by_key_value(gmx_tokens, "symbol", raw_position_data["market_symbol"][0])

        # Get output token address
        out_token_address = find_dictionary_by_key_value(gmx_tokens, "symbol", out_token)["address"]

        # Get markets info
        markets = Markets(config=config).info

        # Calculate swap path if needed
        swap_path = []
        if collateral_address != out_token_address:
            swap_path = determine_swap_route(markets, collateral_address, out_token_address)[0]

        # Calculate size delta based on position size and close amount
        size_delta = int((Decimal(raw_position_data["position_size"]) * (Decimal(10) ** 30)) * Decimal(amount_of_position_to_close))

        # Return formatted parameters
        return {"chain": config.chain, "market_key": raw_position_data["market"], "collateral_address": collateral_address, "index_token_address": index_address["address"], "is_long": raw_position_data["is_long"], "size_delta": size_delta, "initial_collateral_delta": int(raw_position_data["inital_collateral_amount"] * amount_of_collateral_to_remove), "slippage_percent": slippage_percent, "swap_path": swap_path}
    except KeyError:
        raise Exception(f"Couldn't find a {market_symbol} {direction} position for the given user!")


if __name__ == "__main__":
    config = ConfigManager(chain="arbitrum")
    config.set_config()

    positions = get_positions(config=config, address="0x0e9E19E7489E5F13a0940b3b6FcB84B25dc68177")

    # market_symbol = "ETH"
    # is_long = True

    # out_token = "USDC"
    # amount_of_position_to_close = 1
    # amount_of_collateral_to_remove = 1

    # order_params = transform_open_position_to_order_parameters(
    #     config=config,
    #     positions=positions,
    #     market_symbol=market_symbol,
    #     is_long=is_long,
    #     slippage_percent=0.003,
    #     out_token="USDC",
    #     amount_of_position_to_close=amount_of_position_to_close,
    #     amount_of_collateral_to_remove=amount_of_collateral_to_remove
    # )
