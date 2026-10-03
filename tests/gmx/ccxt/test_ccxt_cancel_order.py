"""Integration tests for GMX CCXT cancel_order and fetch_orders.

Tests the CCXT-compatible ``cancel_order()`` and ``fetch_orders()`` / ``fetch_open_orders()``
methods against a live Arbitrum mainnet fork (Anvil).

Lifecycle tested:

1. Open a long position with a bundled stop-loss.
2. Execute the position order as keeper (SL stays pending in DataStore).
3. ``fetch_orders()`` returns the pending SL as a CCXT order dict.
4. ``cancel_order(sl_key_hex)`` cancels the SL.
5. No pending SL remains; cached completed orders may still be returned.
"""

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from ccxt.base.errors import OrderNotFound
from eth_typing import HexAddress
from web3 import Web3
from web3.types import TxReceipt

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.testing import execute_order_as_keeper, extract_order_key_from_receipt
from eth_defi.testing.gmx_lagoon import gmx_fork_position_reads


@pytest.fixture(autouse=True)
def _fork_local_order_reads(ccxt_gmx_fork_open_close: GMX) -> Iterator[None]:
    """Keep fork order assertions independent of public indexed history.

    Local fork orders/positions are read from real DataStore/Reader contracts.
    Public Subsquid history cannot include these private transactions; separate
    provider and recovery tests cover indexed histories.

    :param ccxt_gmx_fork_open_close:
        Actual funded CCXT exchange connected to the private fork.
    :return:
        Context restoring indexer methods after each test.
    """
    with gmx_fork_position_reads(), patch.object(ccxt_gmx_fork_open_close.subsquid, "get_position_changes", return_value=[]):
        yield


def _execute_order(web3: Web3, tx_hash: str, refund_address: HexAddress | None = None) -> TxReceipt:
    """Execute a GMX order as keeper given a creation transaction hash.

    Execute the submitted order through real keeper contract calls, then restore
    optional gas funding for the next wallet transaction.

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


def test_ccxt_cancel_order_lifecycle(
    ccxt_gmx_fork_open_close: GMX,
    web3_arbitrum_fork_ccxt_long: Web3,
    execution_buffer: int,
) -> None:
    """Full lifecycle: open + SL → fetch_orders → cancel_order → no pending SL.

    Verifies that the CCXT cancel_order() correctly cancels a pending SL order
    and that subsequent fetch_orders() no longer returns it. Reuse the same
    transaction for order-shape and position-versus-pending dispatch coverage.

    :param ccxt_gmx_fork_open_close:
        Funded exchange adapter on the private fork.
    :param web3_arbitrum_fork_ccxt_long:
        Chain connection used for keeper execution.
    :param execution_buffer:
        Execution-fee buffer for the bundled orders.
    :return:
        None; assertions cover the full order lifecycle.
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
    pending = [order for order in gmx.fetch_orders(symbol=symbol) if order["status"] == "open"]
    assert len(pending) == 1, f"Expected exactly one pending SL, got {len(pending)} orders"

    # Preserve order-shape coverage in the actual cancellation lifecycle.
    sl = pending[0]
    assert sl.get("status") == "open", "Pending order must have status='open'"
    assert sl.get("id") is not None, "Pending order must have an id (order key hex)"
    assert sl.get("type") == "stopLoss", f"Expected type='stopLoss', got {sl.get('type')!r}"
    assert sl.get("side") == "buy", "Long SL order must have side='buy'"
    assert sl.get("price", 0) > 0, "SL trigger price must be non-zero"

    info = sl.get("info", {})
    assert info.get("order_key") is not None, "info.order_key must be set"
    assert info.get("is_long") is True, "SL order must be for a long position"

    pending_only = gmx.fetch_open_orders(symbol=symbol, params={"pending_orders_only": True})
    assert {order["id"] for order in pending_only} == {order["id"] for order in pending}
    positions = gmx.fetch_open_orders(symbol=symbol)
    assert len(positions) == 1, "Default fetch_open_orders must return exactly the opened position"
    assert all(order.get("type") != "stopLoss" for order in positions)

    sl_key_hex = sl["id"]
    assert sl_key_hex.startswith("0x"), "Order key must be '0x'-prefixed hex"

    # Step 4: Cancel the SL order via CCXT interface
    cancel_result = gmx.cancel_order(sl_key_hex, symbol=symbol)
    assert cancel_result.get("status") == "cancelled", f"Expected status='cancelled', got {cancel_result.get('status')!r}"
    assert cancel_result.get("id") == sl_key_hex, "Cancelled order id must match the requested key"
    assert cancel_result["info"].get("tx_hash") is not None, "Cancel result must include tx_hash"

    # Step 5: Verify the order is gone
    assert gmx.fetch_open_orders(symbol=symbol, params={"pending_orders_only": True}) == []
    assert all(order["id"] != sl_key_hex or order["status"] != "open" for order in gmx.fetch_orders(symbol=symbol))


def test_ccxt_cancel_nonexistent_order(
    ccxt_gmx_fork_open_close: GMX,
) -> None:
    """Raise ``OrderNotFound`` for a key absent from the real DataStore.

    A well-formed fabricated key exercises missing-order handling without
    creating another position.

    :param ccxt_gmx_fork_open_close:
        Funded exchange adapter on the private fork.
    :return:
        None; the expected exception is asserted.
    """
    gmx = ccxt_gmx_fork_open_close

    # Fabricate a well-formed but non-existent order key
    fake_key = "0x" + "ab" * 32

    with pytest.raises(OrderNotFound):
        gmx.cancel_order(fake_key)


def test_ccxt_fetch_orders_empty_account(
    ccxt_gmx_fork_open_close: GMX,
) -> None:
    """Return no orders for an account without pending orders.

    Read the real DataStore for a funded account before it creates any order.

    :param ccxt_gmx_fork_open_close:
        Funded exchange adapter on the private fork.
    :return:
        None; empty-account behaviour is asserted.
    """
    gmx = ccxt_gmx_fork_open_close

    # No orders have been created yet, so this must be empty
    pending = gmx.fetch_orders()
    assert pending == [], f"Expected empty list, got {pending}"
