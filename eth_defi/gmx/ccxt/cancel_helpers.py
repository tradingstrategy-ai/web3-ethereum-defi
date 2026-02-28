"""Shared helpers for GMX CCXT cancel_order / cancel_orders.

Both the sync (``eth_defi.gmx.ccxt.exchange``) and async
(``eth_defi.gmx.ccxt.async_support.exchange``) exchange classes contain
nearly identical logic for:

* resolving a raw id (possibly a tx_hash) to a DataStore order_key
* building the CCXT-compatible cancelled-order response dict

These helpers live here so the two exchange classes share the logic
without inheriting from each other (they have different CCXT base classes).

.. note::
    ``TODO(refactor):`` The full validation loop (is_order_pending RPC call per
    key) is still duplicated.  A multicall-based batch validator would also
    remove the O(N) RPC overhead documented in cancel_orders().
"""

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def resolve_order_id(
    orders_cache: dict[str, dict],
    raw_id: str,
    log_context: str = "cancel_order",
) -> tuple[str, bytes]:
    """Resolve a raw order id to a (resolved_id, order_key_bytes) pair.

    Accepts either a raw DataStore order key (64 hex chars) or a tx_hash
    previously stored in *orders_cache* by ``create_stoploss()`` /
    ``_create_standalone_sltp_order()``.

    :param orders_cache:
        The exchange's ``_orders`` dict mapping tx_hash -> cached order dict.
    :param raw_id:
        Order id as supplied by the caller — ``"0x..."`` or bare hex.
    :param log_context:
        Short label for log messages, e.g. ``"cancel_order"``.
    :returns:
        ``(resolved_id, order_key_bytes)`` where *resolved_id* is the
        DataStore order key hex string and *order_key_bytes* is the
        corresponding 32-byte value.
    :raises ValueError:
        If the resolved id is not a valid 32-byte hex string.
    """
    _lookup_id = raw_id if raw_id.startswith("0x") else "0x" + raw_id
    _cached = orders_cache.get(raw_id) or orders_cache.get(_lookup_id)
    if _cached:
        _cached_key = _cached.get("info", {}).get("order_key")
        if _cached_key:
            logger.debug(
                "%s: resolved tx_hash %s -> order_key %s via cache",
                log_context,
                raw_id[:18],
                _cached_key[:18],
            )
            raw_id = _cached_key

    hex_str = raw_id.removeprefix("0x")
    if len(hex_str) != 64:
        raise ValueError(
            f"Invalid order key '{raw_id}': expected 32-byte (64 hex chars) key, got {len(hex_str)} chars.",
        )
    return raw_id, bytes.fromhex(hex_str)


def build_cancel_order_response(
    order_id: str,
    symbol: str | None,
    tx_hash: str,
    block_number: int | None,
    timestamp_ms: int,
    iso8601_fn: Callable[[int], str],
) -> dict:
    """Build a CCXT-compatible cancelled-order response dict.

    :param order_id: DataStore order key hex string (``"0x..."``).
    :param symbol: Market symbol, or ``None`` (CCXT compatibility).
    :param tx_hash: On-chain cancel transaction hash.
    :param block_number: Block number of the cancel transaction.
    :param timestamp_ms: Current timestamp in milliseconds.
    :param iso8601_fn: CCXT ``iso8601()`` callable for datetime formatting.
    :returns: CCXT order dict with ``status="cancelled"``.
    """
    return {
        "id": order_id,
        "clientOrderId": None,
        "timestamp": timestamp_ms,
        "datetime": iso8601_fn(timestamp_ms),
        "lastTradeTimestamp": None,
        "status": "cancelled",
        "symbol": symbol,
        "type": None,
        "timeInForce": None,
        "side": None,
        "price": None,
        "amount": None,
        "filled": 0.0,
        "remaining": None,
        "cost": None,
        "trades": [],
        "fee": None,
        "info": {
            "status": "ok",
            "response": {
                "type": "cancel",
                "data": {
                    "statuses": ["success"],
                },
            },
            "order_key": order_id,
            "tx_hash": tx_hash,
            "block_number": block_number,
        },
        "average": None,
        "fees": [],
    }
