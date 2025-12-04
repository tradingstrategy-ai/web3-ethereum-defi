"""Test GMX CCXT trading methods.

Tests order creation and execution via CCXT adapter.
"""

import pytest
from ccxt.base.errors import NotSupported

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.core import GetOpenPositions
from tests.gmx.fork_helpers import execute_order_as_keeper, extract_order_key_from_receipt


def test_create_market_buy_order(arbitrum_fork_config, test_wallet):
    """Test opening a long position with create_market_buy_order.

    - Creates market buy order for ETH/USD
    - Executes order as keeper
    - Verifies position was created
    """
    # Initialize GMX with wallet for trading
    gmx = GMX(config=arbitrum_fork_config, wallet=test_wallet)
    gmx.load_markets()

    # Create market buy order
    order = gmx.create_market_buy_order(
        "ETH/USDC",
        10.0,  # $10 position size
        {
            "leverage": 2.5,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": 2.2,
        },
    )

    # Verify order structure
    assert isinstance(order, dict)
    assert order["status"] == "open"
    assert order["id"] is not None
    assert order["symbol"] == "ETH/USDC"
    assert order["side"] == "buy"
    assert order["amount"] == 10.0
    assert "fee" in order
    assert order["fee"]["cost"] > 0

    # Verify transaction info
    assert "info" in order
    assert "tx_hash" in order["info"]
    assert "receipt" in order["info"]
    assert "execution_fee" in order["info"]
    assert order["info"]["receipt"]["status"] == 1

    # Execute order as keeper
    order_key = extract_order_key_from_receipt(order["info"]["receipt"])
    exec_receipt, keeper_address = execute_order_as_keeper(
        arbitrum_fork_config.web3,
        order_key,
    )
    assert exec_receipt["status"] == 1

    # Verify position was created
    position_verifier = GetOpenPositions(arbitrum_fork_config)
    open_positions = position_verifier.get_data(test_wallet.address)

    assert len(open_positions) > 0
    position = list(open_positions.values())[0]
    assert position["market_symbol"] == "ETH"
    assert position["is_long"] is True
    assert position["leverage"] > 0


def test_create_market_sell_order(arbitrum_fork_config, test_wallet):
    """Test closing a long position with create_market_sell_order.

    - Opens a long position first
    - Closes it with market sell order
    - Verifies position was closed
    """
    # Initialize GMX with wallet
    gmx = GMX(config=arbitrum_fork_config, wallet=test_wallet)
    gmx.load_markets()

    # First, open a long position
    buy_order = gmx.create_market_buy_order(
        "ETH/USDC",
        10.0,
        {
            "leverage": 2.5,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": 2.2,
        },
    )

    # Execute buy order
    buy_order_key = extract_order_key_from_receipt(buy_order["info"]["receipt"])
    exec_receipt, _ = execute_order_as_keeper(arbitrum_fork_config.web3, buy_order_key)
    assert exec_receipt["status"] == 1

    # Verify position exists
    position_verifier = GetOpenPositions(arbitrum_fork_config)
    positions_before = position_verifier.get_data(test_wallet.address)
    assert len(positions_before) > 0

    # Now close the position with market sell
    sell_order = gmx.create_market_sell_order(
        "ETH/USDC",
        10.0,
        {
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": 2.2,
        },
    )

    # Verify sell order structure
    assert isinstance(sell_order, dict)
    assert sell_order["status"] == "open"
    assert sell_order["symbol"] == "ETH/USDC"
    assert sell_order["side"] == "sell"
    assert sell_order["amount"] == 10.0
    assert sell_order["info"]["receipt"]["status"] == 1

    # Execute sell order
    sell_order_key = extract_order_key_from_receipt(sell_order["info"]["receipt"])
    exec_receipt, _ = execute_order_as_keeper(arbitrum_fork_config.web3, sell_order_key)
    assert exec_receipt["status"] == 1

    # Verify position was closed
    positions_after = position_verifier.get_data(test_wallet.address)
    assert len(positions_after) == 0


def test_create_order(arbitrum_fork_config, test_wallet):
    """Test generic create_order method with custom parameters.

    - Uses create_order directly instead of convenience methods
    - Tests parameter conversion
    - Verifies order execution
    """
    gmx = GMX(config=arbitrum_fork_config, wallet=test_wallet)
    gmx.load_markets()

    # Create order using generic method
    order = gmx.create_order(
        symbol="ETH/USDC",
        type="market",
        side="buy",
        amount=10.0,
        price=None,
        params={
            "leverage": 3.0,
            "collateral_symbol": "USDC",
            "slippage_percent": 0.01,
            "execution_buffer": 2.5,
        },
    )

    # Verify order structure
    assert isinstance(order, dict)
    assert order["status"] == "open"
    assert order["symbol"] == "ETH/USDC"
    assert order["type"] == "market"
    assert order["side"] == "buy"
    assert order["amount"] == 10.0
    assert order["info"]["receipt"]["status"] == 1

    # Execute and verify position
    order_key = extract_order_key_from_receipt(order["info"]["receipt"])
    execute_order_as_keeper(arbitrum_fork_config.web3, order_key)

    position_verifier = GetOpenPositions(arbitrum_fork_config)
    positions = position_verifier.get_data(test_wallet.address)
    assert len(positions) > 0


def test_create_order_without_wallet(arbitrum_fork_config):
    """Test that order creation fails without wallet.

    - Initializes GMX without wallet
    - Verifies ValueError is raised
    """
    gmx = GMX(config=arbitrum_fork_config)  # No wallet
    gmx.load_markets()

    with pytest.raises(ValueError, match="Wallet required for order creation"):
        gmx.create_market_buy_order("ETH/USDC", 10.0)


def test_cancel_order_not_supported(arbitrum_fork_config, test_wallet):
    """Test that cancel_order raises NotSupported.

    GMX orders execute immediately and cannot be cancelled.
    """
    gmx = GMX(config=arbitrum_fork_config, wallet=test_wallet)
    gmx.load_markets()

    with pytest.raises(NotSupported, match="cannot be cancelled"):
        gmx.cancel_order("0x123")


def test_fetch_order_not_supported(arbitrum_fork_config, test_wallet):
    """Test that fetch_order raises NotSupported.

    GMX orders execute immediately, use fetch_positions instead.
    """
    gmx = GMX(config=arbitrum_fork_config, wallet=test_wallet)
    gmx.load_markets()

    with pytest.raises(NotSupported, match="execute immediately"):
        gmx.fetch_order("0x123")
