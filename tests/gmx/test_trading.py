"""
Tests for GMXTrading on Arbitrum mainnet fork.

These tests verify GMX trading functionality on Arbitrum mainnet fork with mock oracle.
Each test gets its own completely isolated Anvil fork instance.

Tests follow the complete order lifecycle:
1. Create order (sign and submit transaction)
2. Execute order as keeper (using mock oracle)
3. Verify position was created with assertions

Required Environment Variables:
- JSON_RPC_ARBITRUM: Arbitrum mainnet RPC endpoint for forking

Uses isolated_fork_env fixture which provides:
- Fresh Anvil fork per test
- Mock oracle set up FIRST (matching debug.py flow)
- Funded wallet with ETH/WETH/USDC
- GMX config with approved tokens
"""

import pytest
from flaky import flaky

from eth_defi.gmx.order.base_order import OrderResult
from eth_defi.gmx.trading import GMXTrading
from tests.gmx.fork_helpers import execute_order_as_keeper, extract_order_key_from_receipt, fetch_on_chain_oracle_prices, setup_mock_oracle


def test_initialization(isolated_fork_env):
    """Test that the trading module initialises correctly on Arbitrum mainnet fork."""
    env = isolated_fork_env
    trading = GMXTrading(env.config)
    assert trading.config == env.config
    assert trading.config.get_chain().lower() == "arbitrum"


# NOTE: These tests are flaky because the blockchain changes and GMX is notoriously unstable and make changes unannounced. We have faced this several times during development. Tests start failing out of the blue for silly reasons.
@flaky(max_runs=3, min_passes=1)
def test_open_long_position(isolated_fork_env, execution_buffer):
    """
    Test opening a long ETH position with full execution.

    Flow:
    1. Create order (ETH market, ETH collateral, 2.5x leverage)
    2. Submit transaction to blockchain
    3. Execute order as keeper
    4. Verify position was created
    """
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    # Record initial state
    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    # Sync nonce before transaction
    env.wallet.sync_nonce(env.web3)

    # === Step 1: Create order ===
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.005,
        execution_buffer=execution_buffer,
    )

    # Verify OrderResult structure
    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"
    assert hasattr(order_result, "transaction"), "OrderResult should have transaction"
    assert order_result.execution_fee > 0, "Execution fee should be > 0"

    # === Step 2: Submit order transaction ===
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order transaction should succeed"

    # Extract order key from receipt
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"

    # === Step 3: Execute order as keeper ===
    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    # === Step 4: Verify position was created ===
    final_positions = env.positions.get_data(wallet_address)
    final_position_count = len(final_positions)

    assert final_position_count == initial_position_count + 1, "Should have 1 more position"

    # Verify position details
    assert len(final_positions) > 0, "Should have at least one position"
    position_key, position = list(final_positions.items())[0]

    assert position["market_symbol"] == "ETH", "Position should be for ETH market"
    assert position["is_long"] is True, "Position should be long"
    assert position["position_size"] > 0, "Position size should be > 0"
    assert position["leverage"] > 0, "Leverage should be > 0"


@flaky(max_runs=3, min_passes=1)
def test_open_short_position(isolated_fork_env_short, execution_buffer):
    """
    Test opening a short ETH position with full execution.
    Uses isolated fork with ETH price of 3550 USD.

    Flow:
    1. Create order (ETH market, USDC collateral, 2.5x leverage)
    2. Submit transaction to blockchain
    3. Execute order as keeper
    4. Verify position was created
    """
    env = isolated_fork_env_short
    wallet_address = env.config.get_wallet_address()

    # Record initial state
    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    # Sync nonce before transaction
    env.wallet.sync_nonce(env.web3)

    # === Step 1: Create order ===
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=False,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.005,
        execution_buffer=execution_buffer,
    )

    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"

    # === Step 2: Submit order transaction ===
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order transaction should succeed"

    # Extract order key
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"

    # === Step 3: Execute order as keeper ===
    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    # === Step 4: Verify position was created ===
    final_positions = env.positions.get_data(wallet_address)
    final_position_count = len(final_positions)

    assert final_position_count == initial_position_count + 1, "Should have 1 more position"

    # Verify position details
    position_key, position = list(final_positions.items())[0]
    assert position["market_symbol"] == "ETH", "Position should be for ETH market"
    assert position["is_long"] is False, "Position should be short"
    assert position["position_size"] > 0, "Position size should be > 0"


