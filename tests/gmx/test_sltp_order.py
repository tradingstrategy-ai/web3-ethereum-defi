"""GMX Stop Loss and Take Profit Order Tests.

Tests SL/TP order creation and execution on Arbitrum mainnet fork.
Uses isolated_fork_env fixture for complete test isolation.

Tests cover two modes:
1. BUNDLED: Open + SL + TP in single transaction (open_position_with_sltp)
2. STANDALONE: Open position first, then add SL/TP (create_stop_loss, create_take_profit)

Tests follow the complete order lifecycle:
1. Create order with SL/TP parameters
2. Submit transaction to blockchain
3. Execute order as keeper (using mock oracle)
4. Verify position was created with assertions
"""

import time

import pytest
from flaky import flaky

from eth_defi.gmx.order.sltp_order import SLTPOrderResult
from tests.gmx.fork_helpers import execute_order_as_keeper, extract_order_key_from_receipt, fetch_on_chain_oracle_prices, setup_mock_oracle


# ============================================================================
# BUNDLED MODE TESTS - Open + SL + TP in single transaction
# ============================================================================


@flaky(max_runs=3, min_passes=1)
def test_bundled_long_with_stop_loss(isolated_fork_env, execution_buffer):
    """Test bundled long position with stop loss only."""
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    env.wallet.sync_nonce(env.web3)

    order_result = env.trading.open_position_with_sltp(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=100,
        leverage=2.5,
        stop_loss_percent=0.05,
        slippage_percent=0.1,  # keep it as it is ik what I'm doing. Don't waste time & move on
        execution_buffer=execution_buffer,
    )

    assert isinstance(order_result, SLTPOrderResult), "Expected SLTPOrderResult instance"
    assert order_result.total_execution_fee > 0, "Total execution fee should be > 0"
    assert order_result.stop_loss_fee > 0, "Stop loss fee should be > 0"

    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"

    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    time.sleep(1)  # Wait for state propagation
    final_positions = env.positions.get_data(wallet_address)
    final_position_count = len(final_positions)

    assert final_position_count == initial_position_count + 1, "Should have 1 more position"

    position_key, position = list(final_positions.items())[0]
    assert position["market_symbol"] == "ETH", "Position should be for ETH market"
    assert position["is_long"] is True, "Position should be long"
    assert position["position_size"] > 0, "Position size should be > 0"


@flaky(max_runs=3, min_passes=1)
def test_bundled_long_with_take_profit(isolated_fork_env, execution_buffer):
    """Test bundled long position with take profit only."""
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    env.wallet.sync_nonce(env.web3)

    order_result = env.trading.open_position_with_sltp(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=100,
        leverage=2.5,
        take_profit_percent=0.15,
        slippage_percent=0.1,  # keep it as it is ik what I'm doing. Don't waste time & move on
        execution_buffer=execution_buffer,
    )

    assert isinstance(order_result, SLTPOrderResult), "Expected SLTPOrderResult instance"
    assert order_result.total_execution_fee > 0, "Total execution fee should be > 0"
    assert order_result.take_profit_fee > 0, "Take profit fee should be > 0"

    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    time.sleep(1)  # Wait for state propagation
    final_positions = env.positions.get_data(wallet_address)
    assert len(final_positions) == initial_position_count + 1, "Should have 1 more position"


@flaky(max_runs=3, min_passes=1)
def test_bundled_long_with_both_sl_tp(isolated_fork_env, execution_buffer):
    """Test bundled long position with both stop loss and take profit."""
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    env.wallet.sync_nonce(env.web3)

    order_result = env.trading.open_position_with_sltp(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=100,
        leverage=2.5,
        stop_loss_percent=0.05,
        take_profit_percent=0.15,
        slippage_percent=0.1,  # keep it as it is ik what I'm doing. Don't waste time & move on
        execution_buffer=execution_buffer,
    )

    assert isinstance(order_result, SLTPOrderResult), "Expected SLTPOrderResult instance"
    assert order_result.total_execution_fee > 0, "Total execution fee should be > 0"
    assert order_result.stop_loss_fee > 0, "Stop loss fee should be > 0"
    assert order_result.take_profit_fee > 0, "Take profit fee should be > 0"
    assert order_result.entry_price > 0, "Entry price should be > 0"
    assert order_result.stop_loss_trigger_price is not None, "Stop loss trigger should be set"
    assert order_result.take_profit_trigger_price is not None, "Take profit trigger should be set"

    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    time.sleep(1)  # Wait for state propagation
    final_positions = env.positions.get_data(wallet_address)
    assert len(final_positions) == initial_position_count + 1, "Should have 1 more position"

    position_key, position = list(final_positions.items())[0]
    assert position["market_symbol"] == "ETH", "Position should be for ETH market"
    assert position["is_long"] is True, "Position should be long"


