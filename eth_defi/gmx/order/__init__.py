"""GMX Order Module

This module provides classes for creating GMX trading and liquidity orders.

Trading Orders (inherit from BaseOrder):
    - IncreaseOrder: Open or increase positions
    - DecreaseOrder: Close or decrease positions
    - SwapOrder: Token swaps

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

from eth_defi.gmx.order.base_order import BaseOrder, OrderParams, OrderType, OrderResult
from eth_defi.gmx.order.increase_order import IncreaseOrder
from eth_defi.gmx.order.decrease_order import DecreaseOrder
from eth_defi.gmx.order.swap_order import SwapOrder
from eth_defi.gmx.liquidity_base.deposit import Deposit, DepositParams, DepositResult
from eth_defi.gmx.liquidity_base.withdraw import Withdraw, WithdrawParams, WithdrawResult
from eth_defi.gmx.order.deposit_order import DepositOrder
from eth_defi.gmx.order.withdraw_order import WithdrawOrder

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
    # Liquidity base classes
    "Deposit",
    "DepositParams",
    "DepositResult",
    "Withdraw",
    "WithdrawParams",
    "WithdrawResult",
    # Liquidity order wrappers
    "DepositOrder",
    "WithdrawOrder",
]
