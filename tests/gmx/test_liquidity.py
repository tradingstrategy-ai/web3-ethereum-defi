"""
Tests for GMXLiquidityManager with parametrized chain testing.

This test suite verifies the functionality of the GMXLiquidityManager class
when connected to different networks.
"""
import pytest
from gmx_python_sdk.scripts.v2.order.create_deposit_order import DepositOrder
import logging

# Suppress logging to keep test output clean
original_log_handlers = logging.getLogger().handlers[:]
for handler in original_log_handlers:
    logging.getLogger().removeHandler(handler)


def test_initialization(chain_name, gmx_config_fork):
    """
    Test that the liquidity manager initializes correctly with chain-specific config.
    """
    from eth_defi.gmx.liquidity import GMXLiquidityManager

    manager = GMXLiquidityManager(gmx_config_fork)
    assert manager.config == gmx_config_fork
    assert manager.config.get_chain().lower() == chain_name.lower()


def test_add_liquidity_native_token(chain_name, liquidity_manager, wallet_with_native_token):
    """
    Test adding liquidity to native token/USDC pool.

    This tests that the order is created correctly for the chain's native token.
    """
    # Select appropriate market parameters based on the chain
    if chain_name == "arbitrum":
        market_token_symbol = "ETH"
        long_token_symbol = "ETH"
    # avalanche
    else:
        market_token_symbol = "AVAX"
        long_token_symbol = "AVAX"

    # Add liquidity with native token as long token
    deposit_order = liquidity_manager.add_liquidity(market_token_symbol=market_token_symbol, long_token_symbol=long_token_symbol, short_token_symbol="USDC", long_token_usd=10, short_token_usd=0, debug_mode=False)

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


def test_add_liquidity_btc_usdc(chain_name, web3_fork, large_wbtc_holder, wbtc):
    """
    Test adding liquidity to BTC/USDC pool.

    This verifies that BTC markets work as well.
    """
    if chain_name == "avalanche":
        pytest.skip("Skipping BTC liquidity test on Avalanche due to SDK limitations")

    # Set up wallet and transfer WBTC
    anvil_private_key: str = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    address: str = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

    # Fund the user with WBTC
    wbtc.contract.functions.transfer(
        address,  # testing wallet address
        9 * 10**8,
    ).transact({"from": large_wbtc_holder})

    # Create config and liquidity manager
    from eth_defi.gmx.config import GMXConfig
    from eth_defi.gmx.liquidity import GMXLiquidityManager

    config = GMXConfig(web3_fork, chain=chain_name, private_key=anvil_private_key, user_wallet_address=address)
    liquidity_manager = GMXLiquidityManager(config)

    # Add liquidity to BTC/USDC market
    deposit_order = liquidity_manager.add_liquidity(market_token_symbol="BTC", long_token_symbol="BTC", short_token_symbol="USDC", long_token_usd=2, short_token_usd=0, debug_mode=False)

    # Verify the order was created with the right type
    assert isinstance(deposit_order, DepositOrder)

    # Verify the order has appropriate parameters
    assert hasattr(deposit_order, "market_key")
    assert hasattr(deposit_order, "initial_long_token")
    assert hasattr(deposit_order, "initial_short_token")

    # Verify debug mode
    assert deposit_order.debug_mode is False


def test_fail_parameter_validation(chain_name, liquidity_manager):
    """
    Test error handling with invalid parameters.

    This checks that appropriate errors are raised for invalid inputs.
    """
    # Test with invalid market token
    with pytest.raises(Exception):
        liquidity_manager.add_liquidity(market_token_symbol="INVALID_TOKEN", long_token_symbol="ETH", short_token_symbol="USDC", long_token_usd=100, short_token_usd=100, debug_mode=False)

    # Test with invalid token amounts (both zero)
    with pytest.raises(Exception):
        liquidity_manager.add_liquidity(market_token_symbol="ETH", long_token_symbol="ETH", short_token_symbol="USDC", long_token_usd=0, short_token_usd=0, debug_mode=False)


# Commented tests for future implementation when remove_liquidity issues are fixed:
"""
def test_remove_liquidity_to_long_token(chain_name, liquidity_manager):
    '''
    Test removing liquidity from a pool to the long token.
    '''
    # Select appropriate market parameters based on the chain
    if chain_name == "arbitrum":
        market_token_symbol = "ETH"
        out_token_symbol = "ETH"
    else:  # avalanche
        market_token_symbol = "AVAX"
        out_token_symbol = "AVAX"

    # First add liquidity
    deposit_order = liquidity_manager.add_liquidity(
        market_token_symbol=market_token_symbol,
        long_token_symbol=out_token_symbol,
        short_token_symbol="USDC",
        long_token_usd=5,
        short_token_usd=0,
        debug_mode=False
    )

    # Then remove liquidity
    withdraw_order = liquidity_manager.remove_liquidity(
        market_token_symbol=market_token_symbol,
        out_token_symbol=out_token_symbol,
        gm_amount=3,
        debug_mode=False
    )

    # Verify the order was created with the right type
    from gmx_python_sdk.scripts.v2.order.create_withdrawal_order import WithdrawOrder
    assert isinstance(withdraw_order, WithdrawOrder)

    # Verify key properties of the order
    assert hasattr(withdraw_order, "config")
    assert hasattr(withdraw_order, "market_key")
    assert hasattr(withdraw_order, "out_token")
    assert hasattr(withdraw_order, "gm_amount")

    # Verify the order has our debug flag
    assert hasattr(withdraw_order, "debug_mode")
    assert withdraw_order.debug_mode is False
"""