@flaky(max_runs=3, min_passes=1)
def test_bundled_short_with_sl_tp(isolated_fork_env_short, execution_buffer):
    """Test bundled short position with SL and TP (matches debug_sltp.py)."""
    env = isolated_fork_env_short
    wallet_address = env.config.get_wallet_address()

    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    env.wallet.sync_nonce(env.web3)

    order_result = env.trading.open_position_with_sltp(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=False,
        size_delta_usd=100,
        leverage=2.5,
        stop_loss_percent=0.05,
        take_profit_percent=0.10,
        slippage_percent=0.1,  # keep it as it is ik what I'm doing. Don't waste time & move on
        execution_buffer=execution_buffer,
    )

    assert isinstance(order_result, SLTPOrderResult), "Expected SLTPOrderResult instance"

    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    time.sleep(1)  # Wait for state propagation
    final_positions = env.positions.get_data(wallet_address)
    assert len(final_positions) == initial_position_count + 1, "Should have 1 more position"

    position_key, position = list(final_positions.items())[0]
    assert position["market_symbol"] == "ETH", "Position should be for ETH market"
    assert position["is_long"] is False, "Position should be short"


# ============================================================================
# STANDALONE MODE TESTS - Open position first, then add SL/TP
# ============================================================================

from eth_defi.gmx.synthetic_tokens import get_gmx_synthetic_token_by_symbol
from eth_defi.token import fetch_erc20_details
from eth_utils import to_checksum_address


def _fund_wallet_for_trading(env, wallet_address):
    """Fund wallet with USDC and ETH for trading."""
    # Fund wallet with USDC from whale if needed
    large_usdc_holder = to_checksum_address("0x2Df1c51E09aECF9cacB7bc98cB1742757f163dF7")
    usdc_token = get_gmx_synthetic_token_by_symbol(env.web3.eth.chain_id, "USDC")

    if usdc_token:
        usdc = fetch_erc20_details(env.web3, usdc_token.address)

        # Check current USDC balance
        usdc_balance_pre = usdc.contract.functions.balanceOf(wallet_address).call()

        # Fund wallet with USDC if balance is 0 or low
        if usdc_balance_pre < 1000 * 10**6:  # Less than 1000 USDC
            # Impersonate whale
            env.web3.provider.make_request("anvil_impersonateAccount", [large_usdc_holder])

            # Fund whale with gas
            gas_eth = 100 * 10**18
            env.web3.provider.make_request("anvil_setBalance", [large_usdc_holder, hex(gas_eth)])

            # Transfer USDC from whale to wallet
            usdc_amount = 100_000_000 * 10**6  # 100M USDC
            tx_hash = usdc.contract.functions.transfer(wallet_address, usdc_amount).transact({"from": large_usdc_holder})
            env.web3.eth.wait_for_transaction_receipt(tx_hash)

            # Stop impersonating
            env.web3.provider.make_request("anvil_stopImpersonatingAccount", [large_usdc_holder])

    # Ensure wallet has enough ETH
    env.web3.provider.make_request("anvil_setBalance", [wallet_address, hex(100 * 10**18)])


@flaky(max_runs=3, min_passes=1)
def test_standalone_long_with_stop_loss(isolated_fork_env, execution_buffer):
    """Test standalone stop loss: open position first, then add SL."""
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    env.wallet.sync_nonce(env.web3)
    _fund_wallet_for_trading(env, wallet_address)

    # Step 1: Open position without SL/TP
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=True,
        size_delta_usd=100,
        leverage=2.5,
        slippage_percent=0.1,  # keep it as it is ik what I'm doing. Don't waste time & move on
        execution_buffer=execution_buffer,
    )

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

    # Get position details
    time.sleep(1)
    positions = env.positions.get_data(wallet_address)
    assert len(positions) > 0, "Should have position after opening"

    pos_key, pos_data = list(positions.items())[0]
    entry_price = pos_data["entry_price"]
    position_size = pos_data["position_size"]

    # Re-fund wallet with ETH explicitly to fix the 0 balance issue
    env.web3.provider.make_request("anvil_setBalance", [wallet_address, hex(100 * 10**18)])

    # Step 2: Create standalone stop loss
    env.wallet.sync_nonce(env.web3)

    sl_result = env.trading.create_stop_loss(
        market_symbol="ETH",
        collateral_symbol="ETH",
        is_long=True,
        position_size_usd=position_size,
        entry_price=entry_price,
        stop_loss_percent=0.05,
        execution_buffer=execution_buffer * 10,
    )

    sl_tx = sl_result.transaction.copy()
    if "nonce" in sl_tx:
        del sl_tx["nonce"]

    signed_sl = env.wallet.sign_transaction_with_new_nonce(sl_tx)
    sl_hash = env.web3.eth.send_raw_transaction(signed_sl.rawTransaction)
    sl_receipt = env.web3.eth.wait_for_transaction_receipt(sl_hash)

    assert sl_receipt["status"] == 1, "Stop loss order should succeed"


