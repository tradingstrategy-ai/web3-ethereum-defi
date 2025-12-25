"""Validation utilities for GMX CCXT exchange data."""

from datetime import datetime, timezone
from typing import Any
from eth_defi.gmx.ccxt.errors import InsufficientHistoricalDataError


def _validate_ohlcv_data_sufficiency(
    ohlcv: list[list],
    symbol: str,
    timeframe: str,
    since: int | None,
    params: dict[str, Any] | None,
) -> None:
    """Validate OHLCV data is sufficient for requested time range.

    Raises InsufficientHistoricalDataError if:
    - 'since' specified but no data returned
    - Data starts on a later date than requested 'since'

    Validation is date-based (ignoring time), meaning any time on the
    same date is acceptable.

    No validation if:
    - 'since' is None (user just wants recent data)
    - params contains skip_validation=True

    Args:
        ohlcv: Parsed OHLCV data (list of [timestamp, o, h, l, c, v])
        symbol: Market symbol
        timeframe: Timeframe interval
        since: Requested start timestamp (ms), if provided
        params: Additional parameters (may contain skip_validation flag)

    Raises:
        InsufficientHistoricalDataError: If data is insufficient
    """
    # Escape hatch
    if params and params.get("skip_validation"):
        return

    # No validation if user didn't specify 'since'
    if since is None:
        return

    # Empty data when user requested specific start time
    if len(ohlcv) == 0:
        raise InsufficientHistoricalDataError(
            symbol=symbol,
            timeframe=timeframe,
            requested_start=since,
            available_start=None,
            available_end=None,
            candles_received=0,
        )

    # Extract time range from received data
    available_start = ohlcv[0][0]  # timestamp of first candle
    available_end = ohlcv[-1][0]  # timestamp of last candle

    # Compare dates (ignore time) for validation
    # This allows any time on the same date to be acceptable
    requested_date = datetime.fromtimestamp(since / 1000, tz=timezone.utc).date()
    available_start_date = datetime.fromtimestamp(available_start / 1000, tz=timezone.utc).date()

    # Check if data starts on a later date
    if available_start_date > requested_date:
        raise InsufficientHistoricalDataError(
            symbol=symbol,
            timeframe=timeframe,
            requested_start=since,
            available_start=available_start,
            available_end=available_end,
            candles_received=len(ohlcv),
        )


def _timeframe_to_milliseconds(timeframe: str) -> int:
    """Convert CCXT timeframe string to milliseconds.

    Args:
        timeframe: Timeframe string (e.g., "1m", "5m", "1h", "4h", "1d")

    Returns:
        Duration in milliseconds

    Examples:
        >>> _timeframe_to_milliseconds("1m")
        60000
        >>> _timeframe_to_milliseconds("1h")
        3600000
        >>> _timeframe_to_milliseconds("1d")
        86400000
    """
    timeframe_map = {
        "1m": 60 * 1000,
        "5m": 5 * 60 * 1000,
        "15m": 15 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "4h": 4 * 60 * 60 * 1000,
        "1d": 24 * 60 * 60 * 1000,
    }
    return timeframe_map.get(timeframe, 60 * 1000)  # Default to 1m
