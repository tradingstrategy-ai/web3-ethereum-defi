"""
Tests for GMXTrading on Arbitrum mainnet fork.

These tests verify GMX trading functionality on Arbitrum mainnet fork with mock oracle.
All tests run on an Anvil fork with a mock oracle to enable testing without live price feeds.

Required Environment Variables:
- ARBITRUM_JSON_RPC_URL: Arbitrum mainnet RPC endpoint for forking

All fixtures are defined in conftest.py:
- arbitrum_fork_config: GMX config for mainnet fork
- trading_manager_fork: GMXTrading instance
- position_verifier_fork: GetOpenPositions instance
- web3_arbitrum_fork: Web3 instance with mock oracle setup
"""

from eth_defi.gmx.order.base_order import OrderResult
from eth_defi.gmx.trading import GMXTrading


def test_initialization(arbitrum_fork_config):
    """Test that the trading module initializes correctly on Arbitrum mainnet fork."""
    trading = GMXTrading(arbitrum_fork_config)
    assert trading.config == arbitrum_fork_config
    assert trading.config.get_chain().lower() == "arbitrum"


def test_create_long_position_order(trading_manager_fork):
    """
    Test creating a long position order (without executing).

    This verifies OrderResult is returned with proper structure.
    Uses ETH market with ETH collateral (available on Arbitrum mainnet).
    """
    # Create order using ETH market and ETH collateral
    order_result = trading_manager_fork.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.005,
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


def test_create_short_position_order(trading_manager_fork):
    """
    Test creating a short position order (without executing).

    This verifies OrderResult is returned for short positions.
    Uses ETH market with USDC collateral (available on Arbitrum mainnet).
    """
    # Create short order using ETH market and USDC collateral
    order_result = trading_manager_fork.open_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=False,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.005,
        execution_buffer=2.2,
    )

    # Verify OrderResult structure
    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"
    assert hasattr(order_result, "transaction"), "OrderResult should have transaction"
    assert hasattr(order_result, "execution_fee"), "OrderResult should have execution_fee"


def test_get_open_positions(position_verifier_fork, arbitrum_fork_config):
    """
    Test fetching open positions.

    This verifies GetOpenPositions works on Arbitrum mainnet fork.
    """
    wallet_address = arbitrum_fork_config.get_wallet_address()

    # Fetch open positions (may be empty)
    open_positions = position_verifier_fork.get_data(wallet_address)

    # Should return a dict (even if empty)
    assert isinstance(open_positions, dict), "Should return dict of positions"


def test_create_swap_order(trading_manager_fork):
    """
    Test creating a swap order (without executing).

    This verifies swap orders return proper OrderResult.
    Uses USDC → ETH swap (tokens available on Arbitrum mainnet).
    """
    # Create swap order using USDC → ETH
    order_result = trading_manager_fork.swap_tokens(
        out_token_symbol="ETH",
        in_token_symbol="USDC",
        amount=5,
        slippage_percent=0.02,
        execution_buffer=2.5,
    )

    # Verify OrderResult structure
    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"
    assert hasattr(order_result, "transaction"), "OrderResult should have transaction"
    assert hasattr(order_result, "execution_fee"), "OrderResult should have execution_fee"
