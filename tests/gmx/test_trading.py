"""
Tests for GMXTrading with parametrized chain testing.

This test suite verifies the functionality of the GMXTrading class
when connected to different networks. The tests focus on creating
orders in debug mode without submitting actual transactions.
"""

import pytest

from gmx_python_sdk.scripts.v2.order.create_decrease_order import DecreaseOrder
from gmx_python_sdk.scripts.v2.order.create_increase_order import IncreaseOrder
from gmx_python_sdk.scripts.v2.order.create_swap_order import SwapOrder

from eth_defi.gmx.trading import GMXTrading
from eth_defi.gmx.testing import emulate_keepers


# TODO: use to avoid race condition https://web3-ethereum-defi.readthedocs.io/api/core/_autosummary/eth_defi.trace.assert_transaction_success_with_explanation.html#eth_defi.trace.assert_transaction_success_with_explanation


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


def test_open_position_long(chain_name, trading_manager, gmx_config_fork, usdc):
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

    # Get test wallet address
    wallet_address = gmx_config_fork.get_wallet_address()

    # Check initial balances
    initial_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()

    # Create a long position with USDC as collateral
    # Using ACTUAL transaction (not debug mode) to test balance changes
    increase_order = trading_manager.open_position(
        market_symbol=market_symbol,
        collateral_symbol=collateral_symbol,
        start_token_symbol=collateral_symbol,
        is_long=True,
        size_delta_usd=100,
        leverage=2,
        slippage_percent=0.003,
        debug_mode=False,
        execution_buffer=2.2,
    )

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

    # Check final balances (USDC should decrease)
    final_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()

    # Verify USDC was spent
    assert final_usdc_balance < initial_usdc_balance, "USDC balance should decrease after opening a long position"


def test_open_position_short(chain_name, trading_manager, gmx_config_fork, usdc):
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

    # Get test wallet address
    wallet_address = gmx_config_fork.get_wallet_address()

    # Check initial balances
    initial_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()

    # Create a short position with USDC as collateral
    increase_order = trading_manager.open_position(
        market_symbol=market_symbol,
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=False,
        size_delta_usd=200,
        leverage=1.5,
        slippage_percent=0.003,
        debug_mode=False,
        execution_buffer=2.2,
    )

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

    # Check final balances (USDC should decrease)
    final_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()

    # Verify USDC was spent
    assert final_usdc_balance < initial_usdc_balance, "USDC balance should decrease after opening a short position"


def test_open_position_high_leverage(chain_name, trading_manager, gmx_config_fork, wrapped_native_token):
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

    # Get test wallet address
    wallet_address = gmx_config_fork.get_wallet_address()

    # Check initial balances
    initial_native_balance = wrapped_native_token.contract.functions.balanceOf(wallet_address).call()

    # Create a long position with high leverage
    increase_order = trading_manager.open_position(
        market_symbol=market_symbol,
        collateral_symbol=collateral_symbol,
        start_token_symbol=collateral_symbol,
        is_long=True,
        size_delta_usd=100,
        leverage=10,
        slippage_percent=0.003,
        debug_mode=False,
        execution_buffer=2.2,
    )

    # Verify the order was created with the right type
    assert isinstance(increase_order, IncreaseOrder)

    # Verify position setup
    assert increase_order.is_long is True
    assert increase_order.debug_mode is False

    # Check final balances (Native token should decrease)
    final_native_balance = wrapped_native_token.contract.functions.balanceOf(wallet_address).call()

    # Verify native token was spent
    assert final_native_balance < initial_native_balance, f"{wrapped_native_token.symbol} balance should decrease after opening a high leverage position"


def test_close_position(chain_name, trading_manager, gmx_config_fork, usdc, web3_fork):
    """
    Test closing a position.

    This test first creates a position, then closes it to ensure both operations work correctly.
    """
    # Select appropriate parameters based on the chain
    if chain_name == "arbitrum":
        market_symbol = "ETH"
    # avalanche
    else:
        market_symbol = "AVAX"

    # Get test wallet address
    wallet_address = gmx_config_fork.get_wallet_address()

    # First, create a position to close
    trading_manager.open_position(
        market_symbol=market_symbol,
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=True,
        size_delta_usd=500,
        leverage=2,
        slippage_percent=0.003,
        debug_mode=False,
        execution_buffer=2.2,
    )

    # Small delay to allow the position to be processed
    web3_fork.provider.make_request("evm_increaseTime", [60])  # Advance time by 60 seconds
    web3_fork.provider.make_request("evm_mine", [])  # Mine a new block

    # Check USDC balance before closing position
    usdc_balance_before_close = usdc.contract.functions.balanceOf(wallet_address).call()

    # Close the position
    decrease_order = trading_manager.close_position(
        market_symbol=market_symbol,
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=True,
        size_delta_usd=500,
        initial_collateral_delta=250,
        slippage_percent=0.003,
        debug_mode=False,
        execution_buffer=2.2,
    )  # Close full position  # Remove half of collateral

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

    # Check USDC balance after closing position
    usdc_balance_after_close = usdc.contract.functions.balanceOf(wallet_address).call()

    # Verify USDC balance has increased after closing the position
    # Note: Due to fees, we might not get back the exact amount, but should be more than before
    assert usdc_balance_after_close > usdc_balance_before_close, "USDC balance should increase after closing a position"


