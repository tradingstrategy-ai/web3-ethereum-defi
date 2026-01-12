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


@flaky(max_runs=3, min_passes=1)
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


@flaky(max_runs=3, min_passes=1)
def test_ccxt_fetch_order_detects_cancelled(isolated_fork_env, execution_buffer):
    """Test that CCXT fetch_order() correctly detects cancelled orders.

    This test verifies the full CCXT integration where:
    1. create_order() returns status "open" (pending keeper execution)
    2. Keeper cancels the order due to price movement
    3. fetch_order() detects the cancellation and returns status "cancelled"

    This is the new two-phase execution model matching how Freqtrade polls
    for order status updates.
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

    # Simulate create_order() returning an "open" order
    # In real usage, create_order() would do this automatically
    tx_hash_str = tx_hash.hex() if isinstance(tx_hash, bytes) else str(tx_hash)
    initial_order = gmx._parse_order_result_to_ccxt(
        order_result,
        symbol="ETH/USDC:USDC",
        side="buy",
        type="market",
        amount=10.0,
        tx_hash=tx_hash_str,
        receipt=receipt,
        order_key=order_key,
    )

    # Store in GMX order cache (create_order() does this automatically)
    gmx._orders[tx_hash_str] = initial_order

    # Verify initial order has status "open"
    assert initial_order["status"] == "open", f"Expected status 'open', got: {initial_order['status']}"
    assert initial_order["info"].get("order_key") == order_key.hex(), "order_key should be stored in info"

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

    # Now fetch_order() should detect the cancellation
    # This is how Freqtrade polls for order status
    updated_order = gmx.fetch_order(tx_hash_str)

    # Verify order status changed to "cancelled"
    assert updated_order["status"] == "cancelled", f"Expected status 'cancelled', got: {updated_order['status']}"
    assert updated_order["filled"] == 0.0, "Cancelled order should have filled=0"
    assert updated_order["remaining"] == updated_order["amount"], "Cancelled order should have remaining=amount"

    # Verify cancellation details are stored
    assert "cancellation_reason" in updated_order["info"], "Should have cancellation_reason in info"
    assert "event_names" in updated_order["info"], "Should have event_names in info"
    assert "OrderCancelled" in updated_order["info"]["event_names"], f"Should have OrderCancelled in event_names, got: {(updated_order['info'].get('event_names'))}"

    print(
        f"Order correctly detected as cancelled: reason={updated_order['info'].get('cancellation_reason')}",
    )


def test_create_order_rejects_conflicting_size_parameters(ccxt_gmx_fork_open_close):
    """Test that create_order raises InvalidOrder when both size_usd and non-zero amount are provided.

    This is a regression test for the position size bug where:
    - The CCXT standard `amount` parameter is in base currency (ETH)
    - The GMX extension `size_usd` parameter is in USD
    - If both are provided, it's ambiguous which should be used

    The fix ensures that the API rejects conflicting parameters with a clear error
    message to prevent accidental position size miscalculations.

    Example of the bug this prevents:
    - User intends to open a $10 USD position
    - User passes amount=10.0 (interpreted as 10 ETH) AND size_usd=10 (interpreted as $10 USD)
    - Without validation, one would be silently ignored, leading to unexpected position size
    """
    from ccxt.base.errors import InvalidOrder

    gmx = ccxt_gmx_fork_open_close

    # Attempt to create order with both size_usd and non-zero amount
    # This should raise InvalidOrder
    with pytest.raises(InvalidOrder) as exc_info:
        gmx.create_order(
            symbol="ETH/USDC:USDC",
            type="market",
            side="buy",
            amount=10.0,  # Non-zero base currency amount (10 ETH)
            params={
                "size_usd": 25.0,  # Direct USD sizing (conflict!)
                "leverage": 2.5,
                "collateral_symbol": "ETH",
                "slippage_percent": 0.005,
            },
        )

    # Verify the error message is helpful
    error_msg = str(exc_info.value)
    assert "size_usd" in error_msg, "Error should mention size_usd parameter"
    assert "amount" in error_msg, "Error should mention amount parameter"
    assert "Cannot use both" in error_msg, "Error should explain the conflict"


def test_create_order_accepts_size_usd_with_zero_amount(ccxt_gmx_fork_open_close, execution_buffer):
    """Test that create_order accepts size_usd when amount is 0.

    The recommended pattern is to use size_usd for USD-denominated position sizes
    with amount=0. This test verifies that pattern works correctly.
    """
    gmx = ccxt_gmx_fork_open_close

    # Create order using the recommended pattern: size_usd with amount=0
    # This should NOT raise InvalidOrder
    # Use wait_for_execution=False for fork tests (Subsquid won't have fork order data)
    order = gmx.create_order(
        symbol="ETH/USDC:USDC",
        type="market",
        side="buy",
        amount=0,  # Zero amount (not used)
        params={
            "size_usd": 10.0,  # Direct USD sizing
            "leverage": 2.5,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
            "wait_for_execution": False,  # Skip Subsquid/EventEmitter waiting on fork
        },
    )

    # Verify order was created (status should be "open" waiting for keeper)
    assert order is not None
    assert order["status"] == "open"
    assert order["symbol"] == "ETH/USDC:USDC"
    assert order["side"] == "buy"
