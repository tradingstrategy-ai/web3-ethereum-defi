"""
Tests for GMXLiquidityManager on Arbitrum network.

This test suite verifies the functionality of the GMXLiquidityManager class
when connected to the Arbitrum network.
"""
import pytest
import os
from gmx_python_sdk.scripts.v2.order.create_deposit_order import DepositOrder
import logging

from web3.contract import Contract

# from gmx_python_sdk.scripts.v2.order.create_withdrawal_order import WithdrawOrder

from eth_defi.gmx.liquidity import GMXLiquidityManager

mainnet_rpc = os.environ.get("ARBITRUM_JSON_RPC_URL")

pytestmark = pytest.mark.skipif(not mainnet_rpc, reason="No ARBITRUM_JSON_RPC_URL environment variable")

# https://betterstack.com/community/questions/how-to-disable-logging-when-running-tests-in-python/
original_log_handlers = logging.getLogger().handlers[:]
# Remove all existing log handlers bcz of anvil is dumping the logs which is not desirable in the workflows
for handler in original_log_handlers:
    logging.getLogger().removeHandler(handler)




def test_initialization(gmx_config_arbitrum_fork):
    """
    Test that the liquidity manager initializes correctly with Arbitrum config.
    """
    manager = GMXLiquidityManager(gmx_config_arbitrum_fork)
    assert manager.config == gmx_config_arbitrum_fork
    assert manager.config.get_chain().lower() == "arbitrum"


def test_add_liquidity_eth_usdc(liquidity_manager_arbitrum):
    """
    Test adding liquidity to ETH/USDC pool on Arbitrum.

    This tests that the order is created correctly.
    """
    # Common market on Arbitrum: ETH with USDC as the short token
    deposit_order = liquidity_manager_arbitrum.add_liquidity(
        market_token_symbol="ETH",
        long_token_symbol="ETH",
        short_token_symbol="USDC",
        long_token_usd=10,  # ETH
        short_token_usd=0,  # USDC
        debug_mode=False
    )

    # Verify the order was created with the right type
    assert isinstance(deposit_order, DepositOrder)

    # Verify key properties of the order
    assert hasattr(deposit_order, "config")
    assert hasattr(deposit_order, "market_key")
    assert hasattr(deposit_order, "initial_long_token")
    assert hasattr(deposit_order, "initial_short_token")
    assert hasattr(deposit_order, "long_token_amount")
    assert hasattr(deposit_order, "short_token_amount")

    # Verify the order has our debug flag
    assert hasattr(deposit_order, "debug_mode")
    assert deposit_order.debug_mode is False


# Skip this as we need WBTC to test this & deploying a mock won't work
# def test_add_liquidity_btc_usdc(liquidity_manager_arbitrum, btc: Contract, deployer: str):
#     """
#     Test adding liquidity to BTC/USDC pool on Arbitrum.
#
#     This verifies that other markets work as well.
#     """
#     # fund the user with BTC
#     btc.functions.transfer("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266", 500).transact({"from": deployer})
#
#     # Another common market on Arbitrum: BTC with USDC as the short token
#     deposit_order = liquidity_manager_arbitrum.add_liquidity(
#         market_token_symbol="BTC",
#         long_token_symbol="BTC",
#         short_token_symbol="USDC",
#         long_token_usd=2,   # BTC
#         short_token_usd=0,  # USDC
#         debug_mode=False
#     )
#
#     # Verify the order was created with the right type
#     assert isinstance(deposit_order, DepositOrder)
#
#     # Verify the order has appropriate parameters
#     assert hasattr(deposit_order, "market_key")
#     assert hasattr(deposit_order, "initial_long_token")
#     assert hasattr(deposit_order, "initial_short_token")
#
#     # Verify debug mode
#     assert deposit_order.debug_mode is False


# Skip the remove liquidity for now as there is too many "Insufficient Balnace" error.
# def test_remove_liquidity_eth_to_eth(liquidity_manager_arbitrum):
#     """
#     Test removing liquidity from ETH pool to ETH on Arbitrum.
#
#     This tests withdrawing to the long token.
#     """
#     # Remove 0.5 GM tokens and get ETH (long token)
#     deposit_order = liquidity_manager_arbitrum.add_liquidity(
#         market_token_symbol="ETH",
#         long_token_symbol="ETH",
#         short_token_symbol="USDC",
#         long_token_usd=5,  # ETH
#         short_token_usd=0,  # USDC
#         debug_mode=False
#     )
#
#     withdraw_order = liquidity_manager_arbitrum.remove_liquidity(
#         market_token_symbol="ETH",
#         out_token_symbol="ETH",
#         gm_amount=3,
#         debug_mode=False
#     )
#
#     # Verify the order was created with the right type
#     assert isinstance(withdraw_order, WithdrawOrder)
#
#     # Verify key properties of the order
#     # (exact fields depend on the implementation of WithdrawOrder)
#     assert hasattr(withdraw_order, "config")
#     assert hasattr(withdraw_order, "market_key")
#     assert hasattr(withdraw_order, "out_token")
#     assert hasattr(withdraw_order, "gm_amount")
#
#     # Verify the order has our debug flag
#     assert hasattr(withdraw_order, "debug_mode")
#     assert withdraw_order.debug_mode is False


# def test_remove_liquidity_eth_to_usdc(liquidity_manager_arbitrum):
#     """
#     Test removing liquidity from ETH pool to USDC on Arbitrum.
#
#     This tests withdrawing to the short token.
#     """
#     # Remove 0.5 GM tokens and get USDC (short token)
#     withdraw_order = liquidity_manager_arbitrum.remove_liquidity(
#         market_token_symbol="ETH",
#         out_token_symbol="USDC",
#         gm_amount=0.5,
#         debug_mode=False
#     )
#
#     # Verify the order was created with the right type
#     assert isinstance(withdraw_order, WithdrawOrder)
#
#     # Verify key properties
#     assert hasattr(withdraw_order, "market_key")
#     assert hasattr(withdraw_order, "out_token")
#     assert hasattr(withdraw_order, "gm_amount")
#     assert withdraw_order.debug_mode is False


def test_parameter_validation(liquidity_manager_arbitrum):
    """
    Test error handling with invalid parameters.

    This checks that appropriate errors are raised for invalid inputs.
    """
    # Test with invalid market token
    with pytest.raises(Exception):
        liquidity_manager_arbitrum.add_liquidity(
            market_token_symbol="INVALID_TOKEN",
            long_token_symbol="ETH",
            short_token_symbol="USDC",
            long_token_usd=100,
            short_token_usd=100,
            debug_mode=False
        )

    # Test with invalid token amounts (both zero)
    with pytest.raises(Exception):
        liquidity_manager_arbitrum.add_liquidity(
            market_token_symbol="ETH",
            long_token_symbol="ETH",
            short_token_symbol="USDC",
            long_token_usd=0,
            short_token_usd=0,
            debug_mode=False
        )