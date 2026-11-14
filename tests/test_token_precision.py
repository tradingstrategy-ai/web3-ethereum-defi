"""Token-unit preservation shared by all vault denomination and share tokens."""

from decimal import Decimal, localcontext
from unittest.mock import MagicMock

import pytest

from eth_defi.token import TokenDetails


@pytest.mark.parametrize("decimals", [0, 6, 8, 18])
@pytest.mark.parametrize("precision", [6, 28, 78])
def test_token_conversion_preserves_raw_units(decimals: int, precision: int) -> None:
    """Keep exact quantities even when the caller uses a small Decimal context.

    1. Model token decimals and small, large, signed and uint256-limit balances.
    2. Convert to human units and back under the caller's Decimal precision.
    3. Check exact raw units, approximate human values and unchanged context.
    """
    # 1. Model token decimals and small, large, signed and uint256-limit balances.
    token = TokenDetails(contract=MagicMock(), decimals=decimals)
    quantum = Decimal(f"1e-{decimals}")
    for raw_amount in [0, 1, 1_001_000_000, 10**29 + 1, -(10**29 + 1), 2**256 - 1]:
        expected = Decimal(f"{raw_amount}e-{decimals}")

        # 2. Convert to human units and back under the caller's Decimal precision.
        with localcontext() as context:
            context.prec = precision
            actual = token.convert_to_decimals(raw_amount)
            converted_raw = token.convert_to_raw(expected)

            # 3. Check exact raw units, approximate human values and unchanged context.
            assert actual == expected
            assert actual == pytest.approx(expected, rel=0, abs=quantum)
            assert converted_raw == raw_amount
            assert token.convert_to_raw(actual) == raw_amount
            assert context.prec == precision


@pytest.mark.parametrize("decimals", [0, 6, 8, 18])
@pytest.mark.parametrize("precision", [6, 28, 78])
def test_token_conversion_truncates_fractional_raw_units(decimals: int, precision: int) -> None:
    """Truncate positive and negative sub-units without a context rounding step.

    1. Compute the expected truncation with enough precision for every digit.
    2. Convert under a smaller caller context and retain legacy numeric inputs.
    3. Compare human amounts within one unit and preserve exact raw results.
    """
    token = TokenDetails(contract=MagicMock(), decimals=decimals)
    quantum = Decimal(f"1e-{decimals}")
    # 1. Compute the expected truncation with enough precision for every digit.
    for text in ["10000000000.1234567890123456789", "-10000000000.1234567890123456789"]:
        amount = Decimal(text)
        with localcontext() as oracle:
            oracle.prec = 100
            expected_raw = int(amount * 10**decimals)
        # 2. Convert under a smaller caller context and retain legacy numeric inputs.
        with localcontext() as context:
            context.prec = precision
            raw = token.convert_to_raw(amount)
            actual = token.convert_to_decimals(raw)
            # 3. Compare human amounts within one unit and preserve exact raw results.
            assert raw == expected_raw
            assert actual == pytest.approx(amount, rel=0, abs=quantum)
            assert context.prec == precision
    assert token.convert_to_raw(1) == 10**decimals
    assert token.convert_to_raw(1.5) == 3 * 10**decimals // 2
