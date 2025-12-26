"""GMX Order Module

This module provides classes for creating GMX trading and liquidity orders.

Trading Orders (inherit from BaseOrder):
    - IncreaseOrder: Open or increase positions
    - DecreaseOrder: Close or decrease positions
    - SwapOrder: Token swaps

Stop-Loss/Take-Profit Orders:
    - SLTPOrder: Create stop-loss and take-profit orders
    - SLTPEntry: Configuration for individual SL/TP trigger
    - SLTPParams: Combined SL/TP parameters for bundled orders
    - SLTPOrderResult: Result of SL/TP order creation

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

from eth_defi.gmx.order.base_order import BaseOrder, OrderParams, OrderResult
from eth_defi.gmx.order.increase_order import IncreaseOrder
from eth_defi.gmx.order.decrease_order import DecreaseOrder
from eth_defi.gmx.order.swap_order import SwapOrder
from eth_defi.gmx.order.sltp_order import SLTPEntry, SLTPParams, SLTPOrder, SLTPOrderResult

__all__ = [
    # Base classes
    "BaseOrder",
    "OrderParams",
    "OrderResult",
    # Trading orders
    "IncreaseOrder",
    "DecreaseOrder",
    "SwapOrder",
    # Stop-Loss/Take-Profit orders
    "SLTPEntry",
    "SLTPParams",
    "SLTPOrder",
    "SLTPOrderResult",
]