@flaky(max_runs=3, min_passes=1)
def test_standalone_long_with_take_profit(isolated_fork_env, execution_buffer):
    """Test standalone take profit: open position first, then add TP."""
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    env.wallet.sync_nonce(env.web3)
    _fund_wallet_for_trading(env, wallet_address)

    # Step 1: Open position without SL/TP
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=True,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.1,  # keep it as it is ik what I'm doing. Don't waste time & move on
        execution_buffer=execution_buffer * 10,
    )

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

    # Get position details
    time.sleep(1)
    positions = env.positions.get_data(wallet_address)
    assert len(positions) > 0, "Should have position after opening"

    pos_key, pos_data = list(positions.items())[0]
    entry_price = pos_data["entry_price"]
    position_size = pos_data["position_size"]

    # Re-fund wallet with ETH explicitly to fix the 0 balance issue
    env.web3.provider.make_request("anvil_setBalance", [wallet_address, hex(100 * 10**18)])

    # Step 2: Create standalone take profit
    env.wallet.sync_nonce(env.web3)

    tp_result = env.trading.create_take_profit(
        market_symbol="ETH",
        collateral_symbol="ETH",
        is_long=True,
        position_size_usd=position_size,
        entry_price=entry_price,
        take_profit_percent=0.10,
        execution_buffer=execution_buffer,
    )

    tp_tx = tp_result.transaction.copy()
    if "nonce" in tp_tx:
        del tp_tx["nonce"]

    signed_tp = env.wallet.sign_transaction_with_new_nonce(tp_tx)
    tp_hash = env.web3.eth.send_raw_transaction(signed_tp.rawTransaction)
    tp_receipt = env.web3.eth.wait_for_transaction_receipt(tp_hash)

    assert tp_receipt["status"] == 1, "Take profit order should succeed"


@flaky(max_runs=3, min_passes=1)
def test_standalone_short_with_sl_and_tp(isolated_fork_env_short, execution_buffer):
    """Test standalone SL + TP for short position (matches debug_sltp.py standalone mode)."""
    env = isolated_fork_env_short
    wallet_address = env.config.get_wallet_address()

    env.wallet.sync_nonce(env.web3)
    _fund_wallet_for_trading(env, wallet_address)

    # Step 1: Open short position
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=False,
        size_delta_usd=100,
        leverage=2.5,
        slippage_percent=0.1,  # keep it as it is ik what I'm doing. Don't waste time & move on
        execution_buffer=execution_buffer,
    )

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

    # Get position details
    time.sleep(1)
    positions = env.positions.get_data(wallet_address)
    assert len(positions) > 0, "Should have position after opening"

    pos_key, pos_data = list(positions.items())[0]
    entry_price = pos_data["entry_price"]
    position_size = pos_data["position_size"]

    # Re-fund wallet with ETH explicitly to fix the 0 balance issue
    env.web3.provider.make_request("anvil_setBalance", [wallet_address, hex(100 * 10**18)])

    # Step 2: Create stop loss for short position
    env.wallet.sync_nonce(env.web3)

    sl_result = env.trading.create_stop_loss(
        market_symbol="ETH",
        collateral_symbol="ETH",
        is_long=False,
        position_size_usd=position_size,
        entry_price=entry_price,
        stop_loss_percent=0.05,
        execution_buffer=execution_buffer * 10,
    )

    sl_tx = sl_result.transaction.copy()
    if "nonce" in sl_tx:
        del sl_tx["nonce"]

    signed_sl = env.wallet.sign_transaction_with_new_nonce(sl_tx)
    sl_hash = env.web3.eth.send_raw_transaction(signed_sl.rawTransaction)
    sl_receipt = env.web3.eth.wait_for_transaction_receipt(sl_hash)

    assert sl_receipt["status"] == 1, "Stop loss order should succeed"

    # Step 3: Create take profit for short position
    env.wallet.sync_nonce(env.web3)

    tp_result = env.trading.create_take_profit(
        market_symbol="ETH",
        collateral_symbol="ETH",
        is_long=False,
        position_size_usd=position_size,
        entry_price=entry_price,
        take_profit_percent=0.10,
        execution_buffer=execution_buffer,
    )

    tp_tx = tp_result.transaction.copy()
    if "nonce" in tp_tx:
        del tp_tx["nonce"]

    signed_tp = env.wallet.sign_transaction_with_new_nonce(tp_tx)
    tp_hash = env.web3.eth.send_raw_transaction(signed_tp.rawTransaction)
    tp_receipt = env.web3.eth.wait_for_transaction_receipt(tp_hash)

    assert tp_receipt["status"] == 1, "Take profit order should succeed"


