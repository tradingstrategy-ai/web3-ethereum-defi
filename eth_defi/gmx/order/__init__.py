"""GMX Order Module

This module provides classes for creating GMX trading and liquidity orders.

Trading Orders (inherit from BaseOrder):
    - IncreaseOrder: Open or increase positions
    - DecreaseOrder: Close or decrease positions
    - SwapOrder: Token swaps
    - SLTPOrder: Stop Loss and Take Profit orders

Liquidity Orders:
    Base Classes:
        - Deposit: Base class for adding liquidity to markets
        - Withdraw: Base class for removing liquidity from markets

    Convenience Wrappers:
        - DepositOrder: Simplified deposit interface
        - WithdrawOrder: Simplified withdrawal interface

All order classes return unsigned transactions for external signing,
following the eth_defi library pattern.
"""

# Re-export the most commonly used public types so callers can do:
#   from eth_defi.gmx.order import OrderResult, PendingOrder, fetch_pending_orders
from eth_defi.gmx.order.base_order import OrderResult
from eth_defi.gmx.order.cancel_order import CancelOrder
from eth_defi.gmx.order.decrease_order import DecreaseOrder
from eth_defi.gmx.order.increase_order import IncreaseOrder
from eth_defi.gmx.order.pending_orders import PendingOrder, fetch_pending_orders
from eth_defi.gmx.order.sltp_order import SLTPOrder

__all__ = [
    "CancelOrder",
    "DecreaseOrder",
    "IncreaseOrder",
    "OrderResult",
    "PendingOrder",
    "SLTPOrder",
    "fetch_pending_orders",
]
