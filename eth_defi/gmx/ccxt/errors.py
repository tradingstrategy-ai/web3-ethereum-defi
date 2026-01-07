"""Custom exceptions for GMX CCXT exchange.

This module defines GMX-specific exceptions that extend CCXT's base error classes.
"""

from ccxt.base.errors import ExchangeError
from datetime import datetime


class InsufficientHistoricalDataError(ExchangeError):
    """Raised when GMX returns insufficient historical data for requested time range.

    This typically occurs when:
    1. Backtesting requests data older than GMX's retention period
    2. The market didn't exist for the entire requested time range
    3. There are gaps in historical data due to API issues

    Attributes:
        symbol: Market symbol that was requested
        timeframe: Candlestick interval requested
        requested_start: Unix timestamp (ms) of the requested start time
        available_start: Unix timestamp (ms) of the earliest available data
        available_end: Unix timestamp (ms) of the latest available data
        candles_received: Number of candles actually received
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        requested_start: int | None,
        available_start: int | None,
        available_end: int | None,
        candles_received: int,
    ):
        # Store attributes
        self.symbol = symbol
        self.timeframe = timeframe
        self.requested_start = requested_start
        self.available_start = available_start
        self.available_end = available_end
        self.candles_received = candles_received

        # Build human-readable message
        message = self._build_message()
        super().__init__(message)

    def _build_message(self) -> str:
        """Build clear, actionable error message with dates and timestamps."""
        lines = [
            f"Insufficient historical data for {self.symbol} ({self.timeframe} timeframe)",
            "",
            f"Candles received: {self.candles_received}",
        ]

        # Show requested time
        if self.requested_start:
            dt = datetime.fromtimestamp(self.requested_start / 1000).strftime("%Y-%m-%d %H:%M:%S UTC")
            lines.append(f"Requested data from: {dt} (timestamp: {self.requested_start})")

        # Show available time range
        if self.available_start and self.available_end:
            start_dt = datetime.fromtimestamp(self.available_start / 1000).strftime("%Y-%m-%d %H:%M:%S UTC")
            end_dt = datetime.fromtimestamp(self.available_end / 1000).strftime("%Y-%m-%d %H:%M:%S UTC")
            lines.extend(
                [
                    "",
                    f"Available data range:",
                    f"  From: {start_dt} (timestamp: {self.available_start})",
                    f"  To:   {end_dt} (timestamp: {self.available_end})",
                ]
            )
        elif self.candles_received == 0:
            lines.append("No historical data available for this symbol/timeframe")

        # Actionable suggestion
        lines.extend(["", "Suggestion:"])
        if self.available_start and self.requested_start and self.available_start > self.requested_start:
            start_dt = datetime.fromtimestamp(self.available_start / 1000).strftime("%Y-%m-%d %H:%M:%S UTC")
            lines.append(f"Adjust your backtest start time to {start_dt} or later (since={self.available_start}) to get a working backtest.")
        elif self.candles_received == 0:
            lines.append("This market may not have historical data available. Try a different symbol or timeframe.")
        else:
            lines.append("Try reducing the backtest time range or using a larger timeframe interval.")

        return "\n".join(lines)


class GMXOrderFailedException(ExchangeError):
    """Raised when a GMX order fails at the protocol level despite transaction success.

    This occurs when GMX emits OrderCancelled or OrderFrozen events, which can happen
    even when the keeper transaction succeeds (receipt.status == 1).

    Common failure reasons:

    - MaxOpenInterestExceeded: Pool has hit its open interest limit
    - InsufficientPoolAmount: Not enough liquidity in the pool
    - OrderNotFulfillableAtAcceptablePrice: Price moved beyond slippage tolerance
    - MinPositionSize: Order size is below minimum
    - InvalidDecreaseOrderSize: Decrease size doesn't match position

    :ivar order_key:
        The 32-byte GMX order key

    :ivar status:
        Order status: "cancelled" or "frozen"

    :ivar reason:
        Human-readable reason string from GMX event

    :ivar decoded_error:
        Decoded error message from reasonBytes (e.g., "MaxOpenInterestExceeded($1,234.56, $1,000.00)")

    :ivar error_selector:
        4-byte error selector hex string (e.g., "2bf127cf")

    :ivar reason_bytes:
        Raw reasonBytes from the GMX event

    :ivar tx_hash:
        Transaction hash of the failed order

    :ivar receipt:
        Full transaction receipt

    Example usage::

        try:
            order = gmx.create_order("ETH/USD", "market", "buy", 100)
        except GMXOrderFailedException as e:
            print(f"Order failed: {e.decoded_error}")
            print(f"Order key: {e.order_key.hex()}")
            # Handle failure - do NOT treat as successful order
    """

    def __init__(
        self,
        order_key: bytes,
        status: str,
        reason: str | None,
        decoded_error: str | None,
        error_selector: str | None,
        reason_bytes: bytes | None,
        tx_hash: str,
        receipt: dict,
    ):
        self.order_key = order_key
        self.status = status
        self.reason = reason
        self.decoded_error = decoded_error
        self.error_selector = error_selector
        self.reason_bytes = reason_bytes
        self.tx_hash = tx_hash
        self.receipt = receipt

        message = self._build_message()
        super().__init__(message)

    def _build_message(self) -> str:
        """Build human-readable error message."""
        lines = [
            f"GMX order {self.status}",
        ]

        if self.decoded_error:
            lines.append(f"Error: {self.decoded_error}")
        elif self.reason:
            lines.append(f"Reason: {self.reason}")

        if self.order_key:
            lines.append(f"Order key: 0x{self.order_key.hex()[:16]}...")

        lines.append(f"Transaction: {self.tx_hash}")

        return " | ".join(lines)
