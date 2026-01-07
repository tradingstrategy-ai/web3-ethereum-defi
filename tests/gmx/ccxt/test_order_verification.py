"""Test GMX order verification integration.

These tests verify that the order verification module correctly detects
GMX-level order failures and raises GMXOrderFailedException when orders
are cancelled or frozen, even when receipt.status == 1.
"""

from eth_defi.gmx.ccxt.exchange import GMX
import pytest
from flaky import flaky

from eth_defi.gmx.ccxt.errors import GMXOrderFailedException
from eth_defi.gmx.order.base_order import OrderResult
from eth_defi.gmx.verification import raise_if_order_failed, verify_gmx_order_execution
from tests.gmx.fork_helpers import (
    execute_order_as_keeper,
    extract_order_key_from_receipt,
    fetch_on_chain_oracle_prices,
    setup_mock_oracle,
)


# @flaky(max_runs=3, min_passes=1)
def test_order_verification_raises_on_cancelled_order(
    isolated_fork_env,
    execution_buffer,
):
    """Test GMXOrderFailedException is raised when order is cancelled due to price movement.

    This test verifies the full order verification integration:
    1. Create a market buy order with tight slippage tolerance
    2. Submit transaction and extract order key
    3. Move oracle price UP significantly (for long order, this exceeds acceptable price)
    4. Execute order as keeper - execution price > acceptable price
    5. GMX cancels order with OrderNotFulfillableAtAcceptablePrice
    6. Verify GMXOrderFailedException is raised with proper error details

    The verification module detects that receipt.status == 1 but OrderCancelled event
    exists, and raises the exception instead of returning a "successful" order.
    """
    env = isolated_fork_env

    # Sync nonce
    env.wallet.sync_nonce(env.web3)

    # Create order with tight slippage (0.1% = 0.001) - will fail when price moves significantly
    # Note: slippage_percent=0.001 means 0.1%, slippage_percent=0.1 means 10%
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.001,  # 0.1% slippage tolerance (tight)
        execution_buffer=execution_buffer,
    )

    assert isinstance(order_result, OrderResult)
    assert order_result.execution_fee > 0
    assert order_result.acceptable_price > 0

    # Submit the order transaction
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)

    assert receipt["status"] == 1, "Order creation transaction should succeed"

    # Extract order key
    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None

    # Move oracle price UP significantly BEFORE keeper execution
    # For a LONG order, acceptable_price is the MAX price buyer will pay
    # Moving price UP means execution_price > acceptable_price → order cancelled
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(env.web3)
    new_eth_price = int(current_eth_price * 1.10)  # 10% price increase
    setup_mock_oracle(
        env.web3,
        eth_price_usd=new_eth_price,
        usdc_price_usd=current_usdc_price,
    )

    # Execute order as keeper - should be cancelled by GMX due to price movement
    exec_receipt, _ = execute_order_as_keeper(env.web3, order_key)

    # Transaction succeeds at blockchain level but order may be cancelled at GMX level
    assert exec_receipt["status"] == 1, "Keeper execution tx should succeed"

    # Verify the order was cancelled
    verification_result = verify_gmx_order_execution(env.web3, exec_receipt, order_key)

    assert verification_result.success is False, "Order should have failed due to tight slippage"
    assert verification_result.status == "cancelled", f"Expected cancelled, got {verification_result.status}"
    assert verification_result.order_key == order_key

    # Assert event names - critical for verifying correct event parsing
    assert "OrderCancelled" in verification_result.event_names, f"Expected OrderCancelled event, got: {verification_result.event_names}"
    assert "OrderExecuted" not in verification_result.event_names, f"OrderExecuted should NOT be present for cancelled order, got: {verification_result.event_names}"
    # Failed orders typically have fewer events (6-10) compared to successful ones (20-32+)
    assert verification_result.event_count < 15, f"Cancelled orders should have fewer events, got {verification_result.event_count}"

    # Check that error is related to acceptable price
    error_msg = verification_result.decoded_error or verification_result.reason or ""
    assert "acceptable" in error_msg.lower() or "OrderNotFulfillableAtAcceptablePrice" in error_msg, f"Expected slippage-related error, got: {error_msg}"

    # Verify raise_if_order_failed raises the exception
    with pytest.raises(GMXOrderFailedException) as exc_info:
        raise_if_order_failed(
            env.web3,
            exec_receipt,
            tx_hash.hex() if isinstance(tx_hash, bytes) else tx_hash,
            order_key,
        )

    # Verify exception attributes
    exc = exc_info.value
    assert exc.status == "cancelled"
    assert exc.order_key == order_key
    assert exc.receipt is not None

    print(f"Raised GMXOrderFailedException as expected: {exc=}")


