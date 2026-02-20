"""Tests for GMX precision guards against IEEE 754 float corruption.

These tests verify that the precision guard utilities correctly detect
and prevent float precision loss in GMX uint256 values. No RPC or
network access needed.
"""

import pytest

from eth_defi.gmx.precision import (
    RAW_USD_THRESHOLD,
    assert_not_float_corrupted,
    cap_size_delta_to_position,
    is_raw_usd_amount,
)

#: Exact values from the failed transaction (tx 0x31af3512...)
#: Their difference is exactly 2^51, a textbook IEEE 754 precision loss artifact.
POSITION_SIZE_IN_USD = 15753774067668833181431687544832
FLOAT_CORRUPTED_SIZE = 15753774067668835433231501230080

#: A value guaranteed to be float-corrupted on ALL IEEE 754 platforms.
#: Any odd integer > 2^53 cannot be exactly represented as float64,
#: so int(float(x)) != x is always true. This is a ~$10 position.
GUARANTEED_CORRUPT_VALUE = 10 * 10**30 + 1


def test_is_raw_usd_amount_detects_raw_format():
    """Raw USD detection correctly identifies 30-decimal ints."""
    assert is_raw_usd_amount(POSITION_SIZE_IN_USD) is True


def test_is_raw_usd_amount_rejects_float():
    """Human-readable float values are not raw."""
    assert is_raw_usd_amount(15753.77) is False


def test_is_raw_usd_amount_rejects_small_int():
    """Small ints (below threshold) are not raw."""
    assert is_raw_usd_amount(100) is False
    assert is_raw_usd_amount(0) is False


def test_is_raw_usd_amount_boundary():
    """Values at/below threshold are not raw."""
    assert is_raw_usd_amount(RAW_USD_THRESHOLD) is False
    assert is_raw_usd_amount(RAW_USD_THRESHOLD + 1) is True


def test_assert_not_float_corrupted_clean_value():
    """Clean ints that survive float round-trip pass the assertion."""
    # Small value that survives float conversion
    assert_not_float_corrupted(12345, "small_value")


def test_assert_not_float_corrupted_detects_corruption():
    """Values that cannot survive float() round-trip are detected.

    Any odd integer > 2^53 is not exactly representable as float64,
    so int(float(x)) != x is always true.
    """
    # Verify the corruption actually occurs for our test value
    assert int(float(GUARANTEED_CORRUPT_VALUE)) != GUARANTEED_CORRUPT_VALUE

    with pytest.raises(AssertionError, match="float-corrupted"):
        assert_not_float_corrupted(GUARANTEED_CORRUPT_VALUE, "test_corrupted")


def test_assert_not_float_corrupted_rejects_float_type():
    """Float type is rejected even if value is small."""
    with pytest.raises(AssertionError, match="must be int"):
        assert_not_float_corrupted(15753.77, "test_float")


def test_cap_size_delta_equal():
    """When sizeDelta == positionSize, no capping occurs."""
    result = cap_size_delta_to_position(POSITION_SIZE_IN_USD, POSITION_SIZE_IN_USD)
    assert result == POSITION_SIZE_IN_USD


def test_cap_size_delta_smaller():
    """When sizeDelta < positionSize (partial close), no capping occurs."""
    partial = POSITION_SIZE_IN_USD // 2
    result = cap_size_delta_to_position(partial, POSITION_SIZE_IN_USD)
    assert result == partial


def test_cap_size_delta_overshooting():
    """When sizeDelta > positionSize (float corruption), caps to position."""
    result = cap_size_delta_to_position(FLOAT_CORRUPTED_SIZE, POSITION_SIZE_IN_USD)
    assert result == POSITION_SIZE_IN_USD


def test_cap_size_delta_overshoot_by_one():
    """Even 1 wei overshoot is capped."""
    result = cap_size_delta_to_position(POSITION_SIZE_IN_USD + 1, POSITION_SIZE_IN_USD)
    assert result == POSITION_SIZE_IN_USD


def test_ieee754_precision_loss_demonstration():
    """Demonstrate the precision loss that causes InvalidDecreaseOrderSize.

    This test documents the root cause: int(float(x)) != x for large ints
    because IEEE 754 double-precision only has 53 bits of mantissa.

    The transaction values (tx 0x31af3512...) document the actual failure:
    the corrupted sizeDeltaUsd exceeded sizeInUsd by exactly 2^51.
    """
    # The transaction values differ by exactly 2^51
    delta = FLOAT_CORRUPTED_SIZE - POSITION_SIZE_IN_USD
    assert delta == 2**51

    # This is what GMX sees: order.sizeDeltaUsd > position.sizeInUsd → revert
    assert FLOAT_CORRUPTED_SIZE > POSITION_SIZE_IN_USD

    # Demonstrate float corruption with a guaranteed-corrupt value
    corrupted = int(float(GUARANTEED_CORRUPT_VALUE))
    assert corrupted != GUARANTEED_CORRUPT_VALUE, "Expected float corruption"

    # The corrupted value differs from the original — this is what causes
    # MarketDecrease to revert when used as sizeDeltaUsd
    assert abs(corrupted - GUARANTEED_CORRUPT_VALUE) > 0


def test_any_10_usd_position_is_corrupted():
    """Any position >= $10 in raw 30-decimal format will be float-corrupted.

    A $10 position has sizeInUsd = 10 * 10^30 = 10^31, which is ~103 bits.
    IEEE 754 doubles only have 53 bits of mantissa, so the lower ~50 bits
    are lost during float conversion.
    """
    ten_usd_raw = 10 * 10**30
    corrupted = int(float(ten_usd_raw))
    assert corrupted != ten_usd_raw, "Expected float corruption for $10 position but got exact match"
