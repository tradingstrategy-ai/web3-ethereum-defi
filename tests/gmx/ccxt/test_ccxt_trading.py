"""GMX CCXT Trading Tests - Open and Close Position.

Tests CCXT-compatible trading methods on Arbitrum mainnet fork.
Follows the same workflow as debug_ccxt.py for reliability.
"""

import time

import pytest

from eth_defi.gmx.ccxt.exchange import GMX
from tests.gmx.fork_helpers import execute_order_as_keeper, extract_order_key_from_receipt, fetch_on_chain_oracle_prices, setup_mock_oracle


def _execute_order(web3, tx_hash):
    """Helper to execute an order: wait for receipt, extract key, execute as keeper."""
    if isinstance(tx_hash, str):
        tx_hash_bytes = bytes.fromhex(tx_hash[2:]) if tx_hash.startswith("0x") else bytes.fromhex(tx_hash)
    else:
        tx_hash_bytes = tx_hash

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash_bytes)
    assert receipt["status"] == 1, "Order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"

    exec_receipt, _ = execute_order_as_keeper(web3, order_key)
    assert exec_receipt["status"] == 1, "Keeper execution should succeed"

    return exec_receipt


@pytest.mark.slow
def test_open_long_and_short_then_close_both(
    ccxt_gmx_fork_open_close: GMX,
    web3_arbitrum_fork_ccxt,
    execution_buffer: int,
):
    """Test opening both long and short positions, then closing both.

    This test follows the debug_ccxt.py workflow:
    1. Create market buy order (open long)
    2. Execute order as keeper
    3. Create market sell order (open short with USDC collateral)
    4. Execute order as keeper
    5. Verify both positions exist
    6. Close long position
    7. Close short position
    8. Verify all positions are closed
    """
    gmx = ccxt_gmx_fork_open_close
    web3 = web3_arbitrum_fork_ccxt

    # Test parameters
    symbol = "ETH/USDC:USDC"
    leverage = 2.5
    size_usd = 10.0

    # =========================================================================
    # STEP 1: Create market buy order (open long position)
    # =========================================================================
    long_order = gmx.create_order(
        symbol=symbol,
        type="market",
        side="buy",
        amount=size_usd,
        params={
            "leverage": leverage,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
        },
    )

    assert long_order is not None
    assert long_order.get("id") is not None
    assert long_order.get("symbol") == symbol
    assert long_order.get("side") == "buy"

    long_tx_hash = long_order.get("info", {}).get("tx_hash") or long_order.get("id")
    assert long_tx_hash is not None, "Long order should have transaction hash"

    # =========================================================================
    # STEP 2: Execute long order as keeper
    # =========================================================================
    _execute_order(web3, long_tx_hash)

    # =========================================================================
    # STEP 3: Create market sell order (open short position)
    # =========================================================================
    short_order = gmx.create_order(
        symbol=symbol,
        type="market",
        side="sell",
        amount=size_usd,
        params={
            "leverage": leverage,
            "collateral_symbol": "USDC",  # USDC collateral for shorts
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
        },
    )

    assert short_order is not None
    assert short_order.get("id") is not None
    assert short_order.get("symbol") == symbol
    assert short_order.get("side") == "sell"

    short_tx_hash = short_order.get("info", {}).get("tx_hash") or short_order.get("id")
    assert short_tx_hash is not None, "Short order should have transaction hash"

    # =========================================================================
    # STEP 4: Execute short order as keeper
    # =========================================================================
    _execute_order(web3, short_tx_hash)

    # =========================================================================
    # STEP 5: Verify both positions exist
    # =========================================================================
    time.sleep(2)  # Brief wait for state to settle
    positions = gmx.fetch_positions([symbol])

    assert len(positions) == 2, f"Should have 2 positions, got {len(positions)}"

    # Find long and short positions
    long_position = None
    short_position = None
    for pos in positions:
        if pos.get("side") == "long":
            long_position = pos
        elif pos.get("side") == "short":
            short_position = pos

    assert long_position is not None, "Should have a long position"
    assert short_position is not None, "Should have a short position"
    assert long_position.get("symbol") == symbol
    assert short_position.get("symbol") == symbol
    assert long_position.get("contracts", 0) > 0
    assert short_position.get("contracts", 0) > 0

    long_size = long_position.get("notional", 0)
    short_size = short_position.get("notional", 0)

    # =========================================================================
    # STEP 6: Update oracle prices for close (simulate price movement)
    # =========================================================================
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(web3)
    # Keep price same - no need to move it for closing
    setup_mock_oracle(
        web3,
        eth_price_usd=current_eth_price,
        usdc_price_usd=current_usdc_price,
    )

    # =========================================================================
    # STEP 7: Close long position (sell)
    # =========================================================================
    close_long_order = gmx.create_order(
        symbol=symbol,
        type="market",
        side="sell",
        amount=long_size,
        params={
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
        },
    )

    assert close_long_order is not None
    close_long_tx_hash = close_long_order.get("info", {}).get("tx_hash") or close_long_order.get("id")
    _execute_order(web3, close_long_tx_hash)

    # =========================================================================
    # STEP 8: Close short position (buy)
    # =========================================================================
    close_short_order = gmx.create_order(
        symbol=symbol,
        type="market",
        side="buy",
        amount=short_size,
        params={
            "collateral_symbol": "USDC",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
        },
    )

    assert close_short_order is not None
    close_short_tx_hash = close_short_order.get("info", {}).get("tx_hash") or close_short_order.get("id")
    _execute_order(web3, close_short_tx_hash)

    # =========================================================================
    # STEP 9: Verify all positions are closed
    # =========================================================================
    time.sleep(2)
    final_positions = gmx.fetch_positions([symbol])

    assert len(final_positions) == 0, f"All positions should be closed, got {len(final_positions)}"
