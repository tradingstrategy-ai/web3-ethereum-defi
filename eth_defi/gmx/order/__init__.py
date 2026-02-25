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
