"""Test HyperCore → HyperEVM bridge fee margin helper.

Verifies that :py:func:`compute_spot_to_evm_withdrawal_amount` correctly
adjusts the withdrawal amount to leave enough USDC on HyperCore spot to
cover the bridge fee.

1. When spot balance has surplus beyond the desired amount, return the
   desired amount unchanged.
2. When withdrawing the full spot balance, reduce by the fee margin.
3. When spot balance is dust (at or below fee margin), return zero.
"""

from decimal import Decimal

from eth_defi.hyperliquid.constants import HYPERCORE_BRIDGE_FEE_MARGIN
from eth_defi.hyperliquid.core_writer import compute_spot_to_evm_withdrawal_amount


def test_spot_to_evm_withdrawal_with_surplus():
    """When spot balance exceeds desired + margin, return desired amount unchanged.

    1. Set spot balance to 10 USDC and desired amount to 7 USDC.
    2. Call compute_spot_to_evm_withdrawal_amount.
    3. Assert result equals 7 USDC (headroom of 3 USDC > margin).
    """
    result = compute_spot_to_evm_withdrawal_amount(
        spot_balance=Decimal("10"),
        desired_amount=Decimal("7"),
    )
    assert result == Decimal("7")


def test_spot_to_evm_withdrawal_full_balance():
    """When withdrawing full spot balance, reduce by fee margin.

    1. Set spot balance and desired amount both to 7.787157 USDC (the
       crash scenario from production).
    2. Call compute_spot_to_evm_withdrawal_amount.
    3. Assert result equals 7.787157 - 0.01 = 7.777157 USDC.
    """
    result = compute_spot_to_evm_withdrawal_amount(
        spot_balance=Decimal("7.787157"),
        desired_amount=Decimal("7.787157"),
    )
    assert result == Decimal("7.787157") - HYPERCORE_BRIDGE_FEE_MARGIN


def test_spot_to_evm_withdrawal_nearly_full():
    """When desired amount leaves less than fee margin, clamp to spot - margin.

    1. Set spot balance to 10 USDC and desired amount to 9.995 USDC.
    2. Headroom is 0.005 which is less than HYPERCORE_BRIDGE_FEE_MARGIN.
    3. Assert result equals 10 - 0.01 = 9.99 USDC.
    """
    result = compute_spot_to_evm_withdrawal_amount(
        spot_balance=Decimal("10"),
        desired_amount=Decimal("9.995"),
    )
    assert result == Decimal("10") - HYPERCORE_BRIDGE_FEE_MARGIN


def test_spot_to_evm_withdrawal_dust_balance():
    """When spot balance is at or below fee margin, return zero.

    1. Set spot balance to 0.005 USDC (below the 0.01 margin).
    2. Call compute_spot_to_evm_withdrawal_amount.
    3. Assert result is zero — caller must skip the withdrawal.
    """
    result = compute_spot_to_evm_withdrawal_amount(
        spot_balance=Decimal("0.005"),
        desired_amount=Decimal("0.005"),
    )
    assert result == Decimal(0)


def test_spot_to_evm_withdrawal_exact_margin():
    """When spot balance equals exactly the fee margin, return zero.

    1. Set spot balance to exactly HYPERCORE_BRIDGE_FEE_MARGIN.
    2. Call compute_spot_to_evm_withdrawal_amount.
    3. Assert result is zero — there is nothing left after the fee.
    """
    result = compute_spot_to_evm_withdrawal_amount(
        spot_balance=HYPERCORE_BRIDGE_FEE_MARGIN,
        desired_amount=HYPERCORE_BRIDGE_FEE_MARGIN,
    )
    assert result == Decimal(0)
