"""
Tests for GMXTrading with parametrized chain testing.

This test suite verifies the functionality of the GMXTrading class
when connected to different networks. The tests focus on creating
orders in debug mode without submitting actual transactions.
"""
from gmx_python_sdk.scripts.v2.order.create_decrease_order import DecreaseOrder
from gmx_python_sdk.scripts.v2.order.create_increase_order import IncreaseOrder
from gmx_python_sdk.scripts.v2.order.create_swap_order import SwapOrder
import pytest

from eth_defi.gmx.trading import GMXTrading


@pytest.fixture()
def trading_manager(gmx_config_fork):
    """
    Create a GMXTrading instance for the current chain being tested.
    The wallet already has all tokens needed for testing through gmx_config_fork.
    """
    return GMXTrading(gmx_config_fork)


def test_initialization(chain_name, gmx_config):
    """
    Test that the trading module initializes correctly with chain-specific config.
    """
    trading = GMXTrading(gmx_config)
    assert trading.config == gmx_config
    assert trading.config.get_chain().lower() == chain_name.lower()


def test_open_position_long(chain_name, trading_manager):
    """
    Test opening a long position.

    This tests creating an IncreaseOrder for a long position.
    """
    # Select appropriate parameters based on the chain
    if chain_name == "arbitrum":
        market_symbol = "ETH"
        collateral_symbol = "USDC"
    # avalanche
    else:
        market_symbol = "AVAX"
        collateral_symbol = "USDC"

    # Create a long position with USDC as collateral
    increase_order = trading_manager.open_position(market_symbol=market_symbol, collateral_symbol=collateral_symbol, start_token_symbol=collateral_symbol, is_long=True, size_delta_usd=100, leverage=2, slippage_percent=0.003, debug_mode=False)  # 100$ worth of token  # as a decimal i.e. 0.003 == 0.3%

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


def test_open_position_short(chain_name, trading_manager):
    """
    Test opening a short position.

    This tests creating an IncreaseOrder for a short position.
    """
    # Select appropriate parameters based on the chain
    if chain_name == "arbitrum":
        market_symbol = "BTC"
    # avalanche
    else:
        market_symbol = "AVAX"

    # Create a short position with USDC as collateral
    increase_order = trading_manager.open_position(market_symbol=market_symbol, collateral_symbol="USDC", start_token_symbol="USDC", is_long=False, size_delta_usd=200, leverage=1.5, slippage_percent=0.003, debug_mode=False)  # $200 worth of tokens

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


def test_open_position_high_leverage(chain_name, trading_manager):
    """
    Test opening a position with high leverage.

    This tests creating an IncreaseOrder with higher leverage.
    """
    # Select appropriate parameters based on the chain
    if chain_name == "arbitrum":
        market_symbol = "ETH"
        collateral_symbol = "ETH"
    # avalanche
    else:
        market_symbol = "AVAX"
        collateral_symbol = "AVAX"

    # Create a long position with high leverage
    increase_order = trading_manager.open_position(market_symbol=market_symbol, collateral_symbol=collateral_symbol, start_token_symbol=collateral_symbol, is_long=True, size_delta_usd=100, leverage=10, slippage_percent=0.003, debug_mode=False)  # Higher leverage

    # Verify the order was created with the right type
    assert isinstance(increase_order, IncreaseOrder)

    # Verify position setup
    assert increase_order.is_long is True
    assert increase_order.debug_mode is False


def test_close_position(chain_name, trading_manager):
    """
    Test closing a position.

    This tests creating a DecreaseOrder.
    """
    # Select appropriate parameters based on the chain
    if chain_name == "arbitrum":
        market_symbol = "ETH"
    # avalanche
    else:
        market_symbol = "AVAX"

    # Close a long position
    decrease_order = trading_manager.close_position(market_symbol=market_symbol, collateral_symbol="USDC", start_token_symbol="USDC", is_long=True, size_delta_usd=500, initial_collateral_delta=250, slippage_percent=0.003, debug_mode=False)  # Close $500 worth  # Remove $250 collateral

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


def test_close_position_full_size(chain_name, trading_manager):
    """
    Test closing a full position.

    This tests creating a DecreaseOrder for a full position.
    """
    # Select appropriate parameters based on the chain
    if chain_name == "arbitrum":
        market_symbol = "BTC"
        size_delta = 2000
        collateral_delta = 1333
    # avalanche
    else:
        market_symbol = "AVAX"
        size_delta = 200
        collateral_delta = 133
        collateral_symbol = "AVAX"

    # Close a full short position
    decrease_order = trading_manager.close_position(market_symbol=market_symbol, collateral_symbol="USDC", start_token_symbol="USDC", is_long=False, size_delta_usd=size_delta, initial_collateral_delta=collateral_delta, slippage_percent=0.003, debug_mode=False)  # Full position size  # Full collateral

    # Verify the order was created with the right type
    assert isinstance(decrease_order, DecreaseOrder)

    # Verify the position being closed is short
    assert decrease_order.is_long is False

    # Verify debug mode
    assert decrease_order.debug_mode is False


def test_swap_tokens(chain_name, trading_manager):
    """
    Test swapping tokens.

    This tests creating a SwapOrder.
    """
    # Select appropriate parameters based on the chain
    if chain_name == "arbitrum":
        out_token_symbol = "ETH"
    # avalanche
    else:
        # For https://github.com/gmx-io/gmx-synthetics/issues/164 skip the test for avalanche
        pytest.skip("Skipping swap_tokens for avalanche because of the known issue in the Reader contract")
        out_token_symbol = "GMX"

    # Swap USDC for chain-specific native token
    swap_order = trading_manager.swap_tokens(out_token_symbol=out_token_symbol, start_token_symbol="USDC", amount=1000, slippage_percent=0.003, debug_mode=False)  # $1000 USDC

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
