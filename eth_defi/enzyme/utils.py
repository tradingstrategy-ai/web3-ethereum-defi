"""Enzyme protocol utilities.

Random uncommented Enzyme TS code ported to Python.

Original code written in Decimal.js

https://mikemcl.github.io/decimal.js/#toSD
"""
from decimal import Decimal

from sigfig import round

ONE_HOUR_IN_SECONDS = 60 * 60
ONE_DAY_IN_SECONDS = ONE_HOUR_IN_SECONDS * 24
ONE_WEEK_IN_SECONDS = ONE_DAY_IN_SECONDS * 7
ONE_YEAR_IN_SECONDS = ONE_DAY_IN_SECONDS * 365.25


SCALED_PER_SECOND_RATE_DIGITS = 27

# export function convertRateToScaledPerSecondRate({
#   rate,
#   adjustInflation,
# }: {
#   rate: BigNumberish;
#   adjustInflation: boolean;
# }) {
#   const rateD = new Decimal(utils.formatEther(rate));
#   const effectiveRate = adjustInflation ? rateD.div(new Decimal(1).minus(rateD)) : rateD;
#
#   const factor = new Decimal(1)
#     .plus(effectiveRate)
#     .pow(1 / ONE_YEAR_IN_SECONDS)
#     .toSignificantDigits(scaledPerSecondRateDigits)
#     .mul(scaledPerSecondRateScaleDecimal);
#
#   return BigNumber.from(factor.toFixed(0));
# }


def convert_rate_to_scaled_per_second_rate(rate: Decimal, adjust_inflation: bool) -> int:
    """Internal helper to deal with Enzyme per second rates."""
    rate_d = rate * 10**18
    effective_rate = rate_d / (1 - rate_d) if adjust_inflation else rate_d
    factor = (1 + effective_rate) ** (1 / ONE_YEAR_IN_SECONDS)
    factor_as_significant_digits = round(factor, sigfigs=SCALED_PER_SECOND_RATE_DIGITS)
    return int(factor * 10**27)
    # factor_fixed_num = factor * 10**SCALED_PER_SECOND_RATE_DIGITS
