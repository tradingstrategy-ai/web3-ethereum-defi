"""GMX Liquidity Module

This module provides base classes for GMX liquidity operations (deposits and withdrawals).

These are separate from trading orders because liquidity operations involve
adding/removing funds from pools rather than opening/closing trading positions.

Classes:
    - Deposit: Base class for adding liquidity to markets
    - Withdraw: Base class for removing liquidity from markets
    - DepositParams: Parameters for deposit operations
    - WithdrawParams: Parameters for withdrawal operations
    - DepositResult: Result of deposit operations
    - WithdrawResult: Result of withdrawal operations

For convenience wrappers, see:
    - eth_defi.gmx.order.Deposit
    - eth_defi.gmx.order.Withdraw
"""

from eth_defi.gmx.liquidity_base.deposit import Deposit, DepositParams, DepositResult
from eth_defi.gmx.liquidity_base.withdraw import Withdraw, WithdrawParams, WithdrawResult

__all__ = [
    "Deposit",
    "DepositParams",
    "DepositResult",
    "Withdraw",
    "WithdrawParams",
    "WithdrawResult",
]
