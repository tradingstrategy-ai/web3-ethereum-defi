"""GMX CCXT Stop Loss and Take Profit Order Tests.

Tests CCXT-compatible SL/TP order creation on Arbitrum mainnet fork.
Demonstrates bundled position opening with stop-loss and take-profit orders.
"""

from flaky import flaky

from eth_defi.gmx.ccxt.exchange import GMX
from tests.gmx.ccxt.test_ccxt_trading import _execute_order


@flaky(max_runs=3, min_passes=1)
def test_ccxt_long_with_stop_loss(
    ccxt_gmx_fork_open_close: GMX,
    web3_arbitrum_fork_ccxt_long,
    execution_buffer: int,
):
    """Test opening a long position with stop loss via CCXT interface.

    Demonstrates CCXT unified API for creating a position with stop loss protection.
    Uses percentage-based trigger (5% below entry price).
    """
    gmx = ccxt_gmx_fork_open_close
    web3 = web3_arbitrum_fork_ccxt_long

    symbol = "ETH/USDC:USDC"
    leverage = 2.5
    size_usd = 10.0
    stop_loss_percent = 0.05

    # Open long position with stop loss
    order = gmx.create_market_buy_order(
        symbol,
        size_usd,
        {
            "leverage": leverage,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
            "stopLoss": {
                "triggerPercent": stop_loss_percent,  # 5% below entry
                "closePercent": 1.0,  # Close 100% of position
            },
        },
    )

    assert order is not None
    assert order.get("id") is not None
    assert order.get("symbol") == symbol
    assert order.get("side") == "buy"

    tx_hash = order.get("info", {}).get("tx_hash") or order.get("id")
    assert tx_hash is not None, "Order should have transaction hash"

    _execute_order(web3, tx_hash)

    # Verify position exists
    positions = gmx.fetch_positions([symbol])
    assert len(positions) > 0, "Should have at least one position after opening"

    position = positions[0]
    assert position.get("symbol") == symbol
    assert position.get("side") == "long"
    assert position.get("contracts", 0) > 0
    assert position.get("notional", 0) > 0

    # Verify SL/TP details in order info
    info = order.get("info", {})
    assert info.get("stop_loss_trigger_price") is not None, "Stop loss trigger price should be set"
    assert info.get("stop_loss_fee", 0) > 0, "Stop loss fee should be > 0"


@flaky(max_runs=3, min_passes=1)
def test_ccxt_long_with_take_profit(
    ccxt_gmx_fork_open_close: GMX,
    web3_arbitrum_fork_ccxt_long,
    execution_buffer: int,
):
    """Test opening a long position with take profit via CCXT interface.

    Demonstrates CCXT unified API for creating a position with take profit target.
    Uses percentage-based trigger (10% above entry price).
    """
    gmx = ccxt_gmx_fork_open_close
    web3 = web3_arbitrum_fork_ccxt_long

    symbol = "ETH/USDC:USDC"
    leverage = 2.5
    size_usd = 10.0
    take_profit_percent = 0.10

    # Open long position with take profit
    order = gmx.create_market_buy_order(
        symbol,
        size_usd,
        {
            "leverage": leverage,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
            "takeProfit": {
                "triggerPercent": take_profit_percent,  # 10% above entry
                "closePercent": 1.0,  # Close 100% of position
            },
        },
    )

    assert order is not None
    assert order.get("id") is not None
    assert order.get("symbol") == symbol
    assert order.get("side") == "buy"

    tx_hash = order.get("info", {}).get("tx_hash") or order.get("id")
    assert tx_hash is not None, "Order should have transaction hash"

    _execute_order(web3, tx_hash)

    # Verify position exists
    positions = gmx.fetch_positions([symbol])
    assert len(positions) > 0, "Should have at least one position after opening"

    position = positions[0]
    assert position.get("symbol") == symbol
    assert position.get("side") == "long"
    assert position.get("contracts", 0) > 0
    assert position.get("notional", 0) > 0

    # Verify SL/TP details in order info
    info = order.get("info", {})
    assert info.get("take_profit_trigger_price") is not None, "Take profit trigger price should be set"
    assert info.get("take_profit_fee", 0) > 0, "Take profit fee should be > 0"


