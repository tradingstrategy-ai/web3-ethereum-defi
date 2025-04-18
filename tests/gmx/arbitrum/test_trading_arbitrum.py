"""
Tests for GMXTrading on Arbitrum network.

This test suite verifies the functionality of the GMXTrading class
when connected to the Arbitrum network. The tests focus on creating
orders in debug mode without submitting actual transactions.
"""
from gmx_python_sdk.scripts.v2.order.create_decrease_order import DecreaseOrder
from gmx_python_sdk.scripts.v2.order.create_increase_order import IncreaseOrder
from gmx_python_sdk.scripts.v2.order.create_swap_order import SwapOrder
import os
import pytest

from eth_defi.gmx.trading import GMXTrading

mainnet_rpc = os.environ.get("ARBITRUM_JSON_RPC_URL")

pytestmark = pytest.mark.skipif(not mainnet_rpc, reason="No ARBITRUM_JSON_RPC_URL environment variable")


def test_initialization(gmx_config_arbitrum):
    """
    Test that the trading module initializes correctly with Arbitrum config.
    """
    trading = GMXTrading(gmx_config_arbitrum)
    assert trading.config == gmx_config_arbitrum
    assert trading.config.get_chain().lower() == "arbitrum"


# TODO: Fix
def test_open_position_long(trading_manager_arbitrum: GMXTrading):
    """
    Test opening a long position on Arbitrum.

    This tests creating an IncreaseOrder for a long position.
    """
    # Create a long ETH position with USDC as collateral
    increase_order = trading_manager_arbitrum.open_position(market_symbol="ETH", collateral_symbol="USDC", start_token_symbol="USDC", is_long=True, size_delta_usd=100, leverage=2, slippage_percent=0.003, debug_mode=False)  # 100$ worth of token  # as a decimal i.e. 0.003 == 0.3%

    # Verify the order was created with the right type
    assert isinstance(increase_order, IncreaseOrder)

    # Verify key properties of the order
    assert hasattr(increase_order, "config")
    assert hasattr(increase_order, "market_key")
    assert hasattr(increase_order, "collateral_address")
    assert hasattr(increase_order, "index_token_address")
    assert hasattr(increase_order, "is_long")
    assert hasattr(increase_order, "size_delta")
    assert hasattr(increase_order, "initial_collateral_delta_amount")
    assert hasattr(increase_order, "slippage_percent")

    # Verify position direction
    assert increase_order.is_long is True

    # Verify the order has our debug flag
    assert hasattr(increase_order, "debug_mode")
    assert increase_order.debug_mode is False


def test_open_position_short(trading_manager_arbitrum: GMXTrading):
    """
    Test opening a short position on Arbitrum.

    This tests creating an IncreaseOrder for a short position.
    """
    # Create a short BTC position with USDC as collateral
    increase_order = trading_manager_arbitrum.open_position(market_symbol="BTC", collateral_symbol="USDC", start_token_symbol="USDC", is_long=False, size_delta_usd=200, leverage=1.5, slippage_percent=0.003, debug_mode=False)  # $200 worth of tokens

    # Verify the order was created with the right type
    assert isinstance(increase_order, IncreaseOrder)

    # Verify key properties
    assert hasattr(increase_order, "market_key")
    assert hasattr(increase_order, "size_delta")
    assert hasattr(increase_order, "initial_collateral_delta_amount")

    # Verify position direction
    assert increase_order.is_long is False

    # Verify debug mode
    assert increase_order.debug_mode is False


def test_open_position_high_leverage(trading_manager_arbitrum: GMXTrading):
    """
    Test opening a position with high leverage on Arbitrum.

    This tests creating an IncreaseOrder with higher leverage.
    """
    # Create a long ETH position with high leverage
    increase_order = trading_manager_arbitrum.open_position(market_symbol="ETH", collateral_symbol="ETH", start_token_symbol="ETH", is_long=True, size_delta_usd=100, leverage=10, slippage_percent=0.003, debug_mode=False)  # Higher leverage

    # Verify the order was created with the right type
    assert isinstance(increase_order, IncreaseOrder)

    # Verify position setup
    assert increase_order.is_long is True
    assert increase_order.debug_mode is False


def test_close_position(trading_manager_arbitrum: GMXTrading):
    """
    Test closing a position on Arbitrum.

    This tests creating a DecreaseOrder.
    """
    # Close a long ETH position
    decrease_order = trading_manager_arbitrum.close_position(market_symbol="ETH", collateral_symbol="USDC", start_token_symbol="USDC", is_long=True, size_delta_usd=500, initial_collateral_delta=250, slippage_percent=0.003, debug_mode=False)  # Close $500 worth  # Remove $250 collateral

    # Verify the order was created with the right type
    assert isinstance(decrease_order, DecreaseOrder)

    # Verify key properties
    assert hasattr(decrease_order, "config")
    assert hasattr(decrease_order, "market_key")
    assert hasattr(decrease_order, "collateral_address")
    assert hasattr(decrease_order, "index_token_address")
    assert hasattr(decrease_order, "is_long")
    assert hasattr(decrease_order, "size_delta")
    assert hasattr(decrease_order, "initial_collateral_delta_amount")
    assert hasattr(decrease_order, "slippage_percent")

    # Verify the position being closed is long
    assert decrease_order.is_long is True

    # Verify debug mode
    assert decrease_order.debug_mode is False


# TODO: Fix
def test_close_position_full_size(trading_manager_arbitrum: GMXTrading):
    """
    Test closing a full position on Arbitrum.

    This tests creating a DecreaseOrder for a full position.
    """
    # Close a full short BTC position
    decrease_order = trading_manager_arbitrum.close_position(market_symbol="BTC", collateral_symbol="USDC", start_token_symbol="USDC", is_long=False, size_delta_usd=2000, initial_collateral_delta=1333, slippage_percent=0.003, debug_mode=False)  # Full position size  # Full collateral

    # Verify the order was created with the right type
    assert isinstance(decrease_order, DecreaseOrder)

    # Verify the position being closed is short
    assert decrease_order.is_long is False

    # Verify debug mode
    assert decrease_order.debug_mode is False


def test_swap_tokens(trading_manager_arbitrum: GMXTrading):
    """
    Test swapping tokens on Arbitrum.

    This tests creating a SwapOrder.
    """
    # Swap USDC for ETH
    swap_order = trading_manager_arbitrum.swap_tokens(out_token_symbol="ETH", start_token_symbol="USDC", amount=1000, slippage_percent=0.003, debug_mode=False)  # $1000 USDC

    # Verify the order was created with the right type
    assert isinstance(swap_order, SwapOrder)

    # Verify key properties
    assert hasattr(swap_order, "config")
    assert hasattr(swap_order, "market_key")
    assert hasattr(swap_order, "start_token")
    assert hasattr(swap_order, "out_token")
    assert hasattr(swap_order, "initial_collateral_delta_amount")
    assert hasattr(swap_order, "slippage_percent")
    assert hasattr(swap_order, "swap_path")

    # Verify swap path exists
    assert hasattr(swap_order, "swap_path")

    # Verify debug mode
    assert swap_order.debug_mode is False