@flaky(max_runs=3, min_passes=1)
def test_order_verification_succeeds_for_valid_order(
    isolated_fork_env,
    execution_buffer,
):
    """Test that verification returns success for normally executed orders.

    This serves as a control test to ensure the verification module correctly
    identifies successful orders and does NOT raise GMXOrderFailedException.
    """
    env = isolated_fork_env

    # Sync nonce
    env.wallet.sync_nonce(env.web3)

    # Create order with normal slippage - should succeed
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.5,  # Normal 0.5% slippage
        execution_buffer=execution_buffer,
    )

    assert isinstance(order_result, OrderResult)

    # Submit order
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1

    order_key = extract_order_key_from_receipt(receipt)

    # Execute order normally
    exec_receipt, _ = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1

    # Verify order succeeded
    verification_result = verify_gmx_order_execution(env.web3, exec_receipt, order_key)

    assert verification_result.success is True, "Order should have succeeded"
    assert verification_result.status == "executed"
    assert verification_result.execution_price is not None, "Should have execution price"
    assert verification_result.execution_price > 0
    assert verification_result.size_delta_usd is not None, "Should have size delta"
    assert verification_result.size_delta_usd > 0

    # Assert event names - critical for verifying correct event parsing
    assert "OrderExecuted" in verification_result.event_names, f"Expected OrderExecuted event, got: {verification_result.event_names}"
    assert "PositionIncrease" in verification_result.event_names, f"Expected PositionIncrease event for opening long, got: {verification_result.event_names}"
    assert "OrderCancelled" not in verification_result.event_names, f"OrderCancelled should NOT be present for successful order, got: {verification_result.event_names}"
    # Successful orders typically have more events (20-32+) compared to failed ones (6-10)
    assert verification_result.event_count >= 20, f"Successful orders should have many events, got {verification_result.event_count}"

    # raise_if_order_failed should NOT raise
    result = raise_if_order_failed(
        env.web3,
        exec_receipt,
        tx_hash.hex() if isinstance(tx_hash, bytes) else tx_hash,
        order_key,
    )
    assert result.success is True
    assert result.execution_price is not None


# @flaky(max_runs=3, min_passes=1)
def test_ccxt_parse_order_raises_on_cancelled(isolated_fork_env, execution_buffer):
    """Test that CCXT _parse_order_result_to_ccxt() raises GMXOrderFailedException.

    This test verifies the full CCXT integration where the exception is raised
    from within the CCXT exchange wrapper's parsing method.
    """

    env = isolated_fork_env

    # Create GMX CCXT instance
    gmx = GMX(
        params={
            "rpcUrl": env.web3.provider.endpoint_uri,
            "wallet": env.wallet,
        }
    )

    # Sync nonce
    env.wallet.sync_nonce(env.web3)

    # Create order with tight slippage (0.1% = 0.001)
    # Note: slippage_percent=0.001 means 0.1%, slippage_percent=0.1 means 10%
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="ETH",
        start_token_symbol="ETH",
        is_long=True,
        size_delta_usd=10,
        leverage=2.5,
        slippage_percent=0.001,  # 0.1% slippage tolerance (tight)
        execution_buffer=execution_buffer,
    )

    # Submit order transaction
    transaction = order_result.transaction.copy()
    if "nonce" in transaction:
        del transaction["nonce"]

    signed_tx = env.wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1

    order_key = extract_order_key_from_receipt(receipt)

    # Move oracle price UP significantly BEFORE keeper execution
    # For a LONG order, acceptable_price is the MAX price buyer will pay
    # Moving price UP means execution_price > acceptable_price → order cancelled
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(env.web3)
    new_eth_price = int(current_eth_price * 1.10)  # 10% price increase
    setup_mock_oracle(
        env.web3,
        eth_price_usd=new_eth_price,
        usdc_price_usd=current_usdc_price,
    )

    # Execute as keeper - should be cancelled due to price movement
    exec_receipt, _ = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1

    # First verify events directly to confirm order was cancelled
    verification_result = verify_gmx_order_execution(env.web3, exec_receipt, order_key)
    assert "OrderCancelled" in verification_result.event_names, f"Expected OrderCancelled event, got: {verification_result.event_names}"
    assert "OrderExecuted" not in verification_result.event_names, f"OrderExecuted should NOT be present, got: {verification_result.event_names}"

    # Call CCXT's parse method which includes verification
    # This should raise GMXOrderFailedException
    with pytest.raises(GMXOrderFailedException) as exc_info:
        gmx._parse_order_result_to_ccxt(
            order_result,
            symbol="ETH/USD:USDC",
            side="buy",
            type="market",
            amount=10.0,
            tx_hash=tx_hash.hex() if isinstance(tx_hash, bytes) else str(tx_hash),
            receipt=exec_receipt,
        )

    exc = exc_info.value
    assert exc.status == "cancelled"
    assert exc.order_key == order_key
