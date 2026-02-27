"""Integration tests for GMX CCXT cancel_order and fetch_orders.

Tests the CCXT-compatible ``cancel_order()`` and ``fetch_orders()`` / ``fetch_open_orders()``
methods against a live Arbitrum mainnet fork (Anvil).

Lifecycle tested:

1. Open a long position with a bundled stop-loss.
2. Execute the position order as keeper (SL stays pending in DataStore).
3. ``fetch_orders()`` returns the pending SL as a CCXT order dict.
4. ``cancel_order(sl_key_hex)`` cancels the SL.
5. ``fetch_orders()`` returns an empty list.
"""

import pytest
from ccxt.base.errors import OrderNotFound
from flaky import flaky

from eth_defi.gmx.ccxt.exchange import GMX
from tests.gmx.fork_helpers import execute_order_as_keeper, extract_order_key_from_receipt


def _execute_order(web3, tx_hash: str, refund_address: str | None = None) -> dict:
    """Execute a GMX order as keeper given a creation transaction hash.

    :param web3:
        Web3 instance connected to the Anvil fork.
    :param tx_hash:
        Transaction hash from the order creation.
    :param refund_address:
        If given, re-fund this address with ETH after keeper execution.
        ``execute_order_as_keeper`` drains the wallet's ETH balance on
        Anvil forks; this restores it so subsequent wallet transactions
        (e.g. ``cancel_order``) can pay for gas.
    :return:
        Execution receipt.
    """
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, _ = execute_order_as_keeper(web3, order_key)

    if refund_address is not None:
        eth_amount = 100_000_000 * 10**18
        web3.provider.make_request("anvil_setBalance", [refund_address, hex(eth_amount)])

    return exec_receipt


@flaky(max_runs=3, min_passes=1)
def test_ccxt_fetch_orders_after_sl_creation(
    ccxt_gmx_fork_open_close: GMX,
    web3_arbitrum_fork_ccxt_long,
    execution_buffer: int,
):
    """fetch_orders() returns the pending SL order after a bundled open+SL transaction.

    Opens an ETH long with a bundled stop-loss, executes the position order via
    keeper (leaving the SL order pending), then verifies that ``fetch_orders()``
    returns it with the correct CCXT structure.
    """
    gmx = ccxt_gmx_fork_open_close
    web3 = web3_arbitrum_fork_ccxt_long
    symbol = "ETH/USDC:USDC"

    # Open position with bundled SL; SL order is created in the same tx
    order = gmx.create_market_buy_order(
        symbol,
        0,
        {
            "size_usd": 10.0,
            "leverage": 2.5,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
            "wait_for_execution": False,
            "stopLoss": {
                "triggerPercent": 0.05,
                "closePercent": 1.0,
            },
        },
    )

    assert order is not None
    tx_hash = order.get("info", {}).get("tx_hash") or order.get("id")
    assert tx_hash is not None, "Order must have a transaction hash"

    # Execute the main (market increase) order via keeper.
    # The SL order stays pending in the DataStore.
    _execute_order(web3, tx_hash)

    # fetch_orders() should now return the pending SL
    pending = gmx.fetch_orders(symbol=symbol)
    assert len(pending) >= 1, f"Expected at least 1 pending order, got {len(pending)}"

    sl = pending[0]
    assert sl.get("status") == "open", "Pending order must have status='open'"
    assert sl.get("id") is not None, "Pending order must have an id (order key hex)"
    assert sl.get("type") == "stopLoss", f"Expected type='stopLoss', got {sl.get('type')!r}"
    assert sl.get("side") == "buy", "Long SL order must have side='buy'"
    assert sl.get("price", 0) > 0, "SL trigger price must be non-zero"

    info = sl.get("info", {})
    assert info.get("order_key") is not None, "info.order_key must be set"
    assert info.get("is_long") is True, "SL order must be for a long position"


