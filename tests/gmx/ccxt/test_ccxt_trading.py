"""
Simplified CCXT trading tests for GMX.

Two minimal tests demonstrating complete position lifecycle:
1. Open and close a long ETH position
2. Open and close a short ETH position
"""

import pytest
from flaky import flaky

from eth_defi.gmx.order.base_order import OrderResult
from tests.gmx.fork_helpers import execute_order_as_keeper, extract_order_key_from_receipt, fetch_on_chain_oracle_prices, setup_mock_oracle


def _execute_order(web3, tx_hash):
    """Execute a GMX order as keeper.

    Helper function for SLTP tests that takes a transaction hash,
    extracts the order key, and executes the order.

    :param web3: Web3 instance
    :param tx_hash: Transaction hash from order creation
    :return: Execution receipt
    """
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, _ = execute_order_as_keeper(web3, order_key)
    return exec_receipt


@flaky(max_runs=3, min_passes=1)
def test_ccxt_open_and_close_long_position(isolated_fork_env, execution_buffer):
    """
    Test opening and closing a long ETH position.

    Flow:
    1. Open long position (ETH market, ETH collateral, 2.5x leverage)
    2. Execute order as keeper
    3. Verify position created
    4. Update oracle price (+1000 USD for profit)
    5. Close position
    6. Execute close order
    7. Verify position closed
    """
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    # Record initial state
    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    # Sync nonce
    env.wallet.sync_nonce(env.web3)

    # === STEP 1: Open long position ===
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.5,
        execution_buffer=execution_buffer,
    )

    assert isinstance(order_result, OrderResult)
    assert order_result.execution_fee > 0

    # Submit and execute open order
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None

    # Execute order as keeper
    exec_receipt, _ = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1

    # === STEP 2: Verify position created ===
    positions_after_open = env.positions.get_data(wallet_address)
    assert len(positions_after_open) == initial_position_count + 1

    position_key, position = list(positions_after_open.items())[0]
    position_size_usd_raw = position["position_size_usd_raw"]
    collateral_amount_usd = position["initial_collateral_amount_usd"]
    assert position["market_symbol"] == "ETH"
    assert position["is_long"] is True
    assert position["position_size"] > 0

    # Update oracle price for profit (long: price goes UP)
    # Use 1% of current price to keep the pool solvent on the fork
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(env.web3)
    new_eth_price = int(current_eth_price * 1.01)
    setup_mock_oracle(
        env.web3,
        eth_price_usd=new_eth_price,
        usdc_price_usd=current_usdc_price,
    )

    env.wallet.sync_nonce(env.web3)

    # === STEP 3: Close position ===
    close_order_result = env.trading.close_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=position_size_usd_raw,
        initial_collateral_delta=collateral_amount_usd,
        slippage_percent=0.5,
        execution_buffer=execution_buffer,
    )

    # Submit and execute close order
    close_transaction = close_order_result.transaction.copy()
    if "nonce" in close_transaction:
        del close_transaction["nonce"]

    signed_close_tx = env.wallet.sign_transaction_with_new_nonce(close_transaction)
    close_tx_hash = env.web3.eth.send_raw_transaction(signed_close_tx.rawTransaction)
    close_receipt = env.web3.eth.wait_for_transaction_receipt(close_tx_hash)

    assert close_receipt["status"] == 1
    close_order_key = extract_order_key_from_receipt(close_receipt)
    close_exec_receipt, _ = execute_order_as_keeper(env.web3, close_order_key)
    assert close_exec_receipt["status"] == 1

    # === STEP 4: Verify position closed ===
    positions_after_close = env.positions.get_data(wallet_address)
    assert len(positions_after_close) == initial_position_count


@flaky(max_runs=3, min_passes=1)
def test_ccxt_open_and_close_short_position(
    isolated_fork_env_short,
    execution_buffer,
):
    """
    Test opening and closing a short ETH position.

    Flow:
    1. Open short position (ETH market, ETH collateral, 2.5x leverage)
    2. Execute order as keeper
    3. Verify position created
    4. Update oracle price (-1000 USD for profit)
    5. Close position
    6. Execute close order
    7. Verify position closed
    """
    env = isolated_fork_env_short
    wallet_address = env.config.get_wallet_address()

    # Record initial state
    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    # Sync nonce
    env.wallet.sync_nonce(env.web3)

    # === STEP 1: Open short position ===
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=False,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.5,
        execution_buffer=execution_buffer,
    )

    assert isinstance(order_result, OrderResult)
    assert order_result.execution_fee > 0

    # Submit and execute open order
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None

    # Execute order as keeper
    exec_receipt, _ = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1

    # === STEP 2: Verify position created ===
    positions_after_open = env.positions.get_data(wallet_address)
    assert len(positions_after_open) == initial_position_count + 1

    position_key, position = list(positions_after_open.items())[0]
    position_size_usd_raw = position["position_size_usd_raw"]
    collateral_amount_usd = position["initial_collateral_amount_usd"]
    assert position["market_symbol"] == "ETH"
    assert position["is_long"] is False
    assert position["position_size"] > 0

    # Update oracle price for profit (short: price goes DOWN)
    # Use 1% of current price to keep the pool solvent on the fork
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(env.web3)
    new_eth_price = int(current_eth_price * 0.99)
    setup_mock_oracle(
        env.web3,
        eth_price_usd=new_eth_price,
        usdc_price_usd=current_usdc_price,
    )

    env.wallet.sync_nonce(env.web3)

    # === STEP 3: Close position ===
    close_order_result = env.trading.close_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=False,
        size_delta_usd=position_size_usd_raw,
        initial_collateral_delta=collateral_amount_usd,
        slippage_percent=0.5,
        execution_buffer=execution_buffer,
    )

    # Submit and execute close order
    close_transaction = close_order_result.transaction.copy()
    if "nonce" in close_transaction:
        del close_transaction["nonce"]

    signed_close_tx = env.wallet.sign_transaction_with_new_nonce(close_transaction)
    close_tx_hash = env.web3.eth.send_raw_transaction(signed_close_tx.rawTransaction)
    close_receipt = env.web3.eth.wait_for_transaction_receipt(close_tx_hash)

    assert close_receipt["status"] == 1
    close_order_key = extract_order_key_from_receipt(close_receipt)
    close_exec_receipt, _ = execute_order_as_keeper(env.web3, close_order_key)
    assert close_exec_receipt["status"] == 1

    # === STEP 4: Verify position closed ===
    positions_after_close = env.positions.get_data(wallet_address)
    assert len(positions_after_close) == initial_position_count
