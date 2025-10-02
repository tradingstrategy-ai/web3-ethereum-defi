"""GMX Order Module

This module provides classes for creating GMX trading and liquidity orders.

Trading Orders (inherit from BaseOrder):
    - IncreaseOrder: Open or increase positions
    - DecreaseOrder: Close or decrease positions
    - SwapOrder: Token swaps

Liquidity Orders (standalone):
    - Deposit: Add liquidity to markets
    - Withdraw: Remove liquidity from markets

All order classes return unsigned transactions for external signing,
following the eth_defi library pattern.
"""

from eth_defi.gmx.order.base_order import BaseOrder, OrderParams, OrderType, OrderResult
from eth_defi.gmx.order.increase_order import IncreaseOrder
from eth_defi.gmx.order.decrease_order import DecreaseOrder
from eth_defi.gmx.order.swap_order import SwapOrder
from eth_defi.gmx.order.deposit import Deposit, DepositResult
from eth_defi.gmx.order.withdraw import Withdraw, WithdrawResult

__all__ = [
    # Base classes
    "BaseOrder",
    "OrderParams",
    "OrderType",
    "OrderResult",
    # Trading orders
    "IncreaseOrder",
    "DecreaseOrder",
    "SwapOrder",
    # Liquidity orders
    "Deposit",
    "DepositResult",
    "Withdraw",
    "WithdrawResult",
]
