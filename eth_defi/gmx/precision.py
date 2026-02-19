"""Precision guards for GMX uint256 values.

GMX uses 30-decimal fixed-point for USD amounts. Python's IEEE 754 double-precision
float has only 53 bits of mantissa (~15.9 decimal digits), while GMX USD values can
be 31+ digits. Converting such values to float and back causes rounding errors of
up to 2^51 (~2.25e15), which causes GMX's MarketDecrease to revert with
InvalidDecreaseOrderSize when sizeDeltaUsd > sizeInUsd.

This module provides guards to ensure raw uint256 values are never silently
converted to float in critical code paths.
"""

import logging

logger = logging.getLogger(__name__)

#: Threshold above which a value is considered to be in raw 30-decimal format.
#: A USD amount of $0.01 in raw format is 10^28, so 10^20 is extremely conservative.
RAW_USD_THRESHOLD = 10**20


def is_raw_usd_amount(value) -> bool:
    """Check if a value is in raw 30-decimal USD format.

    :param value:
        The value to check

    :returns:
        True if the value is an int exceeding the raw threshold
    :rtype: bool
    """
    return isinstance(value, int) and value > RAW_USD_THRESHOLD


def assert_not_float_corrupted(value: int, label: str = "value") -> None:
    """Assert that a raw uint256 value has not been through float conversion.

    Checks whether ``int(float(value)) == value``. If not, the value has been
    corrupted by IEEE 754 double-precision rounding.

    :param value:
        The raw uint256 integer to validate
    :param label:
        A label for error messages identifying which variable is checked
    :raises AssertionError:
        If the value shows signs of float corruption
    """
    if not isinstance(value, int):
        raise AssertionError("%s must be int, got %s: %s" % (label, type(value).__name__, value))
    # Check round-trip through float
    float_roundtrip = int(float(value))
    if float_roundtrip != value:
        raise AssertionError("%s appears float-corrupted: int(float(%s)) = %s, delta = %s" % (label, value, float_roundtrip, float_roundtrip - value))


def cap_size_delta_to_position(
    size_delta_usd: int,
    position_size_usd: int,
    label: str = "",
) -> int:
    """Cap sizeDeltaUsd to never exceed the on-chain position sizeInUsd.

    GMX's MarketDecrease order handler reverts with InvalidDecreaseOrderSize
    when sizeDeltaUsd > sizeInUsd. LimitDecrease and StopLossDecrease auto-cap,
    but MarketDecrease does not.

    This mirrors the GMX official SDK's approach in decrease.ts where for full
    closes: ``values.sizeDeltaUsd = position.sizeInUsd``

    :param size_delta_usd:
        The requested decrease size (raw 30-decimal int)
    :param position_size_usd:
        The on-chain position size (raw 30-decimal int)
    :param label:
        Optional label for logging
    :returns:
        min(size_delta_usd, position_size_usd)
    :rtype: int
    """
    if size_delta_usd > position_size_usd:
        overshoot = size_delta_usd - position_size_usd
        logger.warning(
            "PRECISION_GUARD: %s sizeDeltaUsd (%s) exceeds position sizeInUsd (%s) by %s — capping to position size to prevent InvalidDecreaseOrderSize",
            label,
            size_delta_usd,
            position_size_usd,
            overshoot,
        )
        return position_size_usd
    return size_delta_usd
