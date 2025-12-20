"""GMX CCXT Trading Tests - Open and Close Position.

Tests CCXT-compatible trading methods on Arbitrum mainnet fork.
Follows the same workflow as debug_ccxt.py for reliability.
"""

from flaky import flaky

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


# NOTE: These 2 tests don't run together. They only pass when ran separately.


@flaky(max_runs=3, min_passes=1)
def test_open_and_close_long_position(
    ccxt_gmx_fork_open_close: GMX,
    web3_arbitrum_fork_ccxt_long,
    execution_buffer: int,
):
    """Test opening and closing a long position via CCXT interface.

    Uses separate anvil fork (web3_arbitrum_fork_ccxt_long) to avoid state pollution.
    """
    gmx = ccxt_gmx_fork_open_close
    web3 = web3_arbitrum_fork_ccxt_long

    symbol = "ETH/USDC:USDC"
    leverage = 2.5
    size_usd = 10.0

    # Open long position
    order = gmx.create_order(
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

    position_size = position.get("notional", 0)

    # Update oracle for close
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(web3)
    new_eth_price = current_eth_price + 1000
    setup_mock_oracle(web3, eth_price_usd=new_eth_price, usdc_price_usd=current_usdc_price)

    # Close long position
    close_order = gmx.create_order(
        symbol=symbol,
        type="market",
        side="sell",
        amount=position_size,
        params={
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
        },
    )

    assert close_order is not None
    close_tx_hash = close_order.get("info", {}).get("tx_hash") or close_order.get("id")
    _execute_order(web3, close_tx_hash)

    # Verify closed
    final_positions = gmx.fetch_positions([symbol])
    assert len(final_positions) == 0, "Position should be closed"


# @flaky(max_runs=3, min_passes=1)
# def test_open_and_close_short_position(
#     ccxt_gmx_fork_short: GMX,
#     web3_arbitrum_fork_ccxt_short,
#     execution_buffer: int,
# ):
#     """Test opening and closing a short position via CCXT interface.

#     Uses separate anvil fork (web3_arbitrum_fork_ccxt_short) to avoid state pollution.
#     """
#     gmx = ccxt_gmx_fork_short
#     web3 = web3_arbitrum_fork_ccxt_short

#     symbol = "ETH/USDC:USDC"
#     leverage = 2.5
#     size_usd = 10.0

#     # Open short position
#     order = gmx.create_order(
#         symbol=symbol,
#         type="market",
#         side="sell",
#         amount=size_usd,
#         params={
#             "leverage": leverage,
#             "collateral_symbol": "USDC",
#             "slippage_percent": 0.005,
#             "execution_buffer": execution_buffer,
#         },
#     )

#     assert order is not None
#     assert order.get("id") is not None
#     assert order.get("symbol") == symbol
#     assert order.get("side") == "sell"

#     tx_hash = order.get("info", {}).get("tx_hash") or order.get("id")
#     assert tx_hash is not None, "Order should have transaction hash"

#     _execute_order(web3, tx_hash)

#     # Verify position exists
#
#     positions = gmx.fetch_positions([symbol])

#     assert len(positions) > 0, "Should have at least one position after opening"

#     position = positions[0]
#     assert position.get("symbol") == symbol
#     assert position.get("side") == "short"
#     assert position.get("contracts", 0) > 0
#     assert position.get("notional", 0) > 0

#     position_size = position.get("notional", 0)

#     # Update oracle for close
#     current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(web3)
#     new_eth_price = current_eth_price - 1000
#     setup_mock_oracle(web3, eth_price_usd=new_eth_price, usdc_price_usd=current_usdc_price)

#     # Close short position
#     close_order = gmx.create_order(
#         symbol=symbol,
#         type="market",
#         side="buy",
#         amount=position_size,
#         params={
#             "collateral_symbol": "USDC",
#             "slippage_percent": 0.005,
#             "execution_buffer": execution_buffer,
#         },
#     )

#     assert close_order is not None
#     close_tx_hash = close_order.get("info", {}).get("tx_hash") or close_order.get("id")
#     _execute_order(web3, close_tx_hash)

#     # Verify closed
#
#     final_positions = gmx.fetch_positions([symbol])
#     assert len(final_positions) == 0, "Position should be closed"