@flaky(max_runs=3, min_passes=1)
def test_ccxt_cancel_order_lifecycle(
    ccxt_gmx_fork_open_close: GMX,
    web3_arbitrum_fork_ccxt_long,
    execution_buffer: int,
):
    """Full lifecycle: open + SL → fetch_orders → cancel_order → fetch_orders empty.

    Verifies that the CCXT cancel_order() correctly cancels a pending SL order
    and that subsequent fetch_orders() no longer returns it.
    """
    gmx = ccxt_gmx_fork_open_close
    web3 = web3_arbitrum_fork_ccxt_long
    symbol = "ETH/USDC:USDC"

    # Step 1: Open position with bundled SL
    order = gmx.create_market_buy_order(
        symbol,
        0,
        {
            "size_usd": 10.0,
            "leverage": 2.5,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
            "wait_for_execution": False,
            "stopLoss": {
                "triggerPercent": 0.05,
                "closePercent": 1.0,
            },
        },
    )

    assert order is not None
    tx_hash = order.get("info", {}).get("tx_hash") or order.get("id")
    assert tx_hash is not None

    # Step 2: Execute the position order; SL stays pending.
    # Re-fund wallet — execute_order_as_keeper zeroes the wallet's ETH
    # balance on Anvil forks.  See execute_order_as_keeper docstring.
    wallet_address = gmx.wallet.address
    _execute_order(web3, tx_hash, refund_address=wallet_address)
    gmx.wallet.sync_nonce(web3)

    # Step 3: Get the pending SL order via fetch_orders
    pending = gmx.fetch_orders(symbol=symbol)
    assert len(pending) >= 1, f"Expected pending SL order, got {len(pending)} orders"

    sl_order = pending[0]
    sl_key_hex = sl_order["id"]
    assert sl_key_hex.startswith("0x"), "Order key must be '0x'-prefixed hex"

    # Step 4: Cancel the SL order via CCXT interface
    cancel_result = gmx.cancel_order(sl_key_hex, symbol=symbol)
    assert cancel_result.get("status") == "cancelled", f"Expected status='cancelled', got {cancel_result.get('status')!r}"
    assert cancel_result.get("id") == sl_key_hex, "Cancelled order id must match the requested key"
    assert cancel_result["info"].get("tx_hash") is not None, "Cancel result must include tx_hash"

    # Step 5: Verify the order is gone
    pending_after = gmx.fetch_orders(symbol=symbol)
    cancelled_keys = [o["id"] for o in pending_after]
    assert sl_key_hex not in cancelled_keys, f"Cancelled SL key {sl_key_hex[:18]}… must not appear in fetch_orders() after cancellation"


@flaky(max_runs=3, min_passes=1)
def test_ccxt_fetch_open_orders_pending_only(
    ccxt_gmx_fork_open_close: GMX,
    web3_arbitrum_fork_ccxt_long,
    execution_buffer: int,
):
    """fetch_open_orders(params={'pending_orders_only': True}) returns the pending SL order."""
    gmx = ccxt_gmx_fork_open_close
    web3 = web3_arbitrum_fork_ccxt_long
    symbol = "ETH/USDC:USDC"

    # Open position with bundled SL
    order = gmx.create_market_buy_order(
        symbol,
        0,
        {
            "size_usd": 10.0,
            "leverage": 2.5,
            "collateral_symbol": "ETH",
            "slippage_percent": 0.005,
            "execution_buffer": execution_buffer,
            "wait_for_execution": False,
            "stopLoss": {
                "triggerPercent": 0.05,
                "closePercent": 1.0,
            },
        },
    )

    assert order is not None
    tx_hash = order.get("info", {}).get("tx_hash") or order.get("id")
    _execute_order(web3, tx_hash)

    # fetch_open_orders with pending_orders_only must return the SL
    pending = gmx.fetch_open_orders(symbol=symbol, params={"pending_orders_only": True})
    assert len(pending) >= 1, "fetch_open_orders(pending_orders_only=True) must return the SL"

    # Default fetch_open_orders must return positions (not pending orders)
    positions_as_orders = gmx.fetch_open_orders(symbol=symbol)
    # The position was opened, so default mode should show it
    assert len(positions_as_orders) >= 1, "Default fetch_open_orders must return open positions"
    # None of the default orders should be stop_loss typed
    for o in positions_as_orders:
        assert o.get("type") != "stopLoss", "Default fetch_open_orders must not return SL orders"


def test_ccxt_cancel_nonexistent_order(
    ccxt_gmx_fork_open_close: GMX,
):
    """cancel_order() raises OrderNotFound for a key not in the DataStore."""
    gmx = ccxt_gmx_fork_open_close

    # Fabricate a well-formed but non-existent order key
    fake_key = "0x" + "ab" * 32

    with pytest.raises(OrderNotFound):
        gmx.cancel_order(fake_key)


def test_ccxt_fetch_orders_empty_account(
    ccxt_gmx_fork_open_close: GMX,
):
    """fetch_orders() returns an empty list when there are no pending limit orders."""
    gmx = ccxt_gmx_fork_open_close

    # No orders have been created yet, so this must be empty
    pending = gmx.fetch_orders()
    assert pending == [], f"Expected empty list, got {pending}"
