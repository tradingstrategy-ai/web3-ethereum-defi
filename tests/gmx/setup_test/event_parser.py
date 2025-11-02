"""Event log parsing utilities for GMX transactions.

This module provides functions to extract order keys, position keys, and other
important data from transaction logs emitted by GMX smart contracts.

Example:
    from tests.gmx.setup_test.event_parser import (
        extract_order_key_from_receipt,
        extract_position_key_from_receipt,
    )

    # After creating an order
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    order_key = extract_order_key_from_receipt(receipt)

    # After executing an order
    exec_receipt = web3.eth.wait_for_transaction_receipt(exec_tx_hash)
    position_key = extract_position_key_from_receipt(exec_receipt)
"""

import logging
from typing import Optional
from hexbytes import HexBytes
from eth_utils import keccak

logger = logging.getLogger(__name__)


def extract_order_key_from_receipt(receipt: dict) -> bytes:
    """Extract order key from OrderCreated event in transaction receipt.

    The OrderCreated event is emitted when an order is created via ExchangeRouter.
    The order key is the first indexed parameter.

    Event signature: OrderCreated(bytes32 indexed key, Order.Props order)

    Args:
        receipt: Transaction receipt from web3.eth.wait_for_transaction_receipt()

    Returns:
        Order key as bytes

    Raises:
        ValueError: If OrderCreated event not found in logs
    """
    # OrderCreated event signature
    order_created_signature = keccak(text="OrderCreated(bytes32,Order.Props)")

    logs = receipt.get("logs", [])

    for log in logs:
        topics = log.get("topics", [])

        if not topics:
            continue

        # Check if this is an OrderCreated event (first topic is the signature)
        if topics[0] == order_created_signature:
            # Second topic (index 1) is the order key (first indexed parameter)
            order_key = HexBytes(topics[1])
            logger.info(f"Extracted order key: {order_key.hex()}")
            return bytes(order_key)

    raise ValueError("OrderCreated event not found in transaction logs")


def extract_position_key_from_receipt(receipt: dict) -> bytes:
    """Extract position key from PositionIncrease event in transaction receipt.

    The PositionIncrease event is emitted when a position is created/increased.
    The position key is extracted from the event data.

    Event signature: PositionIncrease(bytes32 indexed key, ...)

    Args:
        receipt: Transaction receipt from web3.eth.wait_for_transaction_receipt()

    Returns:
        Position key as bytes

    Raises:
        ValueError: If PositionIncrease event not found in logs
    """
    # PositionIncrease event - note: signature may vary, try common variants
    position_increase_signatures = [
        keccak(text="PositionIncrease(bytes32,PositionIncreaseParams,uint256)"),
        keccak(text="PositionIncrease(bytes32,uint256)"),
    ]

    logs = receipt.get("logs", [])

    for log in logs:
        topics = log.get("topics", [])

        if not topics:
            continue

        # Check if this is a PositionIncrease event
        if any(topics[0] == sig for sig in position_increase_signatures):
            # Second topic is the position key (first indexed parameter)
            position_key = HexBytes(topics[1])
            logger.info(f"Extracted position key: {position_key.hex()}")
            return bytes(position_key)

    raise ValueError("PositionIncrease event not found in transaction logs")


def extract_position_decrease_key_from_receipt(receipt: dict) -> bytes:
    """Extract position key from PositionDecrease event in transaction receipt.

    Args:
        receipt: Transaction receipt

    Returns:
        Position key as bytes

    Raises:
        ValueError: If PositionDecrease event not found in logs
    """
    position_decrease_signatures = [
        keccak(text="PositionDecrease(bytes32,PositionDecreaseParams,uint256)"),
        keccak(text="PositionDecrease(bytes32,uint256)"),
    ]

    logs = receipt.get("logs", [])

    for log in logs:
        topics = log.get("topics", [])

        if not topics:
            continue

        if any(topics[0] == sig for sig in position_decrease_signatures):
            position_key = HexBytes(topics[1])
            logger.info(f"Extracted position key from decrease: {position_key.hex()}")
            return bytes(position_key)

    raise ValueError("PositionDecrease event not found in transaction logs")


def extract_event_by_signature(receipt: dict, event_signature: str) -> Optional[dict]:
    """Extract first event matching signature from receipt.

    Args:
        receipt: Transaction receipt
        event_signature: Event signature string (e.g., "Transfer(address,address,uint256)")

    Returns:
        Event log dict or None if not found
    """
    signature = keccak(text=event_signature)
    logs = receipt.get("logs", [])

    for log in logs:
        topics = log.get("topics", [])
        if topics and topics[0] == signature:
            return log

    return None


def extract_all_events_by_signature(receipt: dict, event_signature: str) -> list:
    """Extract all events matching signature from receipt.

    Args:
        receipt: Transaction receipt
        event_signature: Event signature string

    Returns:
        List of matching event logs
    """
    signature = keccak(text=event_signature)
    logs = receipt.get("logs", [])
    matching_logs = []

    for log in logs:
        topics = log.get("topics", [])
        if topics and topics[0] == signature:
            matching_logs.append(log)

    return matching_logs


def get_event_topic(event_signature: str) -> bytes:
    """Get the topic hash for an event signature.

    Useful for filtering logs when querying events.

    Args:
        event_signature: Event signature string (e.g., "OrderCreated(bytes32,Order.Props)")

    Returns:
        Topic hash as bytes
    """
    return keccak(text=event_signature)


def extract_position_key_from_receipt_generic(receipt: dict) -> bytes:
    """Extract position key from receipt by looking for any indexed bytes32 parameter.

    This is a more robust approach that works even if event signatures vary.
    It looks for logs from GMX contracts and extracts position keys.

    Args:
        receipt: Transaction receipt

    Returns:
        Position key as bytes

    Raises:
        ValueError: If no suitable event found
    """
    logs = receipt.get("logs", [])

    # Try to find PositionIncrease or PositionDecrease
    for log in logs:
        topics = log.get("topics", [])

        if len(topics) < 2:
            continue

        # Check if this looks like a GMX position event
        # (has indexed parameter and comes from a GMX contract)
        address = log.get("address", "").lower()

        # Common GMX contract addresses (optional check)
        # For now, just check if we have at least 2 topics (signature + indexed key)
        if len(topics) >= 2:
            # Try to extract the key
            potential_key = HexBytes(topics[1])

            # Position keys should be 32 bytes
            if len(potential_key) == 32:
                logger.info(f"Extracted potential position key: {potential_key.hex()}")
                return bytes(potential_key)

    raise ValueError("Could not extract position key from receipt")