@flaky(max_runs=3, min_passes=1)
def test_ccxt_sltp_uses_correct_market(
    ccxt_gmx_fork_open_close: GMX,
    web3_arbitrum_fork_ccxt_long,
    execution_buffer: int,
):
    """Test that SLTP orders use the correct ETH market, not wstETH market.

    Regression test for bug where ETH/USDC:USDC with ETH collateral was
    incorrectly using wstETH market (0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5)
    instead of ETH market (0x70d95587d40A2caf56bd97485aB3Eec10Bee6336).

    This test verifies the fix that makes SLTP orders use Core Markets module
    for market resolution, same as normal orders.
    """
    gmx = ccxt_gmx_fork_open_close
    web3 = web3_arbitrum_fork_ccxt_long

    # Expected market addresses
    ETH_MARKET = "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336".lower()
    WSTETH_MARKET = "0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5".lower()

    symbol = "ETH/USDC:USDC"
    leverage = 2.5
    size_usd = 10.0

    # Create order with stop loss
    order = gmx.create_market_buy_order(
        symbol,
        size_usd,
        {
            "leverage": leverage,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
            "stopLoss": {
                "triggerPercent": 0.05,
                "closePercent": 1.0,
            },
        },
    )

    assert order is not None
    assert order.get("id") is not None

    # Verify market resolution via transaction
    # The transaction should be created with ETH market, not wstETH market
    # We can verify this by checking the order was created successfully
    # (if wrong market was used, the order would fail with invalid oracle params)
    tx_hash = order.get("info", {}).get("tx_hash") or order.get("id")
    assert tx_hash is not None, "Order should have transaction hash"

    # Execute order - if wrong market was used, this would fail
    _execute_order(web3, tx_hash)

    # Verify position was created successfully
    # This proves the correct market was used
    positions = gmx.fetch_positions([symbol])
    assert len(positions) > 0, "Position should be created with correct market"

    position = positions[0]
    assert position.get("symbol") == symbol
    assert position.get("side") == "long"
    assert position.get("contracts", 0) > 0

    # Additional verification: check that the order info contains valid prices
    # (invalid market would result in zero or None prices)
    info = order.get("info", {})
    entry_price = info.get("entry_price")
    sl_trigger = info.get("stop_loss_trigger_price")

    assert entry_price is not None and entry_price > 0, "Entry price should be valid (non-zero)"
    assert sl_trigger is not None and sl_trigger > 0, "SL trigger price should be valid (non-zero)"
    assert sl_trigger < entry_price, "Stop loss should be below entry price for long position"


@flaky(max_runs=3, min_passes=1)
def test_ccxt_sltp_graphql_mode(
    ccxt_gmx_fork_graphql: GMX,
    web3_arbitrum_fork_ccxt_long,
    execution_buffer: int,
):
    """Test that SLTP orders work correctly with GraphQL market loading.

    Verifies that the GraphQL fix correctly separates ETH and wstETH markets,
    allowing SLTP orders to work without making RPC calls to Core Markets module.
    """
    gmx = ccxt_gmx_fork_graphql
    web3 = web3_arbitrum_fork_ccxt_long

    # Verify we're using GraphQL loading (markets should already be loaded)
    assert gmx.markets_loaded, "Markets should be loaded"
    assert len(gmx.markets) > 0, "Should have markets loaded"

    # Verify both ETH and wstETH markets are correctly separated
    assert "ETH/USDC:USDC" in gmx.markets, "ETH market should exist"
    assert "wstETH/USDC:USDC" in gmx.markets, "wstETH market should exist"

    eth_market = gmx.markets["ETH/USDC:USDC"]
    wsteth_market = gmx.markets["wstETH/USDC:USDC"]

    # Verify they map to different market addresses
    assert eth_market["info"]["market_token"].lower() == "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336".lower(), "ETH market address incorrect"
    assert wsteth_market["info"]["market_token"].lower() == "0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5".lower(), "wstETH market address incorrect"

    # Now test that SLTP orders work with GraphQL-loaded markets
    symbol = "ETH/USDC:USDC"
    leverage = 2.5
    size_usd = 10.0

    order = gmx.create_market_buy_order(
        symbol,
        size_usd,
        {
            "leverage": leverage,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
            "stopLoss": {
                "triggerPercent": 0.05,
                "closePercent": 1.0,
            },
            "takeProfit": {
                "triggerPercent": 0.10,
                "closePercent": 1.0,
            },
        },
    )

    assert order is not None
    assert order.get("id") is not None

    tx_hash = order.get("info", {}).get("tx_hash") or order.get("id")
    assert tx_hash is not None

    # Execute and verify
    _execute_order(web3, tx_hash)

    positions = gmx.fetch_positions([symbol])
    assert len(positions) > 0, "Position should be created with GraphQL-loaded markets"

    # Verify both SL and TP were created
    info = order.get("info", {})
    assert info.get("stop_loss_trigger_price") is not None, "SL should be set"
    assert info.get("take_profit_trigger_price") is not None, "TP should be set"
