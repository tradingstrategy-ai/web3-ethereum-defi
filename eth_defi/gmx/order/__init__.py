"""GMX order module.

Provides all public order classes and result types for the GMX protocol.

Trading orders (inherit from :class:`BaseOrder`):

- :class:`IncreaseOrder` — open or increase a position
- :class:`DecreaseOrder` — close or decrease a position
- :class:`SwapOrder` — token swap
- :class:`SLTPOrder` — bundled stop-loss / take-profit order

Cancel helpers:

- :class:`CancelOrder` — cancel a single or batch of pending orders
- :class:`CancelOrderResult` — result of a single cancellation
- :class:`BatchCancelOrderResult` — result of a batch cancellation

SL/TP building blocks:

- :class:`SLTPEntry` — defines one SL or TP leg
- :class:`SLTPParams` — parameters for a full SL/TP bundle
- :class:`SLTPOrderResult` — result returned after placing a SL/TP bundle

Pending order helpers:

- :class:`PendingOrder` — on-chain pending order data class
- :func:`fetch_pending_orders` — fetch all open orders for an account
- :func:`fetch_pending_order_count` — count open orders for an account

Base / parameter types:

- :class:`BaseOrder` — abstract base for all trading orders
- :class:`OrderParams` — common order parameter container
- :class:`OrderResult` — result returned after placing an order
- :class:`OrderArgumentParser` — helper for resolving human-readable
  token/market arguments into on-chain parameters
"""

from eth_defi.gmx.order.base_order import BaseOrder, OrderParams, OrderResult
from eth_defi.gmx.order.cancel_order import BatchCancelOrderResult, CancelOrder, CancelOrderResult
from eth_defi.gmx.order.decrease_order import DecreaseOrder
from eth_defi.gmx.order.increase_order import IncreaseOrder
from eth_defi.gmx.order.order_argument_parser import OrderArgumentParser
from eth_defi.gmx.order.pending_orders import PendingOrder, fetch_pending_order_count, fetch_pending_orders
from eth_defi.gmx.order.sltp_order import SLTPEntry, SLTPOrder, SLTPOrderResult, SLTPParams
from eth_defi.gmx.order.swap_order import SwapOrder

__all__ = [
    "BaseOrder",
    "BatchCancelOrderResult",
    "CancelOrder",
    "CancelOrderResult",
    "DecreaseOrder",
    "IncreaseOrder",
    "OrderArgumentParser",
    "OrderParams",
    "OrderResult",
    "PendingOrder",
    "SLTPEntry",
    "SLTPOrder",
    "SLTPOrderResult",
    "SLTPParams",
    "SwapOrder",
    "fetch_pending_order_count",
    "fetch_pending_orders",
]
