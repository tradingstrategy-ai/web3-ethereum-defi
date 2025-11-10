"""
Tests for GMXTrading on Arbitrum mainnet fork.

These tests verify GMX trading functionality on Arbitrum mainnet fork with mock oracle.
All tests run on an Anvil fork with a mock oracle to enable testing without live price feeds.

Tests follow the complete order lifecycle:
1. Create order (sign and submit transaction)
2. Execute order as keeper (using mock oracle)
3. Verify position was created with assertions

Required Environment Variables:
- ARBITRUM_JSON_RPC_URL: Arbitrum mainnet RPC endpoint for forking

All fixtures are defined in conftest.py:
- arbitrum_fork_config: GMX config for mainnet fork
- trading_manager_fork: GMXTrading instance
- position_verifier_fork: GetOpenPositions instance
- web3_arbitrum_fork: Web3 instance with mock oracle setup
- test_wallet: HotWallet for signing transactions
"""

from eth_defi.gmx.order.base_order import OrderResult
from eth_defi.gmx.trading import GMXTrading
from tests.gmx.fork_helpers import execute_order_as_keeper, extract_order_key_from_receipt


def test_initialization(arbitrum_fork_config):
    """Test that the trading module initializes correctly on Arbitrum mainnet fork."""
    trading = GMXTrading(arbitrum_fork_config)
    assert trading.config == arbitrum_fork_config
    assert trading.config.get_chain().lower() == "arbitrum"


