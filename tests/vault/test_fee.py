"""Tests for vault fee metadata."""

from decimal import Decimal

import pytest

from eth_defi.vault.fee import FeeData, VaultFeeMode


def test_fee_data_accepts_one_hundred_percent_fee() -> None:
    """A 100% performance fee is a valid fractional fee boundary."""
    fees = FeeData(
        fee_mode=VaultFeeMode.externalised,
        management=0.0,
        performance=1,
        deposit=0.0,
        withdraw=0.0,
    )

    assert fees.performance == 1.0
    assert isinstance(fees.performance, float)


def test_fee_data_rejects_fee_above_one_hundred_percent() -> None:
    """Malformed fee fractions cannot be persisted by a new scan."""
    with pytest.raises(ValueError, match=r"FeeData\.performance must be between 0 and 1 inclusive"):
        FeeData(
            fee_mode=VaultFeeMode.externalised,
            management=0.0,
            performance=1.01,
            deposit=0.0,
            withdraw=0.0,
        )


def test_fee_data_rejects_decimal_fees() -> None:
    """Reject a Decimal fee before a new scan can persist it."""
    with pytest.raises(AssertionError, match=r"FeeData\.performance must be a real number or None"):
        FeeData(fee_mode=VaultFeeMode.externalised, management=0, performance=Decimal("0.01"), deposit=0, withdraw=0)
