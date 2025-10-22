"""
Tests for GMXTrading on Arbitrum Sepolia testnet.

These tests verify GMX trading functionality on Arbitrum Sepolia testnet.
All tests run in debug mode to test functionality without requiring pre-funded wallets.

Required Environment Variables:
- PRIVATE_KEY: Your wallet private key
- ARBITRUM_SEPOLIA_RPC_URL: Arbitrum Sepolia RPC endpoint

All fixtures are defined in conftest.py:
- arbitrum_sepolia_config: GMX config for Sepolia
- trading_manager_sepolia: GMXTrading instance
- position_verifier_sepolia: GetOpenPositions instance
"""

from eth_defi.gmx.order.base_order import OrderResult
from eth_defi.gmx.trading import GMXTrading


def test_initialization(arbitrum_sepolia_config):
    """Test that the trading module initializes correctly on Arbitrum Sepolia."""
    trading = GMXTrading(arbitrum_sepolia_config)
    assert trading.config == arbitrum_sepolia_config
    assert trading.config.get_chain().lower() == "arbitrum_sepolia"


def test_create_long_position_order(trading_manager_sepolia):
    """
    Test creating a long position order (without executing).

    This verifies OrderResult is returned with proper structure.
    Uses CRV market with USDC.SG collateral (available on Arbitrum Sepolia).
    """
    # Create order (doesn't execute without signing)
    # Using CRV market and USDC.SG as these are available on Arbitrum Sepolia testnet
    order_result = trading_manager_sepolia.open_position(
        market_symbol="CRV",
        collateral_symbol="USDC.SG",
        start_token_symbol="USDC.SG",
        is_long=True,
        size_delta_usd=10,
        leverage=1,
        slippage_percent=0.003,
        execution_buffer=2.2,
    )

    # Verify OrderResult structure
    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"
    assert hasattr(order_result, "transaction"), "OrderResult should have transaction"
    assert hasattr(order_result, "execution_fee"), "OrderResult should have execution_fee"
    assert hasattr(order_result, "acceptable_price"), "OrderResult should have acceptable_price"
    assert hasattr(order_result, "mark_price"), "OrderResult should have mark_price"
    assert hasattr(order_result, "gas_limit"), "OrderResult should have gas_limit"

    # Verify transaction structure
    assert "from" in order_result.transaction, "Transaction should have 'from' field"
    assert "to" in order_result.transaction, "Transaction should have 'to' field"
    assert "data" in order_result.transaction, "Transaction should have 'data' field"


def test_create_short_position_order(trading_manager_sepolia):
    """
    Test creating a short position order (without executing).

    This verifies OrderResult is returned for short positions.
    Uses CRV market with USDC.SG collateral (available on Arbitrum Sepolia).
    """
    # Create order (doesn't execute without signing)
    # Using CRV market and USDC.SG as these are available on Arbitrum Sepolia testnet
    order_result = trading_manager_sepolia.open_position(
        market_symbol="CRV",
        collateral_symbol="USDC.SG",
        start_token_symbol="USDC.SG",
        is_long=False,
        size_delta_usd=10,
        leverage=1,
        slippage_percent=0.003,
        execution_buffer=2.2,
    )

    # Verify OrderResult structure
    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"
    assert hasattr(order_result, "transaction"), "OrderResult should have transaction"
    assert hasattr(order_result, "execution_fee"), "OrderResult should have execution_fee"


def test_get_open_positions(position_verifier_sepolia, arbitrum_sepolia_config):
    """
    Test fetching open positions.

    This verifies GetOpenPositions works on Sepolia.
    """
    wallet_address = arbitrum_sepolia_config.get_wallet_address()

    # Fetch open positions (may be empty)
    open_positions = position_verifier_sepolia.get_data(wallet_address)

    # Should return a dict (even if empty)
    assert isinstance(open_positions, dict), "Should return dict of positions"


def test_create_swap_order(trading_manager_sepolia):
    """
    Test creating a swap order (without executing).

    This verifies swap orders return proper OrderResult.
    Uses USDC.SG → BTC swap (tokens available on Arbitrum Sepolia).
    """
    # Create swap order (doesn't execute without signing)
    # Using USDC.SG → BTC as these tokens are available on Arbitrum Sepolia testnet
    order_result = trading_manager_sepolia.swap_tokens(
        out_token_symbol="BTC",
        in_token_symbol="USDC.SG",
        amount=5,
        slippage_percent=0.02,
        execution_buffer=2.5,
    )

    # Verify OrderResult structure
    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"
    assert hasattr(order_result, "transaction"), "OrderResult should have transaction"
    assert hasattr(order_result, "execution_fee"), "OrderResult should have execution_fee"
