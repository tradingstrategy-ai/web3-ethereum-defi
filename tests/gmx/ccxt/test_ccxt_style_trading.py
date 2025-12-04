"""Test GMX CCXT-style trading with new initialization.

Tests order creation and position management using CCXT-style initialization.
This ensures the new initialization pattern works correctly for actual trading.
"""

import pytest

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.core import GetOpenPositions
from tests.gmx.fork_helpers import execute_order_as_keeper, extract_order_key_from_receipt


def test_ccxt_style_create_market_buy_order(arbitrum_fork_config, test_wallet):
    """Test opening a long position with CCXT-style initialization.

    - Initializes GMX with CCXT-style parameters
    - Creates market buy order for ETH/USD
    - Executes order as keeper
    - Verifies position was created
    """
    # Get RPC URL from config
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri
    private_key_hex = "0x" + test_wallet.private_key.hex()

    # Initialize GMX with CCXT-style parameters
    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "privateKey": private_key_hex,
        }
    )

    # Load markets
    gmx.load_markets()
    assert len(gmx.markets) > 0

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


def test_ccxt_style_create_market_sell_order(arbitrum_fork_config, test_wallet):
    """Test closing a long position with CCXT-style initialization.

    - Initializes GMX with CCXT-style parameters
    - Opens a long position first
    - Closes it with market sell order
    - Verifies position was closed
    """
    # Get RPC URL from config
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri
    private_key_hex = "0x" + test_wallet.private_key.hex()

    # Initialize GMX with CCXT-style parameters
    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "privateKey": private_key_hex,
        }
    )

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


def test_ccxt_style_create_order_generic(arbitrum_fork_config, test_wallet):
    """Test generic create_order method with CCXT-style init.

    - Uses CCXT-style initialization
    - Uses create_order directly instead of convenience methods
    - Tests parameter conversion
    - Verifies order execution
    """
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri
    private_key_hex = "0x" + test_wallet.private_key.hex()

    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "privateKey": private_key_hex,
        }
    )

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


def test_ccxt_style_open_and_close_complete_flow(arbitrum_fork_config, test_wallet):
    """Test complete open and close flow with CCXT-style init.

    - Opens position with CCXT-style init
    - Verifies position is open
    - Closes position completely
    - Verifies position is closed
    """
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri
    private_key_hex = "0x" + test_wallet.private_key.hex()

    # Initialize once and reuse
    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "privateKey": private_key_hex,
        }
    )

    gmx.load_markets()

    # Open position
    buy_order = gmx.create_market_buy_order(
        "ETH/USDC",
        15.0,  # $15 position
        {
            "leverage": 3.0,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": 2.2,
        },
    )

    # Execute buy order
    buy_order_key = extract_order_key_from_receipt(buy_order["info"]["receipt"])
    buy_exec_receipt, _ = execute_order_as_keeper(arbitrum_fork_config.web3, buy_order_key)
    assert buy_exec_receipt["status"] == 1

    # Verify position is open
    position_verifier = GetOpenPositions(arbitrum_fork_config)
    positions_after_open = position_verifier.get_data(test_wallet.address)
    assert len(positions_after_open) == 1

    position = list(positions_after_open.values())[0]
    assert position["market_symbol"] == "ETH"
    assert position["is_long"] is True

    # Close position
    sell_order = gmx.create_market_sell_order(
        "ETH/USDC",
        15.0,  # Close full position
        {
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": 2.2,
        },
    )

    # Execute sell order
    sell_order_key = extract_order_key_from_receipt(sell_order["info"]["receipt"])
    sell_exec_receipt, _ = execute_order_as_keeper(arbitrum_fork_config.web3, sell_order_key)
    assert sell_exec_receipt["status"] == 1

    # Verify position is closed
    positions_after_close = position_verifier.get_data(test_wallet.address)
    assert len(positions_after_close) == 0


def test_ccxt_style_with_wallet_object(arbitrum_fork_config, test_wallet):
    """Test CCXT-style init with wallet object instead of privateKey.

    - Uses wallet object in parameters
    - Creates and executes orders
    - Verifies everything works with wallet object
    """
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri

    # Initialize with wallet object instead of privateKey
    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "wallet": test_wallet,
        }
    )

    gmx.load_markets()

    # Create order
    order = gmx.create_market_buy_order(
        "ETH/USDC",
        10.0,
        {
            "leverage": 2.5,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": 2.2,
        },
    )

    # Verify order was created
    assert isinstance(order, dict)
    assert order["status"] == "open"
    assert order["info"]["receipt"]["status"] == 1

    # Execute order
    order_key = extract_order_key_from_receipt(order["info"]["receipt"])
    exec_receipt, _ = execute_order_as_keeper(arbitrum_fork_config.web3, order_key)
    assert exec_receipt["status"] == 1


def test_ccxt_style_view_only_mode_fails_on_orders(arbitrum_fork_config):
    """Test that view-only mode (no wallet) fails when creating orders.

    - Initializes without privateKey or wallet
    - Verifies order creation raises appropriate error
    """
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri

    # Initialize in view-only mode (no wallet)
    gmx = GMX(
        {
            "rpcUrl": rpc_url,
        }
    )

    gmx.load_markets()

    # Verify order creation fails with clear message
    with pytest.raises(ValueError, match="VIEW-ONLY mode"):
        gmx.create_market_buy_order("ETH/USDC", 10.0)
