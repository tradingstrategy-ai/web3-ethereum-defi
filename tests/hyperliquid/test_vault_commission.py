"""Tests for Hyperliquid vault withdrawal commission estimation.

Tests that :py:func:`~eth_defi.hyperliquid.vault.estimate_max_withdrawal_commission`
correctly computes worst-case vault leader commission for withdrawals.
"""

from decimal import Decimal

from eth_defi.hyperliquid.vault import estimate_max_withdrawal_commission


def test_estimate_max_withdrawal_commission_with_rate():
    """Estimate worst-case commission with a 10% leader rate.

    1. Compute commission for a 500 USDC withdrawal at 10% rate.
    2. Verify the result is 50 USDC (worst case: 100% profit).
    """
    # 1. Compute commission for a 500 USDC withdrawal at 10% rate.
    result = estimate_max_withdrawal_commission(
        withdrawal_amount=Decimal("500"),
        commission_rate=Decimal("0.10"),
    )

    # 2. Verify the result is 50 USDC (worst case: 100% profit).
    assert result == Decimal("50")


def test_estimate_max_withdrawal_commission_none_rate():
    """Protocol vaults (HLP) have no commission — rate is None.

    1. Compute commission with None rate.
    2. Verify the result is zero.
    """
    # 1. Compute commission with None rate.
    result = estimate_max_withdrawal_commission(
        withdrawal_amount=Decimal("500"),
        commission_rate=None,
    )

    # 2. Verify the result is zero.
    assert result == Decimal(0)


def test_estimate_max_withdrawal_commission_zero_rate():
    """Vault with explicit zero commission rate.

    1. Compute commission with zero rate.
    2. Verify the result is zero.
    """
    # 1. Compute commission with zero rate.
    result = estimate_max_withdrawal_commission(
        withdrawal_amount=Decimal("500"),
        commission_rate=Decimal("0"),
    )

    # 2. Verify the result is zero.
    assert result == Decimal(0)


def test_estimate_max_withdrawal_commission_float_rate():
    """VaultInfo.commission_rate is a float — must not raise TypeError.

    1. Pass a float commission rate (as returned by VaultInfo).
    2. Verify the result is correct and is a Decimal.
    """
    # 1. Float rate from VaultInfo.commission_rate.
    result = estimate_max_withdrawal_commission(
        withdrawal_amount=Decimal("500"),
        commission_rate=0.1,
    )

    # 2. Correct result, returned as Decimal.
    assert result == Decimal("50")
    assert isinstance(result, Decimal)


def test_estimate_max_withdrawal_commission_negative_withdrawal():
    """Withdrawal events store outflows as negative — must return positive commission.

    1. Pass a negative withdrawal amount (as in VaultDepositEvent.usdc).
    2. Verify the commission is positive (abs applied internally).
    """
    # 1. Negative withdrawal amount.
    result = estimate_max_withdrawal_commission(
        withdrawal_amount=Decimal("-500"),
        commission_rate=Decimal("0.10"),
    )

    # 2. Commission is positive.
    assert result == Decimal("50")
    assert result > 0
