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

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.gmx.constants import (
    SUBSQUID_ORDER_TRACKING_BACKOFF_MULTIPLIER,
    SUBSQUID_ORDER_TRACKING_INITIAL_DELAY,
    SUBSQUID_ORDER_TRACKING_MAX_DELAY,
    SUBSQUID_ORDER_TRACKING_MAX_RETRIES,
    SUBSQUID_ORDER_TRACKING_TIMEOUT,
)
from eth_defi.gmx.contracts import get_contract_addresses, get_datastore_contract
from eth_defi.gmx.events import _get_event_emitter_contract, decode_gmx_event
from eth_defi.gmx.graphql.client import GMXSubsquidClient
from eth_defi.provider.log_block_range import get_logs_max_block_range

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


def _scan_event_emitter_logs_chunked(
    web3: Web3,
    event_emitter: HexAddress,
    order_key: bytes,
    from_block: int,
    to_block: int,
    chunk_size: int | None = None,
) -> OrderStatusResult | None:
    """Scan EventEmitter logs in chunks for order execution event.

    Uses chunked queries to avoid RPC timeouts on large block ranges.

    :param web3:
        Web3 instance
    :param event_emitter:
        EventEmitter contract address
    :param order_key:
        The 32-byte order key to search for
    :param from_block:
        Start block for scanning
    :param to_block:
        End block for scanning
    :param chunk_size:
        Number of blocks per query. If None, uses get_logs_max_block_range().
    :return:
        OrderStatusResult if found, None otherwise
    """
    if chunk_size is None:
        chunk_size = get_logs_max_block_range(web3)

    total_blocks = to_block - from_block + 1
    logger.info(
        "Scanning %d blocks for order %s in chunks of %d",
        total_blocks,
        order_key.hex()[:16],
        chunk_size,
    )

    # Build the EventEmitter contract once for the entire scan to avoid
    # repeated HTTP requests to the GMX contract registry per log entry
    emitter_contract = _get_event_emitter_contract(web3)

    for chunk_start in range(from_block, to_block + 1, chunk_size):
        chunk_end = min(chunk_start + chunk_size - 1, to_block)

        logger.debug(
            "Scanning blocks %d-%d for order %s",
            chunk_start,
            chunk_end,
            order_key.hex()[:16],
        )

        try:
            logs = web3.eth.get_logs(
                {
                    "address": event_emitter,
                    "fromBlock": chunk_start,
                    "toBlock": chunk_end,
                }
            )

            for log in logs:
                try:
                    event = decode_gmx_event(web3, log, event_emitter_contract=emitter_contract)
                    if not event:
                        continue

                    if event.event_name not in ("OrderExecuted", "OrderCancelled", "OrderFrozen"):
                        continue

                    event_order_key = event.topic1 or event.get_bytes32("key")
                    if event_order_key != order_key:
                        continue

                    tx_hash = log["transactionHash"]
                    if isinstance(tx_hash, bytes):
                        tx_hash = tx_hash.hex()

                    receipt = web3.eth.get_transaction_receipt(tx_hash)

                    logger.info(
                        "Found %s event for order %s in block %d via chunked scan",
                        event.event_name,
                        order_key.hex()[:16],
                        log["blockNumber"],
                    )

                    return OrderStatusResult(
                        is_pending=False,
                        execution_tx_hash=tx_hash,
                        execution_receipt=dict(receipt),
                        execution_block=log["blockNumber"],
                    )
                except Exception as e:
                    logger.warning("Error decoding log: %s", e)
                    continue

        except Exception as e:
            logger.warning(
                "Error scanning blocks %d-%d for order %s: %s",
                chunk_start,
                chunk_end,
                order_key.hex()[:16],
                e,
            )
            # Continue to next chunk - partial failure is acceptable

    return None


def _query_subsquid_with_extended_retry(
    web3: Web3,
    chain: str,
    order_key_hex: str,
    timeout: int,
    max_retries: int,
    initial_delay: float,
    wait_for_indexer: bool = False,
    wait_for_indexer_timeout: float = 60.0,
) -> OrderStatusResult | None:
    """Query Subsquid with extended wait-then-retry logic.

    :param web3:
        Web3 instance for fetching receipts
    :param chain:
        Chain name
    :param order_key_hex:
        Order key as hex string (with 0x prefix)
    :param timeout:
        Timeout per Subsquid query
    :param max_retries:
        Maximum retry attempts
    :param initial_delay:
        Initial delay between retries
    :param wait_for_indexer:
        If True, keep polling until indexer catches up (for fresh orders)
    :param wait_for_indexer_timeout:
        Maximum time to wait for indexer to catch up
    :return:
        OrderStatusResult if found, None otherwise
    """
    retry_delay = initial_delay

    if wait_for_indexer:
        start_time = time.time()
        attempt = 0

        while time.time() - start_time < wait_for_indexer_timeout:
            attempt += 1
            try:
                client = GMXSubsquidClient(chain=chain)
                action = client.get_trade_action_by_order_key(
                    order_key_hex,
                    timeout_seconds=timeout,
                    poll_interval=0.5,
                )
                if action:
                    return _build_result_from_subsquid_action(web3, action, order_key_hex)

                elapsed = time.time() - start_time
                remaining = wait_for_indexer_timeout - elapsed

                logger.debug(
                    "Subsquid not caught up for order %s, waiting %.1fs (%.1fs remaining)",
                    order_key_hex[:18],
                    retry_delay,
                    remaining,
                )

                time.sleep(min(retry_delay, remaining))
                retry_delay = min(retry_delay * SUBSQUID_ORDER_TRACKING_BACKOFF_MULTIPLIER, SUBSQUID_ORDER_TRACKING_MAX_DELAY)

            except Exception as e:
                logger.warning(
                    "Subsquid query failed (attempt %d): %s, waiting %.1fs",
                    attempt,
                    e,
                    retry_delay,
                )
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * SUBSQUID_ORDER_TRACKING_BACKOFF_MULTIPLIER, SUBSQUID_ORDER_TRACKING_MAX_DELAY)

        return None

    else:
        for attempt in range(max_retries):
            try:
                client = GMXSubsquidClient(chain=chain)
                action = client.get_trade_action_by_order_key(
                    order_key_hex,
                    timeout_seconds=timeout,
                    poll_interval=0.5,
                )
                if action:
                    return _build_result_from_subsquid_action(web3, action, order_key_hex)
                return None

            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Subsquid query failed (attempt %d/%d): %s, retrying in %.1fs",
                        attempt + 1,
                        max_retries,
                        e,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * SUBSQUID_ORDER_TRACKING_BACKOFF_MULTIPLIER, SUBSQUID_ORDER_TRACKING_MAX_DELAY)
                else:
                    logger.warning("Subsquid query failed after %d attempts: %s", max_retries, e)

        return None