# ============================================================================
# FULL LIFECYCLE TESTS - Open with SL/TP, then close
# ============================================================================


@flaky(max_runs=3, min_passes=1)
def test_full_lifecycle_open_and_close_with_sl_tp(isolated_fork_env, execution_buffer):
    """Test full position lifecycle with SL/TP: open then close."""
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    env.wallet.sync_nonce(env.web3)

    order_result = env.trading.open_position_with_sltp(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=100,
        leverage=2.5,
        stop_loss_percent=0.05,
        take_profit_percent=0.15,
        slippage_percent=0.1,  # keep it as it is ik what I'm doing. Don't waste time & move on
        execution_buffer=execution_buffer,
    )

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

    time.sleep(1)  # Wait for state propagation
    positions_after_open = env.positions.get_data(wallet_address)
    assert len(positions_after_open) == initial_position_count + 1, "Should have 1 position after opening"

    position_key, position = list(positions_after_open.items())[0]
    position_size_usd_raw = position["position_size_usd_raw"]
    collateral_amount_usd = position["initial_collateral_amount_usd"]

    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(env.web3)
    new_eth_price = current_eth_price + 20  # Small increase to create profit without breaking pool solvency
    setup_mock_oracle(
        env.web3,
        eth_price_usd=new_eth_price,
        usdc_price_usd=current_usdc_price,
    )

    env.wallet.sync_nonce(env.web3)

    close_order_result = env.trading.close_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=position_size_usd_raw,
        initial_collateral_delta=collateral_amount_usd,
        slippage_percent=0.1,
        execution_buffer=execution_buffer,
    )

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

    positions_after_close = env.positions.get_data(wallet_address)
    assert len(positions_after_close) == initial_position_count, "Should have no positions after closing"


# ============================================================================
# ABSOLUTE PRICE TRIGGER TESTS
# ============================================================================


@flaky(max_runs=3, min_passes=1)
def test_absolute_trigger_price_stop_loss(isolated_fork_env, execution_buffer):
    """Test stop loss with absolute trigger price instead of percentage."""
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    env.wallet.sync_nonce(env.web3)

    order_result = env.trading.open_position_with_sltp(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=100,
        leverage=2.5,
        stop_loss_price=3000.0,
        slippage_percent=0.1,  # keep it as it is ik what I'm doing. Don't waste time & move on
        execution_buffer=execution_buffer,
    )

    assert isinstance(order_result, SLTPOrderResult), "Expected SLTPOrderResult instance"

    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    time.sleep(1)  # Wait for state propagation
    final_positions = env.positions.get_data(wallet_address)
    assert len(final_positions) == initial_position_count + 1, "Should have 1 more position"


@flaky(max_runs=3, min_passes=1)
def test_absolute_trigger_price_take_profit(isolated_fork_env, execution_buffer):
    """Test take profit with absolute trigger price instead of percentage."""
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()

    initial_positions = env.positions.get_data(wallet_address)
    initial_position_count = len(initial_positions)

    env.wallet.sync_nonce(env.web3)

    order_result = env.trading.open_position_with_sltp(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=100,
        leverage=2.5,
        take_profit_price=4500.0,
        slippage_percent=0.1,  # keep it as it is ik what I'm doing. Don't waste time & move on
        execution_buffer=execution_buffer,
    )

    assert isinstance(order_result, SLTPOrderResult), "Expected SLTPOrderResult instance"

    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, keeper_address = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Order execution should succeed"

    time.sleep(1)  # Wait for state propagation
    final_positions = env.positions.get_data(wallet_address)
    assert len(final_positions) == initial_position_count + 1, "Should have 1 more position"