def test_close_position_full_size(chain_name, trading_manager, gmx_config_fork, usdc, web3_fork):
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

    # Get test wallet address
    wallet_address = gmx_config_fork.get_wallet_address()

    # First, create a position to close
    trading_manager.open_position(
        market_symbol=market_symbol,
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=False,
        size_delta_usd=size_delta,
        leverage=1.5,
        slippage_percent=0.003,
        debug_mode=False,
        execution_buffer=2.2,
    )

    # Small delay to allow the position to be processed
    web3_fork.provider.make_request("evm_increaseTime", [60])  # Advance time by 60 seconds
    web3_fork.provider.make_request("evm_mine", [])  # Mine a new block

    # Check USDC balance before closing position
    usdc_balance_before_close = usdc.contract.functions.balanceOf(wallet_address).call()

    # Close a full short position
    decrease_order = trading_manager.close_position(
        market_symbol=market_symbol,
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=False,
        size_delta_usd=size_delta,
        initial_collateral_delta=collateral_delta,
        slippage_percent=0.003,
        debug_mode=False,
        execution_buffer=2.2,
    )  # Full position size  # Full collateral

    # Verify the order was created with the right type
    assert isinstance(decrease_order, DecreaseOrder)

    # Verify the position being closed is short
    assert decrease_order.is_long is False

    # Verify debug mode
    assert decrease_order.debug_mode is False

    # Check USDC balance after closing position
    usdc_balance_after_close = usdc.contract.functions.balanceOf(wallet_address).call()

    # Verify USDC balance has increased after closing the position
    assert usdc_balance_after_close > usdc_balance_before_close, "USDC balance should increase after closing a full position"


def test_swap_tokens(chain_name, trading_manager, gmx_config_fork, usdc, wsol, wallet_with_usdc):
    """
    Test swapping tokens.

    This tests creating a SwapOrder.
    """
    start_token_symbol: str = "USDC"
    start_token_address = usdc.contract.functions.address
    # Select appropriate parameters based on the chain
    if chain_name == "arbitrum":
        out_token_symbol = "SOL"
        out_token_address = wsol.contract.functions.address
    # avalanche
    else:
        # For https://github.com/gmx-io/gmx-synthetics/issues/164 skip the test for avalanche
        pytest.skip("Skipping swap_tokens for avalanche because of the known issue in the Reader contract")
        out_token_symbol = "GMX"

    # Get test wallet address
    wallet_address = gmx_config_fork.get_wallet_address()

    # Check initial balances
    initial_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()

    # Swap USDC for chain-specific native token
    swap_order = trading_manager.swap_tokens(
        out_token_symbol=out_token_symbol,
        in_token_symbol=start_token_symbol,
        amount=50000.3785643,  # 50000 USDC tokens & fractions for fun
        slippage_percent=0.02,  # 0.2% slippage
        debug_mode=False,
        execution_buffer=2.5,  # this is needed to pass the gas usage
    )

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

    # Check final balances
    final_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()
    decimals = wsol.contract.functions.decimals().call()

    # Verify balances changed
    assert final_usdc_balance < initial_usdc_balance, "USDC balance should decrease after swap"

    emulate_keepers(
        gmx_config_fork,
        start_token_symbol,
        out_token_symbol,
        gmx_config_fork.web3,
        wallet_address,
        start_token_address,
        out_token_address,
    )

    output = wsol.contract.functions.balanceOf(wallet_address).call()

    # As of 21 May 2025, 50k ARB -> 122 SOL (Roughly). Keeping it at 100 just be safe.
    assert output // decimals >= 100


def test_swap_tokens_usdc_aave(chain_name, trading_manager, gmx_config_fork, usdc, aave, wallet_with_usdc):
    """
    Test swapping tokens.

    This tests creating a SwapOrder.
    """
    start_token_symbol: str = "USDC"
    start_token_address = usdc.contract.functions.address
    # Select appropriate parameters based on the chain
    if chain_name == "arbitrum":
        out_token_symbol = "AAVE"
        out_token_address = aave.contract.functions.address
    # avalanche
    else:
        # For https://github.com/gmx-io/gmx-synthetics/issues/164 skip the test for avalanche
        pytest.skip("Skipping swap_tokens for avalanche because of the known issue in the Reader contract")
        out_token_symbol = "GMX"

    # Get test wallet address
    wallet_address = gmx_config_fork.get_wallet_address()

    # Check initial balances
    initial_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()

    # Swap USDC for chain-specific native token
    swap_order = trading_manager.swap_tokens(
        out_token_symbol=out_token_symbol,
        in_token_symbol=start_token_symbol,
        amount=50000.3785643,  # 50000 ARB tokens & fractions for fun
        slippage_percent=0.02,  # 0.2% slippage
        debug_mode=False,
        execution_buffer=2.5,  # this is needed to pass the gas usage
    )

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

    # Check final balances
    final_usdc_balance = usdc.contract.functions.balanceOf(wallet_address).call()
    decimals = aave.contract.functions.decimals().call()

    # Verify balances changed
    assert final_usdc_balance < initial_usdc_balance, "USDC balance should decrease after swap"

    emulate_keepers(
        gmx_config_fork,
        start_token_symbol,
        out_token_symbol,
        gmx_config_fork.web3,
        wallet_address,
        start_token_address,
        out_token_address,
    )

    output = aave.contract.functions.balanceOf(wallet_address).call()

    # As of 21 May 2025, 50k ARB -> 122 SOL (Roughly). Keeping it at 100 just be safe.
    assert output // decimals >= 100