def test_open_long_position(
    web3_arbitrum_fork,
    trading_manager_fork,
    position_verifier_fork,
    arbitrum_fork_config,
    test_wallet,
):
    """
    Test opening a long ETH position with full execution.

    Flow:
    1. Create order (ETH market, ETH collateral, 2.5x leverage)
    2. Submit transaction to blockchain
    3. Execute order as keeper
    4. Verify position was created
    """
    wallet_address = arbitrum_fork_config.get_wallet_address()

    # Record initial state
    initial_positions = position_verifier_fork.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    # === Step 1: Create order ===
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
    assert order_result.execution_fee > 0, "Execution fee should be > 0"

    # === Step 2: Submit order transaction ===
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = test_wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = web3_arbitrum_fork.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3_arbitrum_fork.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order transaction should succeed"

    # Extract order key from receipt
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"

    # === Step 3: Execute order as keeper ===
    exec_receipt, keeper_address = execute_order_as_keeper(web3_arbitrum_fork, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    # === Step 4: Verify position was created ===
    final_positions = position_verifier_fork.get_data(wallet_address)
    final_position_count = len(final_positions)

    assert final_position_count == initial_position_count + 1, "Should have 1 more position"

    # Verify position details
    assert len(final_positions) > 0, "Should have at least one position"
    position_key, position = list(final_positions.items())[0]

    assert position["market_symbol"] == "ETH", "Position should be for ETH market"
    assert position["is_long"] is True, "Position should be long"
    assert position["position_size"] > 0, "Position size should be > 0"
    assert position["leverage"] > 0, "Leverage should be > 0"


def test_open_short_position(
    web3_arbitrum_fork,
    arbitrum_fork_config_short,
    test_wallet,
):
    """
    Test opening a short ETH position with full execution.
    Uses ETH price of 3550 USD.

    Flow:
    1. Create order (ETH market, USDC collateral, 2.5x leverage)
    2. Submit transaction to blockchain
    3. Execute order as keeper
    4. Verify position was created
    """
    from eth_defi.gmx.trading import GMXTrading
    from eth_defi.gmx.core import GetOpenPositions

    # Create instances with short position config
    trading_manager_fork = GMXTrading(arbitrum_fork_config_short)
    position_verifier_fork = GetOpenPositions(arbitrum_fork_config_short)
    wallet_address = arbitrum_fork_config_short.get_wallet_address()

    # Record initial state
    initial_positions = position_verifier_fork.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    # === Step 1: Create order ===
    order_result = trading_manager_fork.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=False,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.005,
        execution_buffer=2.2,
    )

    assert isinstance(order_result, OrderResult), "Expected OrderResult instance"

    # === Step 2: Submit order transaction ===
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = test_wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = web3_arbitrum_fork.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3_arbitrum_fork.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order transaction should succeed"

    # Extract order key
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"

    # === Step 3: Execute order as keeper ===
    exec_receipt, keeper_address = execute_order_as_keeper(web3_arbitrum_fork, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    # === Step 4: Verify position was created ===
    final_positions = position_verifier_fork.get_data(wallet_address)
    final_position_count = len(final_positions)

    assert final_position_count == initial_position_count + 1, "Should have 1 more position"

    # Verify position details
    position_key, position = list(final_positions.items())[0]
    assert position["market_symbol"] == "ETH", "Position should be for ETH market"
    assert position["is_long"] is False, "Position should be short"
    assert position["position_size"] > 0, "Position size should be > 0"


def test_open_and_close_position(
    web3_arbitrum_fork,
    arbitrum_fork_config_open_close,
    test_wallet,
):
    """
    Test full position lifecycle: open then close.
    Uses fresh oracle setup with ETH price of 3450 USD.

    Flow:
    1. Open position (long ETH)
    2. Verify position was created
    3. Close position (decrease to 0)
    4. Verify position was closed
    """
    from eth_defi.gmx.trading import GMXTrading
    from eth_defi.gmx.core import GetOpenPositions

    # Create instances with open/close position config
    trading_manager_fork = GMXTrading(arbitrum_fork_config_open_close)
    position_verifier_fork = GetOpenPositions(arbitrum_fork_config_open_close)
    wallet_address = arbitrum_fork_config_open_close.get_wallet_address()

    # Record initial state
    initial_positions = position_verifier_fork.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    # === Step 1: Open position ===
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

    # Submit and execute open order
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = test_wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = web3_arbitrum_fork.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3_arbitrum_fork.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Open order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, _ = execute_order_as_keeper(web3_arbitrum_fork, order_key)
    assert exec_receipt["status"] == 1, "Open order execution should succeed"

    # === Step 2: Verify position was created ===
    positions_after_open = position_verifier_fork.get_data(wallet_address)
    assert len(positions_after_open) == initial_position_count + 1, "Should have 1 position after opening"

    position_key, position = list(positions_after_open.items())[0]
    position_size_usd = position["position_size"]
    collateral_amount_usd = position["initial_collateral_amount_usd"]
    assert position_size_usd > 0, "Position size should be > 0"

    # === Step 3: Close position ===
    close_order_result = trading_manager_fork.close_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",  # Receive ETH when closing
        is_long=True,
        size_delta_usd=position_size_usd,  # Close full position
        initial_collateral_delta=collateral_amount_usd,  # Withdraw all collateral
        slippage_percent=0.005,
        execution_buffer=2.2,
    )

    # Submit and execute close order
    close_transaction = close_order_result.transaction.copy()
    if "nonce" in close_transaction:
        del close_transaction["nonce"]

    signed_close_tx = test_wallet.sign_transaction_with_new_nonce(close_transaction)
    close_tx_hash = web3_arbitrum_fork.eth.send_raw_transaction(signed_close_tx.rawTransaction)
    close_receipt = web3_arbitrum_fork.eth.wait_for_transaction_receipt(close_tx_hash)

    assert close_receipt["status"] == 1, "Close order transaction should succeed"

    close_order_key = extract_order_key_from_receipt(close_receipt)
    close_exec_receipt, _ = execute_order_as_keeper(web3_arbitrum_fork, close_order_key)
    assert close_exec_receipt["status"] == 1, "Close order execution should succeed"

    # === Step 4: Verify position was closed ===
    positions_after_close = position_verifier_fork.get_data(wallet_address)
    assert len(positions_after_close) == initial_position_count, "Should have no positions after closing"
