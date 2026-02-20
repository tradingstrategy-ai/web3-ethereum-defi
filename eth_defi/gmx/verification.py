"""GMX order execution verification.

This module provides verification utilities for GMX order executions. GMX keeper
transactions can have receipt.status == 1 even when the order is cancelled or
frozen at the protocol level. This module detects such failures by parsing
GMX events.

Key functions:

- :py:func:`verify_gmx_order_execution`: Main verification function
- :py:func:`raise_if_order_failed`: Convenience wrapper that raises on failure

Example usage::

    from eth_defi.gmx.verification import verify_gmx_order_execution, raise_if_order_failed

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    # Option 1: Check and raise if failed
    result = raise_if_order_failed(web3, receipt, tx_hash)

    # Option 2: Check without raising
    result = verify_gmx_order_execution(web3, receipt)
    if not result.success:
        print(f"Order failed: {result.decoded_error}")

"""

import logging
from dataclasses import dataclass, field
from typing import Literal

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.gmx.ccxt.errors import GMXOrderFailedException
from eth_defi.gmx.events import (
    GMX_PRICE_PRECISION,
    GMX_USD_PRECISION,
    OrderExecutionResult,
    OrderFees,
    decode_gmx_events,
    extract_order_execution_result,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GMXOrderVerificationResult:
    """Result of GMX order execution verification.

    This dataclass contains the verification result and extracted execution details
    from GMX events.

    :ivar success:
        Whether the order executed successfully

    :ivar order_key:
        The order key (32-byte identifier)

    :ivar status:
        Order status: "executed", "cancelled", or "frozen"

    :ivar account:
        Account address that owns the order

    :ivar execution_price:
        Execution price (converted to float USD)

    :ivar size_delta_usd:
        Size delta in USD (converted to float)

    :ivar pnl_usd:
        Realised PnL in USD (converted to float)

    :ivar price_impact_usd:
        Price impact in USD (converted to float)

    :ivar fees:
        Execution fees from GMX

    :ivar reason:
        Error reason string (for failed orders)

    :ivar decoded_error:
        Decoded error message

    :ivar error_selector:
        4-byte error selector hex string

    :ivar event_count:
        Number of GMX events in the transaction

    :ivar event_names:
        List of event names found
    """

    #: Whether the order executed successfully
    success: bool

    #: The order key (32-byte identifier)
    order_key: bytes | None = None

    #: Order status: "executed", "cancelled", or "frozen"
    status: Literal["executed", "cancelled", "frozen"] | None = None

    #: Account address that owns the order
    account: HexAddress | None = None

    #: Execution price (converted to float USD)
    execution_price: float | None = None

    #: Size delta in USD (converted to float)
    size_delta_usd: float | None = None

    #: Size delta in tokens (raw value)
    size_delta_in_tokens: int | None = None

    #: Collateral delta amount (can be negative)
    collateral_delta: int | None = None

    #: Realised PnL in USD (converted to float)
    pnl_usd: float | None = None

    #: Price impact in USD (converted to float)
    price_impact_usd: float | None = None

    #: Whether the position is long
    is_long: bool | None = None

    #: Position key (if position was modified)
    position_key: bytes | None = None

    #: Collateral token address (actual, from events - not assumed from market)
    collateral_token: HexAddress | None = None

    #: Collateral token price in raw 30-decimal GMX format (from events)
    collateral_token_price: int | None = None

    #: Execution fees
    fees: OrderFees | None = None

    #: Error reason string (for failed orders)
    reason: str | None = None

    #: Raw error reason bytes
    reason_bytes: bytes | None = None

    #: Decoded error message
    decoded_error: str | None = None

    #: Error selector (4-byte hex string)
    error_selector: str | None = None

    #: Number of GMX events in the transaction
    event_count: int = 0

    #: List of event names found
    event_names: list[str] = field(default_factory=list)


def verify_gmx_order_execution(
    web3: Web3,
    receipt: dict,
    order_key: bytes | None = None,
) -> GMXOrderVerificationResult:
    """Verify GMX order execution from transaction receipt.

    This function parses GMX events from the receipt to determine the true
    order execution status. A transaction can have receipt.status == 1 but
    still contain OrderCancelled or OrderFrozen events indicating failure.

    Success pattern:

    - Has OrderExecuted event
    - Has PositionIncrease or PositionDecrease event
    - Typically 20-32+ GMX events

    Failure pattern:

    - Has OrderCancelled or OrderFrozen event
    - No OrderExecuted event
    - Typically 6-10 GMX events
    - Error reason in reasonBytes field

    :param web3:
        Web3 instance connected to the appropriate chain

    :param receipt:
        Transaction receipt from keeper execution

    :param order_key:
        Optional order key to filter for. If not provided, returns result
        for the first order event found.

    :return:
        GMXOrderVerificationResult with success flag and execution details

    Example::

        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        result = verify_gmx_order_execution(web3, receipt)

        if not result.success:
            logger.error(
                "Order %s failed: %s",
                result.order_key.hex() if result.order_key else "unknown",
                result.decoded_error or result.reason,
            )
    """
    # Collect all events for analysis
    events = list(decode_gmx_events(web3, receipt))
    event_names = [e.event_name for e in events]

    result = GMXOrderVerificationResult(
        success=False,
        event_count=len(events),
        event_names=event_names,
    )

    if not events:
        logger.warning("No GMX events found in receipt - cannot verify order execution")
        return result

    # Use existing extract function to get order result
    order_result = extract_order_execution_result(web3, receipt, order_key)

    if not order_result:
        logger.warning(
            "No order execution events found in receipt. Events: %s",
            event_names,
        )
        return result

    # Populate result from OrderExecutionResult
    result.order_key = order_result.order_key
    result.status = order_result.status
    result.account = order_result.account
    result.is_long = order_result.is_long
    result.position_key = order_result.position_key
    result.collateral_token = order_result.collateral_token
    result.collateral_token_price = order_result.collateral_token_price
    result.fees = order_result.fees
    result.reason = order_result.reason
    result.reason_bytes = order_result.reason_bytes
    result.decoded_error = order_result.decoded_error

    # Extract error selector from reason_bytes
    if order_result.reason_bytes and len(order_result.reason_bytes) >= 4:
        result.error_selector = order_result.reason_bytes[:4].hex()

    # Check for success
    if order_result.status == "executed":
        result.success = True

        # Store raw execution_price (30 decimals) for later conversion
        # Conversion to USD will be done in exchange.py using token-specific decimals
        if order_result.execution_price:
            result.execution_price = order_result.execution_price

        if order_result.size_delta_usd:
            result.size_delta_usd = order_result.size_delta_usd / GMX_USD_PRECISION

        result.size_delta_in_tokens = order_result.size_delta_in_tokens
        result.collateral_delta = order_result.collateral_delta

        if order_result.pnl_usd is not None:
            result.pnl_usd = order_result.pnl_usd / GMX_USD_PRECISION

        if order_result.price_impact_usd is not None:
            result.price_impact_usd = order_result.price_impact_usd / GMX_USD_PRECISION

        logger.debug(
            "Order executed successfully: key=%s, price=%.2f, size_usd=%.2f",
            result.order_key.hex()[:16] if result.order_key else "unknown",
            result.execution_price or 0,
            result.size_delta_usd or 0,
        )

    else:
        # Order was cancelled or frozen
        result.success = False

        logger.warning(
            "Order %s: key=%s, reason=%s, decoded=%s, selector=%s",
            result.status,
            result.order_key.hex()[:16] if result.order_key else "unknown",
            result.reason,
            result.decoded_error,
            result.error_selector,
        )

    return result


def raise_if_order_failed(
    web3: Web3,
    receipt: dict,
    tx_hash: str,
    order_key: bytes | None = None,
) -> GMXOrderVerificationResult:
    """Verify order execution and raise exception if failed.

    Convenience function that combines verification and exception raising.
    Use this in the CCXT exchange wrapper to ensure failed orders are not
    treated as successful.

    :param web3:
        Web3 instance

    :param receipt:
        Transaction receipt

    :param tx_hash:
        Transaction hash (for error reporting)

    :param order_key:
        Optional order key to filter for

    :return:
        GMXOrderVerificationResult if order succeeded

    :raises GMXOrderFailedException:
        If order was cancelled or frozen

    Example::

        from eth_defi.gmx.verification import raise_if_order_failed
        from eth_defi.gmx.ccxt.errors import GMXOrderFailedException

        try:
            result = raise_if_order_failed(web3, receipt, tx_hash)
            # Order succeeded - use result.execution_price, result.size_delta_usd, etc.
        except GMXOrderFailedException as e:
            # Order failed at GMX level despite tx success
            print(f"Order failed: {e.decoded_error}")
    """
    result = verify_gmx_order_execution(web3, receipt, order_key)

    if not result.success and result.status in ("cancelled", "frozen"):
        raise GMXOrderFailedException(
            order_key=result.order_key or b"",
            status=result.status,
            reason=result.reason,
            decoded_error=result.decoded_error,
            error_selector=result.error_selector,
            reason_bytes=result.reason_bytes,
            tx_hash=tx_hash,
            receipt=receipt,
        )

    return result
