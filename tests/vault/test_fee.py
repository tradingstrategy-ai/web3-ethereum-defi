"""Tests for vault fee metadata."""

from decimal import Decimal

import pytest

from eth_defi.vault.fee import FeeData, VaultFeeMode


def test_fee_data_normalises_integer_fees_to_floats() -> None:
    """Fee metadata stores real-number inputs as floats."""
    fees = FeeData(
        fee_mode=VaultFeeMode.externalised,
        management=0,
        performance=0,
        deposit=0,
        withdraw=0,
    )

    assert fees.management == 0.0
    assert fees.performance == 0.0
    assert fees.deposit == 0.0
    assert fees.withdraw == 0.0
    assert all(isinstance(fee, float) for fee in (fees.management, fees.performance, fees.deposit, fees.withdraw))


@pytest.mark.parametrize("field_name", ("management", "performance", "deposit", "withdraw"))
def test_fee_data_rejects_decimal_fees(field_name: str) -> None:
    """Reject Decimal fee metadata before it can enter the vault pickle."""
    kwargs = dict.fromkeys(("management", "performance", "deposit", "withdraw"), 0.0)
    kwargs[field_name] = Decimal("0.01")

    with pytest.raises(AssertionError, match=rf"FeeData\.{field_name} must be a real number or None"):
        FeeData(fee_mode=VaultFeeMode.externalised, **kwargs)
