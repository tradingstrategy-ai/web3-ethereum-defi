"""GMX order status tracking.

This module provides utilities for tracking GMX order execution status.
GMX uses a two-phase order process:

1. **Order Creation** - User submits order, receives OrderCreated event
2. **Keeper Execution** - Keeper executes order (separate tx), receives OrderExecuted or OrderCancelled

The functions in this module help track when orders transition from pending
to executed/cancelled by querying the DataStore and EventEmitter contracts.

Example usage::

    from eth_defi.gmx.order_tracking import check_order_status

    # Check if order has been executed
    result = check_order_status(web3, order_key, "arbitrum")

    if result.is_pending:
        print("Order still waiting for keeper execution")
    elif result.execution_receipt:
        print(f"Order executed in tx: {result.execution_tx_hash}")
"""

import logging
from dataclasses import dataclass
from typing import Literal

from eth_typing import HexAddress
from web3 import Web3

logger = logging.getLogger(__name__)

#: GMX DataStore ORDER_LIST key - keccak256("ORDER_LIST")
#: Pending orders are stored in this list and removed after execution/cancellation
ORDER_LIST_KEY = bytes.fromhex("86f7cfd5d8f8404e5145c91bebb8484657420159dabd0753d6a59f3de3f7b8c1")


@dataclass(slots=True)
class OrderStatusResult:
    """Result of checking order status in GMX DataStore.

    :ivar is_pending:
        Whether the order is still pending (waiting for keeper execution)

    :ivar execution_tx_hash:
        Transaction hash of the keeper execution (if order is no longer pending)

    :ivar execution_receipt:
        Full transaction receipt of the keeper execution

    :ivar execution_block:
        Block number where the order was executed/cancelled
    """

    #: Whether the order is still pending
    is_pending: bool

    #: Transaction hash of keeper execution
    execution_tx_hash: str | None = None

    #: Full transaction receipt from keeper execution
    execution_receipt: dict | None = None

    #: Block number of execution
    execution_block: int | None = None


def check_order_status(
    web3: Web3,
    order_key: bytes,
    chain: str,
    search_blocks: int = 1000,
) -> OrderStatusResult:
    """Check if a GMX order is still pending or has been executed/cancelled.

    This function first checks the DataStore to see if the order exists in the
    pending orders list. If not, it queries the EventEmitter logs to find the
    execution transaction.

    :param web3:
        Web3 instance connected to the appropriate chain

    :param order_key:
        The 32-byte order key from the OrderCreated event

    :param chain:
        Chain name ("arbitrum", "avalanche", or "arbitrum_sepolia")

    :param search_blocks:
        Number of recent blocks to search for execution events (default: 1000)

    :return:
        OrderStatusResult with pending status and execution details if available

    Example::

        from eth_defi.gmx.order_tracking import check_order_status
        from eth_defi.gmx.events import extract_order_key_from_receipt

        # After order creation
        order_key = extract_order_key_from_receipt(web3, creation_receipt)

        # Poll for execution
        while True:
            result = check_order_status(web3, order_key, "arbitrum")
            if not result.is_pending:
                break
            time.sleep(2)

        # Now verify the execution
        if result.execution_receipt:
            verification = verify_gmx_order_execution(web3, result.execution_receipt, order_key)
    """
    from eth_defi.gmx.contracts import get_contract_addresses, get_datastore_contract
    from eth_defi.gmx.events import decode_gmx_event

    # 1. Check DataStore if order still exists in pending list
    datastore = get_datastore_contract(web3, chain)

    try:
        is_pending = datastore.functions.containsBytes32(ORDER_LIST_KEY, order_key).call()
    except Exception as e:
        logger.warning(
            "Failed to query DataStore for order %s: %s",
            order_key.hex()[:16],
            e,
        )
        # Assume still pending if we can't query
        return OrderStatusResult(is_pending=True)

    if is_pending:
        logger.debug(
            "Order %s is still pending in DataStore",
            order_key.hex()[:16],
        )
        return OrderStatusResult(is_pending=True)

    # 2. Order no longer pending - find execution receipt via EventEmitter logs
    logger.debug(
        "Order %s no longer in DataStore, searching for execution event",
        order_key.hex()[:16],
    )

    addresses = get_contract_addresses(chain)
    event_emitter = addresses.eventemitter

    current_block = web3.eth.block_number
    from_block = max(0, current_block - search_blocks)

    try:
        # Query all logs from EventEmitter - we need to decode each event
        # to find the matching order_key since GMX's EventLog1/EventLog2 events
        # store order_key in decoded args (topic1), not in raw indexed topics
        logs = web3.eth.get_logs(
            {
                "address": event_emitter,
                "fromBlock": from_block,
                "toBlock": current_block,
            }
        )
    except Exception as e:
        logger.warning(
            "Failed to query EventEmitter logs for order %s: %s",
            order_key.hex()[:16],
            e,
        )
        return OrderStatusResult(is_pending=False)

    # Find OrderExecuted, OrderCancelled, or OrderFrozen event matching our order_key
    for log in logs:
        try:
            event = decode_gmx_event(web3, log)
            if not event:
                continue

            if event.event_name not in ("OrderExecuted", "OrderCancelled", "OrderFrozen"):
                continue

            # Check if this event matches our order_key
            # Order key can be in topic1 (for EventLog1/EventLog2) or in bytes32_items["key"]
            event_order_key = event.topic1 or event.get_bytes32("key")
            if event_order_key != order_key:
                continue

            tx_hash = log["transactionHash"]
            if isinstance(tx_hash, bytes):
                tx_hash = tx_hash.hex()

            receipt = web3.eth.get_transaction_receipt(tx_hash)

            logger.info(
                "Found %s event for order %s in tx %s (block %d)",
                event.event_name,
                order_key.hex()[:16],
                tx_hash[:16],
                log["blockNumber"],
            )

            return OrderStatusResult(
                is_pending=False,
                execution_tx_hash=tx_hash,
                execution_receipt=dict(receipt),
                execution_block=log["blockNumber"],
            )
        except Exception as e:
            logger.debug("Error decoding log: %s", e)
            continue

    # Order removed from DataStore but no execution event found
    # This shouldn't happen in normal operation
    logger.warning(
        "Order %s removed from DataStore but no execution event found in last %d blocks",
        order_key.hex()[:16],
        search_blocks,
    )
    return OrderStatusResult(is_pending=False)


def is_order_pending(
    web3: Web3,
    order_key: bytes,
    chain: str,
) -> bool:
    """Quick check if an order is still pending in the DataStore.

    This is a lighter-weight alternative to check_order_status() when you
    only need to know if the order is still pending, without needing the
    execution receipt.

    :param web3:
        Web3 instance

    :param order_key:
        The 32-byte order key

    :param chain:
        Chain name

    :return:
        True if order is still pending, False if executed/cancelled
    """
    from eth_defi.gmx.contracts import get_datastore_contract

    datastore = get_datastore_contract(web3, chain)

    try:
        return datastore.functions.containsBytes32(ORDER_LIST_KEY, order_key).call()
    except Exception as e:
        logger.warning(
            "Failed to check if order %s is pending: %s",
            order_key.hex()[:16],
            e,
        )
        return True  # Assume pending if query fails