@flaky(max_runs=3, min_passes=1)
def test_open_and_close_position(isolated_fork_env, execution_buffer):
    """
    Test full position lifecycle: open then close.
    Uses isolated fork with fresh oracle setup.

    Flow:
    1. Open position (long ETH)
    2. Verify position was created
    3. Close position (decrease to 0)
    4. Verify position was closed
    """
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    # Record initial state
    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    # Sync nonce before transaction
    env.wallet.sync_nonce(env.web3)

    # === Step 1: Open position ===
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.005,
        execution_buffer=execution_buffer,
    )

    # Submit and execute open order
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Open order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, _ = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Open order execution should succeed"

    # === Step 2: Verify position was created ===
    positions_after_open = env.positions.get_data(wallet_address)
    assert len(positions_after_open) == initial_position_count + 1, "Should have 1 position after opening"

    position_key, position = list(positions_after_open.items())[0]
    position_size_usd = position["position_size"]
    # Use raw position size (exact on-chain value with 30 decimals) for closing
    # GMX requires EXACT matching - even 1 wei difference will cause failure
    position_size_usd_raw = position["position_size_usd_raw"]
    collateral_amount_usd = position["initial_collateral_amount_usd"]
    assert position_size_usd > 0, "Position size should be > 0"

    # Update mock oracle price before closing to simulate price movement
    # For long positions: price goes UP to create profit
    # Use 1% of current price to keep the pool solvent on the fork
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(env.web3)
    new_eth_price = int(current_eth_price * 1.01)
    setup_mock_oracle(
        env.web3,
        eth_price_usd=new_eth_price,
        usdc_price_usd=current_usdc_price,
    )

    # Sync wallet nonce after oracle setup (which sends transactions)
    env.wallet.sync_nonce(env.web3)

    # === Step 3: Close position ===
    close_order_result = env.trading.close_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",  # Receive ETH when closing
        is_long=True,
        size_delta_usd=position_size_usd_raw,  # Use raw value for exact match
        initial_collateral_delta=collateral_amount_usd,  # Withdraw all collateral
        slippage_percent=0.005,
        execution_buffer=execution_buffer,
    )

    # Submit and execute close order
    close_transaction = close_order_result.transaction.copy()
    if "nonce" in close_transaction:
        del close_transaction["nonce"]

    signed_close_tx = env.wallet.sign_transaction_with_new_nonce(close_transaction)
    close_tx_hash = env.web3.eth.send_raw_transaction(signed_close_tx.rawTransaction)
    close_receipt = env.web3.eth.wait_for_transaction_receipt(close_tx_hash)

    assert close_receipt["status"] == 1, "Close order transaction should succeed"

    close_order_key = extract_order_key_from_receipt(close_receipt)
    close_exec_receipt, _ = execute_order_as_keeper(env.web3, close_order_key)
    assert close_exec_receipt["status"] == 1, "Close order execution should succeed"

    # === Step 4: Verify position was closed ===
    positions_after_close = env.positions.get_data(wallet_address)
    assert len(positions_after_close) == initial_position_count, "Should have no positions after closing"


@flaky(max_runs=3, min_passes=1)
def test_open_limit_long_position(isolated_fork_env, execution_buffer):
    """
    Test opening a limit long ETH position with keeper execution.

    Flow:
    1. Create limit order at a trigger price (use current price to ensure immediate execution)
    2. Submit transaction to blockchain
    3. Execute order as keeper
    4. Verify position was created

    Note: In production, limit orders wait for price to reach trigger level.
    For testing, we set trigger_price to current oracle price so keeper can execute immediately.
    """
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    # Record initial state
    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    # Sync nonce before transaction
    env.wallet.sync_nonce(env.web3)

    # Get current ETH price from oracle for trigger price
    # Use current price so the limit order can be executed immediately by keeper
    eth_oracle_price, _ = fetch_on_chain_oracle_prices(env.web3)

    # === Step 1: Create limit order ===
    order_result = env.trading.open_limit_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=10,
        leverage=2.5,
        trigger_price=eth_oracle_price,  # Use current price for immediate execution in test
        slippage_percent=0.005,
        execution_buffer=execution_buffer,
        auto_cancel=True,
    )

    # Verify OrderResult structure
    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"
    assert hasattr(order_result, "transaction"), "OrderResult should have transaction"
    assert order_result.execution_fee > 0, "Execution fee should be > 0"

    # === Step 2: Submit order transaction ===
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order transaction should succeed"

    # Extract order key from receipt
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"

    # === Step 3: Execute order as keeper ===
    # In production, keeper would wait for price to reach trigger
    # In test, we execute immediately since trigger_price == current price
    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    # === Step 4: Verify position was created ===
    final_positions = env.positions.get_data(wallet_address)
    final_position_count = len(final_positions)

    assert final_position_count == initial_position_count + 1, "Should have 1 more position"

    # Verify position details
    assert len(final_positions) > 0, "Should have at least one position"
    position_key, position = list(final_positions.items())[0]

    assert position["market_symbol"] == "ETH", "Position should be for ETH market"
    assert position["is_long"] is True, "Position should be long"
    assert position["position_size"] > 0, "Position size should be > 0"
    assert position["leverage"] > 0, "Leverage should be > 0"
