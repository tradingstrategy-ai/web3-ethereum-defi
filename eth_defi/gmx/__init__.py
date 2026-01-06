"""GMX API.

- This is a clean wrapper around the original `gmx-python-ng <https://github.com/tradingstrategy-ai/gmx-python-ng>__` library
"""

from eth_defi.gmx.events import (
    GMX_ERROR_SELECTORS,
    GMX_PRICE_PRECISION,
    GMX_USD_PRECISION,
    GMXEventData,
    OrderExecutionResult,
    OrderFees,
    decode_error_reason,
    decode_gmx_event,
    decode_gmx_events,
    extract_order_execution_result,
    extract_order_key_from_receipt,
    find_events_by_name,
    get_event_name_hash,
)

__all__ = [
    "GMX_ERROR_SELECTORS",
    "GMX_PRICE_PRECISION",
    "GMX_USD_PRECISION",
    "GMXEventData",
    "OrderExecutionResult",
    "OrderFees",
    "decode_error_reason",
    "decode_gmx_event",
    "decode_gmx_events",
    "extract_order_execution_result",
    "extract_order_key_from_receipt",
    "find_events_by_name",
    "get_event_name_hash",
]
