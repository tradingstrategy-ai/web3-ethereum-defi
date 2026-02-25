"""Integration tests for GMX limit order cancellation.

Tests the core ``pending_orders`` and ``cancel_order`` modules against a live
Arbitrum mainnet fork (Anvil).  Each test follows the full lifecycle:

1. Open a position (market order executed by keeper).
2. Create a stop-loss or take-profit order.
3. Verify the order is pending via ``fetch_pending_orders``.
4. Cancel the order (single or batch).
5. Verify the order is gone from the DataStore.
"""

from flaky import flaky

from eth_defi.gmx.order.cancel_order import BatchCancelOrderResult, CancelOrder, CancelOrderResult
from eth_defi.gmx.order.pending_orders import fetch_pending_order_count, fetch_pending_orders
from eth_defi.gmx.order_tracking import is_order_pending


def test_fetch_pending_order_count_empty(isolated_fork_env):
    """Fresh fork account has no pending limit orders in the DataStore."""
    env = isolated_fork_env
    wallet_address = env.config.get_wallet_address()
    chain = env.config.get_chain()

    count = fetch_pending_order_count(env.web3, chain, wallet_address)
    assert count == 0, f"Expected 0 pending orders on fresh account, got {count}"

    orders = list(fetch_pending_orders(env.web3, chain, wallet_address))
    assert orders == [], "Expected no pending orders on fresh account"


@flaky(max_runs=3, min_passes=1)
def test_cancel_stop_loss_lifecycle(funded_isolated_fork_env, pending_sl_key):
    """Full lifecycle: open position → create SL → verify pending → cancel → verify gone."""
    env = funded_isolated_fork_env
    sl_key = pending_sl_key
    wallet_address = env.config.get_wallet_address()
    chain = env.config.get_chain()

    # Step 1: Verify the SL is pending
    assert is_order_pending(env.web3, sl_key, chain), "SL order must be pending in DataStore after creation"

    count_before = fetch_pending_order_count(env.web3, chain, wallet_address)
    assert count_before >= 1, "Must have at least 1 pending order after SL creation"

    pending = list(fetch_pending_orders(env.web3, chain, wallet_address))
    sl_keys = [o.order_key for o in pending]
    assert sl_key in sl_keys, "SL order key must appear in fetch_pending_orders"

    sl_order = next(o for o in pending if o.order_key == sl_key)
    assert sl_order.is_stop_loss, "Order must be classified as stop loss"
    assert sl_order.is_long is True, "SL order must be for long position"
    assert sl_order.trigger_price_usd > 0, "SL trigger price must be non-zero"

    # Step 2: Cancel the SL order
    canceller = CancelOrder(env.config)
    env.wallet.sync_nonce(env.web3)
    cancel_result = canceller.cancel_order(sl_key)
    assert isinstance(cancel_result, CancelOrderResult)

    cancel_tx = cancel_result.transaction.copy()
    cancel_tx.pop("nonce", None)
    signed_cancel = env.wallet.sign_transaction_with_new_nonce(cancel_tx)
    cancel_hash = env.web3.eth.send_raw_transaction(signed_cancel.rawTransaction)
    cancel_receipt = env.web3.eth.wait_for_transaction_receipt(cancel_hash)
    assert cancel_receipt["status"] == 1, "Cancel transaction must succeed"

    # Step 3: Verify the SL order is no longer pending
    assert not is_order_pending(env.web3, sl_key, chain), "SL order must no longer be pending after cancellation"

    count_after = fetch_pending_order_count(env.web3, chain, wallet_address)
    assert count_after == count_before - 1, "Pending order count must decrease by 1 after cancellation"


@flaky(max_runs=3, min_passes=1)
def test_batch_cancel_sl_and_tp(funded_isolated_fork_env, pending_sl_key, pending_tp_key):
    """Create SL + TP, batch cancel both in one transaction, verify both gone."""
    env = funded_isolated_fork_env
    sl_key = pending_sl_key
    tp_key = pending_tp_key
    wallet_address = env.config.get_wallet_address()
    chain = env.config.get_chain()

    # Verify both are pending
    assert is_order_pending(env.web3, sl_key, chain), "SL must be pending"
    assert is_order_pending(env.web3, tp_key, chain), "TP must be pending"

    expected_min_orders = 2  # SL + TP
    count_before = fetch_pending_order_count(env.web3, chain, wallet_address)
    assert count_before >= expected_min_orders, f"Expected at least 2 pending orders, got {count_before}"

    # Batch cancel both orders
    canceller = CancelOrder(env.config)
    env.wallet.sync_nonce(env.web3)
    batch_result = canceller.cancel_orders([sl_key, tp_key])
    assert isinstance(batch_result, BatchCancelOrderResult)
    assert batch_result.order_keys == [sl_key, tp_key]

    batch_tx = batch_result.transaction.copy()
    batch_tx.pop("nonce", None)
    signed_batch = env.wallet.sign_transaction_with_new_nonce(batch_tx)
    batch_hash = env.web3.eth.send_raw_transaction(signed_batch.rawTransaction)
    batch_receipt = env.web3.eth.wait_for_transaction_receipt(batch_hash)
    assert batch_receipt["status"] == 1, "Batch cancel transaction must succeed"

    # Verify both orders are gone
    assert not is_order_pending(env.web3, sl_key, chain), "SL must no longer be pending after batch cancel"
    assert not is_order_pending(env.web3, tp_key, chain), "TP must no longer be pending after batch cancel"

    count_after = fetch_pending_order_count(env.web3, chain, wallet_address)
    assert count_after == count_before - 2, "Pending count must decrease by 2 after batch cancel"


@flaky(max_runs=3, min_passes=1)
def test_cancel_via_gmx_trading(funded_isolated_fork_env, pending_sl_key):
    """Cancel SL via GMXTrading.cancel_order() high-level method."""
    env = funded_isolated_fork_env
    sl_key = pending_sl_key
    chain = env.config.get_chain()

    assert is_order_pending(env.web3, sl_key, chain), "SL must be pending before cancel"

    env.wallet.sync_nonce(env.web3)
    cancel_result = env.trading.cancel_order(sl_key)
    assert isinstance(cancel_result, CancelOrderResult)

    cancel_tx = cancel_result.transaction.copy()
    cancel_tx.pop("nonce", None)
    signed = env.wallet.sign_transaction_with_new_nonce(cancel_tx)
    tx_hash = env.web3.eth.send_raw_transaction(signed.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1, "GMXTrading.cancel_order() transaction must succeed"

    assert not is_order_pending(env.web3, sl_key, chain), "SL must be gone after GMXTrading.cancel_order()"