def _build_result_from_subsquid_action(
    web3: Web3,
    action: dict,
    order_key_hex: str,
) -> OrderStatusResult:
    """Build OrderStatusResult from Subsquid action data.

    :param web3:
        Web3 instance for fetching receipts
    :param action:
        Trade action dict from Subsquid
    :param order_key_hex:
        Order key for logging
    :return:
        OrderStatusResult with execution details
    """
    tx_hash = action.get("transaction", {}).get("hash")
    event_name = action.get("eventName", "unknown")

    logger.info(
        "Found %s event for order %s via Subsquid (tx: %s)",
        event_name,
        order_key_hex[:18],
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


def check_order_status(
    web3: Web3,
    order_key: bytes,
    chain: str,
    search_blocks: int = 1000,
    subsquid_timeout: int = SUBSQUID_ORDER_TRACKING_TIMEOUT,
    subsquid_max_retries: int = SUBSQUID_ORDER_TRACKING_MAX_RETRIES,
    subsquid_initial_delay: float = SUBSQUID_ORDER_TRACKING_INITIAL_DELAY,
    creation_block: int | None = None,
    wait_for_indexer: bool = False,
    wait_for_indexer_timeout: float = 60.0,
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
        Number of recent blocks to search for execution events via RPC fallback.
        Only used if creation_block is not provided. (default: 1000)

    :param subsquid_timeout:
        Timeout in seconds for each Subsquid query attempt (default: 10)

    :param subsquid_max_retries:
        Maximum retry attempts for Subsquid queries (default: 5)

    :param subsquid_initial_delay:
        Initial delay between Subsquid retries in seconds (default: 2.0)

    :param creation_block:
        Block number where the order was created. If provided, log scanning
        will start from this block instead of (current_block - search_blocks).
        This enables accurate scanning after bot restarts.

    :param wait_for_indexer:
        If True, keep polling Subsquid until indexer catches up (for fresh orders).
        Useful when checking status immediately after order creation.

    :param wait_for_indexer_timeout:
        Maximum time to wait for Subsquid indexer to catch up (default: 60s)

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

    # Try Subsquid with extended retry logic
    result = _query_subsquid_with_extended_retry(
        web3=web3,
        chain=chain,
        order_key_hex=order_key_hex,
        timeout=subsquid_timeout,
        max_retries=subsquid_max_retries,
        initial_delay=subsquid_initial_delay,
        wait_for_indexer=wait_for_indexer,
        wait_for_indexer_timeout=wait_for_indexer_timeout,
    )

    if result is not None:
        return result

    logger.debug(
        "Order %s not found in Subsquid, falling back to chunked log scan",
        order_key.hex()[:16],
    )

    # 3. Fallback: scan EventEmitter logs via RPC with chunking
    addresses = get_contract_addresses(chain)
    event_emitter = addresses.eventemitter

    current_block = web3.eth.block_number

    # Use creation_block if provided, otherwise fall back to search_blocks
    if creation_block is not None:
        from_block = creation_block
        logger.debug(
            "Order %s: using creation_block %d for log scan (range: %d blocks)",
            order_key.hex()[:16],
            creation_block,
            current_block - creation_block,
        )
    else:
        from_block = max(0, current_block - search_blocks)
        logger.debug(
            "Order %s: no creation_block, using search_blocks=%d (from %d)",
            order_key.hex()[:16],
            search_blocks,
            from_block,
        )

    # Use chunked scanning to avoid RPC timeouts on large block ranges
    result = _scan_event_emitter_logs_chunked(
        web3=web3,
        event_emitter=event_emitter,
        order_key=order_key,
        from_block=from_block,
        to_block=current_block,
    )

    if result is not None:
        return result

    # Order removed from DataStore but no execution event found
    # This shouldn't happen in normal operation
    logger.warning(
        "Order %s removed from DataStore but no execution event found. Subsquid: no result. Log scan: blocks %d-%d",
        order_key.hex()[:16],
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
