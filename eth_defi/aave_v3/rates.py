"""Aave v3 rate calculation."""
import logging
from datetime import datetime
from decimal import Decimal
from typing import NamedTuple, Tuple

from pandas import DataFrame, Timedelta

logger = logging.getLogger(__name__)

# Constants for APY and APR calculation
RAY = Decimal(10**27)  # 10 to the power 27
WAD = Decimal(10**18)  # 10 to the power 18
SECONDS_PER_YEAR_INT = 31_536_000
SECONDS_PER_YEAR = Decimal(SECONDS_PER_YEAR_INT)


# Response from aave_v3_calculate_accrued_interests functions with all different interests calculated
class AaveAccruedInterests(NamedTuple):
    # The first interest event found in the given date range
    actual_start_time: datetime

    # The last interest event found in the given date range
    actual_end_time: datetime

    # Calculated interest for deposit of specified amount
    deposit_interest: Decimal

    # Calculated interest for a variable-rate borrow loan of specified amount
    variable_borrow_interest: Decimal

    # Calculated interest for stable-rate borrow loan of specified amount
    stable_borrow_interest: Decimal


# Response from aave_v3_calculate_accrued_xxx_interest functions with a single interest calculated
class AaveAccruedInterest(NamedTuple):
    # The first interest event found in the given date range
    actual_start_time: datetime

    # The last interest event found in the given date range
    actual_end_time: datetime

    # Calculated interest for specified amount
    interest: Decimal


def aave_v3_calculate_apr_apy_rates(df: DataFrame) -> DataFrame:
    """
    Calculate APR and APY columns for Aave v3 DataFrame previously generated from the blockchain events.
    Also add converted float versions of rate columns for easier calculation operations.
    https://docs.aave.com/developers/v/2.0/guides/apy-and-apr
    """
    # First we convert the rates to floats and Decimals to preseve accuracy. Original numbers are huge 256-bit integer values multiplied with RAY.
    df = df.assign(
        liquidity_rate_float=df["liquidity_rate"].apply(lambda value: float(Decimal(value) / RAY)),
        variable_borrow_rate_float=df["variable_borrow_rate"].apply(lambda value: float(Decimal(value) / RAY)),
        stable_borrow_rate_float=df["stable_borrow_rate"].apply(lambda value: float(Decimal(value) / RAY)),
        liquidity_rate_dec=df["liquidity_rate"].apply(lambda value: Decimal(value) / RAY),
        variable_borrow_rate_dec=df["variable_borrow_rate"].apply(lambda value: Decimal(value) / RAY),
        stable_borrow_rate_dec=df["stable_borrow_rate"].apply(lambda value: Decimal(value) / RAY),
    )

    # And finally we can calculate APR/APY percent values according to Aave v3 formulas.
    df = df.assign(
        deposit_apr=df["liquidity_rate_float"] * 100,
        variable_borrow_apr=df["variable_borrow_rate_float"] * 100,
        stable_borrow_apr=df["stable_borrow_rate_float"] * 100,
        deposit_apy=df["liquidity_rate_dec"].apply(lambda value: float((((Decimal(1) + (value / SECONDS_PER_YEAR)) ** SECONDS_PER_YEAR) - Decimal(1)) * 100)),
        variable_borrow_apy=df["variable_borrow_rate_dec"].apply(lambda value: float((((Decimal(1) + (value / SECONDS_PER_YEAR)) ** SECONDS_PER_YEAR) - Decimal(1)) * 100)),
        stable_borrow_apy=df["stable_borrow_rate_dec"].apply(lambda value: float((((Decimal(1) + (value / SECONDS_PER_YEAR)) ** SECONDS_PER_YEAR) - Decimal(1)) * 100)),
    )

    return df


def aave_v3_filter_by_token(df: DataFrame, token: str = "") -> DataFrame:
    """
    Filter the DataFrame by token. If token is empty, return the entire DataFrame.
    """
    if not token:
        # Return everything
        return df
    else:
        # Filter by token
        return df.loc[df["token"] == token]


def aave_v3_calculate_ohlc(df: DataFrame, time_bucket: Timedelta, attribute: str | Tuple, token: str = "") -> DataFrame | Tuple:
    """
    Calculate OHLC (Open, High, Low, Close) values for a given time bucket (e.g. 1 day) and given attribute.
    Attribute can be e.g. deposit_apr, variable_borrow_apr, stable_borrow_apr, deposit_apy, variable_borrow_apy, stable_borrow_apy.
    The dataframe must be indexed by timestamp.
    Returns a new DataFrame, or a tuple of DataFrames if a tuple of attributes was specified.
    """
    df = aave_v3_filter_by_token(df, token)
    if isinstance(attribute, str):
        # Single attribute
        return df[attribute].resample(time_bucket).ohlc(_method="ohlc")
    else:
        # Multiple attributes
        return (df[attr].resample(time_bucket).ohlc(_method="ohlc") for attr in attribute)


def aave_v3_calculate_mean(df: DataFrame, time_bucket: Timedelta, attribute: str | Tuple, token: str = "") -> DataFrame | Tuple:
    """
    Calculate mean values for a given time bucket (e.g. 1 day) and given attribute.
    Attribute can be e.g. deposit_apr, variable_borrow_apr, stable_borrow_apr, deposit_apy, variable_borrow_apy, stable_borrow_apy.
    The dataframe must be indexed by timestamp.
    Returns a new DataFrame, or a tuple of DataFrames if a tuple of attributes was specified.
    """
    df = aave_v3_filter_by_token(df, token)
    if isinstance(attribute, str):
        # Single attribute
        return df[attribute].resample(time_bucket).mean()
    else:
        # Multiple attributes
        return (df[attr].resample(time_bucket).mean() for attr in attribute)


