"""GMX order status tracking.

This module provides utilities for tracking GMX order execution status.
GMX uses a two-phase order process:

1. **Order Creation** - User submits order, receives OrderCreated event
2. **Keeper Execution** - Keeper executes order (separate tx), receives OrderExecuted or OrderCancelled

The functions in this module help track when orders transition from pending
to executed/cancelled by:

1. **Primary method**: Querying Subsquid GraphQL indexer (faster, more reliable)
2. **Fallback method**: Scanning EventEmitter logs via RPC (if Subsquid unavailable)

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
import time
from dataclasses import dataclass
from typing import Literal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.gmx.contracts import get_contract_addresses, get_datastore_contract
from eth_defi.gmx.events import decode_gmx_event
from eth_defi.gmx.graphql.client import GMXSubsquidClient

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
    subsquid_timeout: int = 5,
) -> OrderStatusResult:
    """Check if a GMX order is still pending or has been executed/cancelled.

    This function first checks the DataStore to see if the order exists in the
    pending orders list. If not, it queries Subsquid GraphQL indexer (primary)
    or falls back to scanning EventEmitter logs via RPC.

    :param web3:
        Web3 instance connected to the appropriate chain

    :param order_key:
        The 32-byte order key from the OrderCreated event

    :param chain:
        Chain name ("arbitrum", "avalanche", or "arbitrum_sepolia")

    :param search_blocks:
        Number of recent blocks to search for execution events via RPC fallback (default: 1000)

    :param subsquid_timeout:
        Timeout in seconds for Subsquid query (default: 5). This is a quick check,
        not waiting for indexer to catch up.

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

    # 2. Order no longer pending - query Subsquid for execution details (primary method)
    logger.debug(
        "Order %s no longer in DataStore, querying Subsquid for execution event",
        order_key.hex()[:16],
    )

    order_key_hex = "0x" + order_key.hex()

    # Retry logic for Subsquid GraphQL with exponential backoff
    max_retries = 3
    retry_delay = 1.0

    for attempt in range(max_retries):
        try:
            client = GMXSubsquidClient(chain=chain)
            action = client.get_trade_action_by_order_key(
                order_key_hex,
                timeout_seconds=subsquid_timeout,
                poll_interval=0.5,
            )

            if action:
                tx_hash = action.get("transaction", {}).get("hash")
                event_name = action.get("eventName", "unknown")

                logger.info(
                    "Found %s event for order %s via Subsquid (tx: %s)",
                    event_name,
                    order_key.hex()[:16],
                    tx_hash[:16] if tx_hash else "unknown",
                )

                # Get full receipt for verification
                execution_receipt = None
                execution_block = None
                if tx_hash:
                    try:
                        receipt = web3.eth.get_transaction_receipt(tx_hash)
                        execution_receipt = dict(receipt)
                        execution_block = receipt.get("blockNumber")
                    except Exception as e:
                        logger.warning("Could not fetch receipt for tx %s: %s", tx_hash, e)

                return OrderStatusResult(
                    is_pending=False,
                    execution_tx_hash=tx_hash,
                    execution_receipt=execution_receipt,
                    execution_block=execution_block,
                )

            # No action found, break out of retry loop (this is not a failure)
            logger.debug(
                "Order %s not found in Subsquid within %ds, falling back to log scan",
                order_key.hex()[:16],
                subsquid_timeout,
            )
            break

        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    "Subsquid query failed for order %s (attempt %d/%d): %s, retrying in %.1fs",
                    order_key.hex()[:16],
                    attempt + 1,
                    max_retries,
                    e,
                    retry_delay,
                )
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logger.warning(
                    "Subsquid query failed for order %s after %d attempts: %s, falling back to log scan",
                    order_key.hex()[:16],
                    max_retries,
                    e,
                )

    # 3. Fallback: scan EventEmitter logs via RPC
    logger.debug(
        "Order %s: scanning EventEmitter logs (blocks %d)",
        order_key.hex()[:16],
        search_blocks,
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

        logger.debug(
            "Order %s: retrieved %d logs from blocks %d-%d",
            order_key.hex()[:16],
            len(logs),
            from_block,
            current_block,
        )

    except Exception as e:
        logger.warning(
            "Failed to query EventEmitter logs for order %s: %s",
            order_key.hex()[:16],
            e,
        )
        return OrderStatusResult(is_pending=False)

    # Find OrderExecuted, OrderCancelled, or OrderFrozen event matching our order_key
    events_decoded = 0
    events_matched_type = 0

    for log in logs:
        try:
            event = decode_gmx_event(web3, log)
            if not event:
                continue

            events_decoded += 1

            if event.event_name not in ("OrderExecuted", "OrderCancelled", "OrderFrozen"):
                continue

            events_matched_type += 1

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
                "Found %s event for order %s in tx %s (block %d) via log scan",
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
        "Order %s removed from DataStore but no execution event found. Subsquid: no result within %ds. Log scan: %d logs, %d decoded, %d matched type, blocks %d-%d",
        order_key.hex()[:16],
        subsquid_timeout,
        len(logs),
        events_decoded,
        events_matched_type,
        from_block,
        current_block,
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