def aave_v3_filter_by_date_range(df: DataFrame, start_time: datetime, end_time: datetime = None, token: str = "") -> DataFrame:
    """
    Filter the DataFrame by date range suitable for interest calculation (loan start to loan end time)
    The DataFrame must be indexed by timestamp.
    If token is specified, also filters by token.
    """
    if end_time:
        return aave_v3_filter_by_token(df, token).query("timestamp >= @start_time and timestamp <= @end_time")
    else:
        return aave_v3_filter_by_token(df, token).query("timestamp >= @start_time")


def _calculate_compound_interest_multiplier(rate: Decimal, seconds: Decimal | float) -> Decimal:
    """
    Calculate compound interest for a given rate and seconds between borrow and payback.
    Based on https://github.com/aave/aave-v3-core/blob/v1.16.2/contracts/protocol/libraries/math/MathUtils.sol#L51
    """
    exp = Decimal(seconds)  # ensure we use decimal
    if exp == 0:
        # No time elapsed, no interest accrued.
        return 1
    exp_minus_one = exp - Decimal(1)
    exp_minus_two = (exp - Decimal(2)) if exp > 2 else Decimal(0)
    base_power_two = rate * rate / (SECONDS_PER_YEAR * SECONDS_PER_YEAR)
    base_power_three = base_power_two * rate / SECONDS_PER_YEAR
    second_term = exp * exp_minus_one * base_power_two / Decimal(2)
    third_term = exp * exp_minus_one * exp_minus_two * base_power_three / Decimal(6)
    multiplier = rate * exp / SECONDS_PER_YEAR + second_term + third_term + 1
    # Alternative simplified Python implementation
    # multiplier = ((rate / SECONDS_PER_YEAR) + 1) ** exp
    # logger.warn(f'Multiplier: {multiplier}')
    return multiplier


def aave_v3_calculate_accrued_interests(df: DataFrame, start_time: datetime, end_time: datetime, amount: Decimal, token: str = "") -> AaveAccruedInterests:
    """
    Calculate total interest accrued for a given time period. The dataframe must be indexed by timestamp.
    Returns a tuple with actual start time, actual end time, and total interest accrued for a deposit, variable borrow debt, and stable borrow debt.
    Actual start time and actual end time are the first and last timestamp in the time period in the DataFrame.
    """
    df = aave_v3_filter_by_date_range(df, start_time, end_time, token)

    if len(df) <= 0:
        raise ValueError("No data found in date range %s - %s" % (start_time, end_time))

    # Loan starts on first row of the DataFrame
    actual_start_time = df.index[0]
    start_deposit_index = Decimal(df["liquidity_index"][0])
    start_variable_borrow_index = Decimal(df["variable_borrow_index"][0])

    # Loan ends on last row of the DataFrame
    actual_end_time = df.index[-1]
    end_deposit_index = Decimal(df["liquidity_index"][-1])
    end_variable_borrow_index = Decimal(df["variable_borrow_index"][-1])

    # Calculate interest for deposit.
    # Based on balanceOf() https://github.com/aave/aave-v3-core/blob/v1.16.2/contracts/protocol/tokenization/AToken.sol#L131
    deposit_scaled_amount = amount / start_deposit_index
    deposit_final_amount = deposit_scaled_amount * end_deposit_index
    deposit_interest = deposit_final_amount - amount

    # Calculate interest for variable borrow.
    # Based on https://github.com/aave/aave-v3-core/blob/v1.16.2/contracts/protocol/tokenization/VariableDebtToken.sol#L78
    variable_borrow_interest = (end_variable_borrow_index / start_variable_borrow_index) * amount - amount

    # Calculate interest for stable borrow. The applied interest rate is the stable borrow rate at the end of the loan.
    # Based on balanceOf() https://github.com/aave/aave-v3-core/blob/v1.16.2/contracts/protocol/tokenization/StableDebtToken.sol#L102
    stable_borrow_interest = amount * _calculate_compound_interest_multiplier(Decimal(df["stable_borrow_rate"][-1]) / RAY, (actual_end_time - actual_start_time).total_seconds()) - amount

    return AaveAccruedInterests(
        actual_start_time=actual_start_time,
        actual_end_time=actual_end_time,
        deposit_interest=deposit_interest,
        variable_borrow_interest=variable_borrow_interest,
        stable_borrow_interest=stable_borrow_interest,
    )


# Simplified shortcut functions for calculating accrued interest


def aave_v3_calculate_accrued_deposit_interest(df: DataFrame, start_time: datetime, end_time: datetime, amount: Decimal, token: str = "") -> AaveAccruedInterest:
    result = aave_v3_calculate_accrued_interests(df, start_time, end_time, amount, token)
    return AaveAccruedInterest(
        actual_start_time=result.actual_start_time,
        actual_end_time=result.actual_end_time,
        interest=result.deposit_interest,
    )


def aave_v3_calculate_accrued_variable_borrow_interest(df: DataFrame, start_time: datetime, end_time: datetime, amount: Decimal, token: str = "") -> AaveAccruedInterest:
    result = aave_v3_calculate_accrued_interests(df, start_time, end_time, amount, token)
    return AaveAccruedInterest(
        actual_start_time=result.actual_start_time,
        actual_end_time=result.actual_end_time,
        interest=result.variable_borrow_interest,
    )


def aave_v3_calculate_accrued_stable_borrow_interest(df: DataFrame, start_time: datetime, end_time: datetime, amount: Decimal, token: str = "") -> AaveAccruedInterest:
    result = aave_v3_calculate_accrued_interests(df, start_time, end_time, amount, token)
    return AaveAccruedInterest(
        actual_start_time=result.actual_start_time,
        actual_end_time=result.actual_end_time,
        interest=result.stable_borrow_interest,
    )
