"""Vault metrics calculations.

- Calculate various performance reports and charts for vaults.
- `For performance stats see FFN <https://pmorissette.github.io/ffn/quick.html>`__.
"""

import datetime
import logging
from typing import Literal, TypeAlias, Optional
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from plotly.subplots import make_subplots
from plotly.graph_objects import Figure
import plotly.io as pio
from tqdm.auto import tqdm
from ffn.core import PerformanceStats
from ffn.core import calc_stats
from ffn.utils import fmtn, fmtp

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.research.value_table import format_series_as_multi_column_grid
from eth_defi.token import is_stablecoin_like
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow
from eth_defi.vault.risk import get_vault_risk, VaultTechnicalRisk
from eth_defi.compat import native_datetime_utc_now


logger = logging.getLogger(__name__)

#: Percent as the floating point.
#:
#: 0.01 = 1%
Percent: TypeAlias = float


def fmt_one_decimal_or_int(x: float | None) -> str:
    """Display fees to .1 accuracy if there are .1 fractions, otherwise as int."""

    if x is None or x == "-":
        # "-" is legacy data, should not be used anymore
        return "?"

    y = round(float(x * 100), 1)
    return f"{y:.0f}%" if y.is_integer() else f"{y:.1f}%"


def create_fee_label(
    management_fee_annual: Percent,
    performance_fee: Percent,
    deposit_fee: Percent | None,
    withdrawal_fee: Percent | None,
):
    """Create 2% / 20% style labels to display variosu kinds of vault fees.

    Order is: management / performance / deposit / withdrawal fees.
    """

    # All fees zero
    if management_fee_annual == 0 and performance_fee == 0 and deposit_fee == 0 and withdrawal_fee == 0:
        return "0% / 0%"

    if deposit_fee in (0, None) and withdrawal_fee in (0, None):
        return f"{fmt_one_decimal_or_int(management_fee_annual)} / {fmt_one_decimal_or_int(performance_fee)}"

    return f"{fmt_one_decimal_or_int(management_fee_annual)} / {fmt_one_decimal_or_int(performance_fee)} / {fmt_one_decimal_or_int(deposit_fee)} / {fmt_one_decimal_or_int(withdrawal_fee)}"


def resample_returns(
    returns_1h: pd.Series,
    freq="D",
) -> pd.Series:
    """Calculate returns from resampled returns series.

    :param returns_1h:
        The original returns series.
    """

    # Wealth index from hourly returns
    wealth = (1.0 + returns_1h).cumprod()
    # Take last wealth per period and compute period-over-period returns
    wealth_resampled = wealth.resample(freq).last()
    returns = wealth_resampled.dropna().pct_change().fillna(0.0)
    return returns


def calculate_returns(
    share_price: pd.Series,
    freq="D",
) -> pd.Series:
    """Calculate returns from resampled share price series."""

    share_price = share_price.resample(freq).last()
    returns = share_price.dropna().pct_change().fillna(0.0)
    return returns


def calculate_cumulative_returns(
    cleaned_returns: pd.Series,
    freq="D",
):
    """Takes a returns series and calculates cumulative returns.

    - The cleaned returns series is created by :py:mod:`eth_defi.research.wrangle_vault_prices`.
    """
    assert isinstance(cleaned_returns, pd.Series)
    assert isinstance(cleaned_returns.index, pd.DatetimeIndex), "returns must have DatetimeIndex"

    s = cleaned_returns

    # Wealth index and cumulative returns
    wealth = (1.0 + s).cumprod()
    if freq is None:
        cum = wealth - 1.0
    else:
        # Resample the wealth index correctly, then convert to cumulative returns
        wealth_resampled = wealth.resample(freq).last()
        cum = wealth_resampled - 1.0

    # Make the first point baseline 0.0
    if len(cum) > 0:
        cum.iloc[0] = 0.0
    return cum


def zero_out_near_zero_prices(s: pd.Series, eps: float = 1e-9, clip_negatives: bool = True) -> pd.Series:
    """
    Replace values with |x| < eps by 0. Optionally clip negatives to 0.
    Keeps NaN as-is, turns {+/-} inf into NaN.
    """
    s = pd.Series(s, dtype="float64").copy()
    s[~np.isfinite(s)] = np.nan
    if clip_negatives:
        s = s.clip(lower=0.0)
    # Zero-out tiny magnitudes
    s = s.where(~np.isclose(s, 0.0, atol=eps), 0.0)
    return s


def calculate_net_profit(
    start: datetime.datetime,
    end: datetime.datetime,
    share_price_start: float,
    share_price_end: float,
    management_fee_annual: Percent,
    performance_fee: Percent,
    deposit_fee: Percent | None,
    withdrawal_fee: Percent | None,
    seconds_in_year=365.25 * 86400,
) -> Percent:
    """Calculate profit after external fees have been reduced from the share price change.

    :param start:
        Start datetime of the investment period.

    :param end:
        End datetime of the investment period.

    :param share_price_start:
        Share price at the start of the investment period.

    :param share_price_end:
        Share price at the end of the investment period.

    :param management_fee_annual:
        Annual management fee as a percent (0.02 = 2% per year).

    :param performance_fee:
        Performance fee as a percent (0.20 = 20% of profits).

    :param deposit_fee:
        Deposit fee as a percent (0.01 = 1% fee), or None if no fee.

    :param withdrawal_fee:
        Withdrawal fee as a percent (0.01 = 1% fee), or None if no fee.

    :return:
        Net profit as a floating point (0.10 = 10% profit).
    """

    assert isinstance(start, datetime.datetime), f"start must be datetime, got {type(start)}"
    assert isinstance(end, datetime.datetime), f"end must be datetime, got {type(end)}"
    assert end > start, "End datetime must be after start datetime"
    assert share_price_start > 0, "Start share price must be positive"
    assert share_price_end >= 0, "End share price must be non-negative"
    if management_fee_annual in (None, "-"):
        # - is legacy
        management_fee_annual = 0.0
    assert 0 <= management_fee_annual < 1, "Management fee must be between 0 and 1"
    if performance_fee in (None, "-"):
        # - is legacy
        performance_fee = 0.0
    assert 0 <= performance_fee < 1, "Performance fee must be between 0 and 1"
    if deposit_fee is None:
        deposit_fee = 0.0
    if withdrawal_fee is None:
        withdrawal_fee = 0.0
    assert 0 <= deposit_fee < 1, "Deposit fee must be between 0 and 1"
    assert 0 <= withdrawal_fee < 1, "Withdrawal fee must be between 0 and 1"

    delta = end - start
    years = delta.total_seconds() / seconds_in_year
    gross_return = (share_price_end / share_price_start) - 1.0
    return_after_management = gross_return - (management_fee_annual * years)
    if return_after_management > 0:
        net_fund_return = return_after_management * (1 - performance_fee)
    else:
        net_fund_return = return_after_management
    net_profit = (1 - deposit_fee) * (1 + net_fund_return) * (1 - withdrawal_fee) - 1
    return net_profit


def calculate_net_returns_from_price(
    name: str,
    share_price: pd.Series,
    management_fee_annual: Percent | None,
    performance_fee: Percent | None,
    deposit_fee: Percent | None,
    withdrawal_fee: Percent | None,
    seconds_in_year=365.25 * 86400,
    zero_epsilon=0.001,
    freq="h",
) -> pd.Series:
    """Convert a share price series to net return series after fees.

    :param name:
        For debugging

    :param share_price:
        Share price series with datetime index.

    :param management_fee_annual:
        Annual management fee as a percent (0.02 = 2% per year).

    :param performance_fee:
        Performance fee as a percent (0.20 = 20% of profits).

    :param deposit_fee:
        Deposit fee as a percent (0.01 = 1% fee), or None if no fee.

    :param withdrawal_fee:
        Withdrawal fee as a percent (0.01 = 1% fee), or None if no fee.

    :param freq:
        The time series frequency (hourly, daily, etc) for management fee calculation.

    :return:
        Cumulative net profit as a floating point (0.10 = 10% profit).
    """

    assert isinstance(share_price, pd.Series), f"share_price must be pandas Series, got {type(share_price)}"
    assert isinstance(share_price.index, pd.DatetimeIndex), "share_price must have DatetimeIndex"

    if management_fee_annual in (None, "-"):
        management_fee_annual = 0.0
    assert 0 <= management_fee_annual < 1, "Management fee must be between 0 and 1"
    if performance_fee in (None, "-"):
        performance_fee = 0.0
    assert 0 <= performance_fee < 1, "Performance fee must be between 0 and 1"
    if deposit_fee is None:
        deposit_fee = 0.0
    if withdrawal_fee is None:
        withdrawal_fee = 0.0
    assert 0 <= deposit_fee < 1, "Deposit fee must be between 0 and 1"
    assert 0 <= withdrawal_fee < 1, "Withdrawal fee must be between 0 and 1"
    if deposit_fee is None:
        deposit_fee = 0.0
    if withdrawal_fee is None:
        withdrawal_fee = 0.0
    assert 0 <= deposit_fee < 1, "Deposit fee must be between 0 and 1"
    assert 0 <= withdrawal_fee < 1, "Withdrawal fee must be between 0 and 1"

    if len(share_price) == 0:
        return share_price

    if len(share_price) == 1:
        return pd.Series([0], index=share_price.index)

    sp = share_price

    # Epsilon issues
    #
    # array([0.00000000e+00, 2.99184722e-06, 1.99455884e-06, 9.97277433e-07,
    #        9.97276438e-07, 9.97275444e-07, 9.97274449e-07, 9.97273454e-07,
    #        9.97272460e-07, 9.97271465e-07, 9.97270471e-07, 9.97269476e-07,
    #        9.97268482e-07, 9.97267487e-07, 9.97266492e-07, 9.97265498e-07,
    #        9.97264504e-07, 9.97263509e-07, 9.97262514e-07, 9.97261520e-07,
    #        9.97260525e-07, 9.97259531e-07, 9.97258536e-07, 9.97257542e-07,
    #        9.97256547e-07, 9.97255553e-07, 9.97254558e-07, 9.97253564e-07,
    #        9.97252569e-07, 9.97251575e-07, 9.97250580e-07])
    sp = zero_out_near_zero_prices(sp, eps=zero_epsilon)

    # Find first strictly positive, finite price to avoid division by zero
    valid = np.isfinite(sp.values) & (sp.values > 0.0)
    if not valid.any():
        # No valid start price -> return zeros
        return pd.Series(0.0, index=sp.index)

    first_pos_idx = sp.index[np.argmax(valid)]
    sp_slice = sp.loc[first_pos_idx:]

    start_time = sp_slice.index[0]
    share_price_start = sp_slice.iloc[0]

    deltas = sp_slice.index - start_time
    years = deltas.total_seconds() / seconds_in_year

    gross_returns = (sp_slice / share_price_start) - 1.0
    return_after_management = gross_returns - (management_fee_annual * years)

    # Apply performance fee only on positive returns
    net_fund_returns = return_after_management.where(
        return_after_management <= 0.0,
        return_after_management * (1.0 - performance_fee),
    )

    net_profits_slice = (1.0 - deposit_fee) * (1.0 + net_fund_returns) * (1.0 - withdrawal_fee) - 1.0

    # Ensure t0 is 0 return
    net_profits_slice.iloc[0] = 0.0

    # Pre-start values are 0
    out = pd.Series(0.0, index=sp.index, dtype=float)
    out.loc[net_profits_slice.index] = net_profits_slice

    return out


def calculate_net_returns_from_gross(
    name: str,
    cumulative_returns: pd.Series,
    management_fee_annual: Optional[Percent],
    performance_fee: Optional[Percent],
    deposit_fee: Optional[Percent],
    withdrawal_fee: Optional[Percent],
    seconds_in_year=365.25 * 86400,
) -> pd.Series:
    """Convert a cumulative gross return series to a cumulative net return series after fees.

    This function correctly models a High-Water Mark (HWM) for performance fees,
    which requires an iterative calculation (a loop). This loop operates on
    Numpy arrays for maximum speed.

    - Management fees are accrued based on the time delta of each period.
    - Performance fees are charged only on profits above the highest *net* value.
    - Deposit fees are applied once at the start (t=0).
    - Withdrawal fees are applied once at the end (t=T).

    :param name:
        Name for the returned pandas Series.
    :param cumulative_returns:
        A pandas Series with a DatetimeIndex representing the
        cumulative *gross* return index (e.g., 1.0, 1.02, 1.05) OR
        cumulative *gross* profit (e.g., 0.0, 0.02, 0.05).
    :param management_fee_annual:
        Annual management fee as a decimal (e.g., 0.02 for 2%).
    :param performance_fee:
        Performance fee as a decimal (e.g., 0.20 for 20% of profits
        above the High-Water Mark).
    :param deposit_fee:
        Fee applied to the initial deposit as a decimal (e.g., 0.01 for 1%).
    :param withdrawal_fee:
        Fee applied to the final withdrawal as a decimal (e.g., 0.01 for 1%).
    :param seconds_in_year:
        The number of seconds in a year for precise management fee accrual.
    :return:
        A pandas Series of the cumulative *net profit* (e.g., 0.10 for 10%).
    """
    virtual_share_price = cumulative_returns + 1.0

    return calculate_net_returns_from_price(
        name=name,
        share_price=virtual_share_price,
        management_fee_annual=management_fee_annual,
        performance_fee=performance_fee,
        deposit_fee=deposit_fee,
        withdrawal_fee=withdrawal_fee,
    )


def calculate_sharpe_ratio_from_returns(
    hourly_returns: pd.Series,
    risk_free_rate: float = 0.00,
    year_multiplier: float = 365,
) -> float:
    """
    Calculate annualized Sharpe ratio from hourly returns.

    :param hourly_returns: Pandas Series of hourly percentage returns.
    :param risk_free_rate: Annualized risk-free rate (default 2%).
    :return: Sharpe ratio as a float.
    """

    assert isinstance(hourly_returns, pd.Series), f"hourly_returns must be a pandas Series, got {type(hourly_returns)}"

    if len(hourly_returns) < 2:
        return np.nan  # Not enough data

    # Annualize mean return (assuming compounding)
    mean_hourly_return = hourly_returns.mean()
    annualized_return = mean_hourly_return * year_multiplier  # ~8760 hours/year

    # Annualize volatility
    std_hourly_return = hourly_returns.std()
    annualized_volatility = std_hourly_return * np.sqrt(year_multiplier)

    # Sharpe ratio
    if annualized_volatility == 0:
        return np.nan  # Avoid division by zero
    sharpe = (annualized_return - risk_free_rate) / annualized_volatility

    return sharpe


def calculate_lifetime_metrics(
    df: pd.DataFrame,
    vault_db: VaultDatabase | dict[VaultSpec, VaultRow],
    returns_column: str = "returns_1h",
) -> pd.DataFrame:
    """Calculate lifetime metrics for each vault in the provided DataFrame.

    - All-time returns
    - 3M returns, latest
    - 1M returns, latest
    - Volatility (3M)

    Lookback based on the last entry.

    :param vault_db:
        Pass all vaults or subset of vaults as VaultRows, or full VaultDatabase

    :return:
        DataFrame, one row per vault.
    """
    assert isinstance(vault_db, (VaultDatabase, dict)), f"Expected vault_db to be VaultDatabase, got {type(vault_db)}"
    assert isinstance(df.index, pd.DatetimeIndex)

    if isinstance(vault_db, VaultDatabase):
        vaults_by_id = vault_db.rows
    else:
        vaults_by_id = vault_db

    assert isinstance(vaults_by_id, dict), "vaults_by_id should be a dictionary of vault metadata"

    month_ago = df.index.max() - pd.Timedelta(days=30)
    three_months_ago = df.index.max() - pd.Timedelta(days=90)

    def process_vault_group(group):
        """Process a single vault group to calculate metrics

        :param group:
            Price DataFrame for a single vault
        ."""
        # Extract the group name (id_val)
        id_val = group["id"].iloc[0]

        # Sort by timestamp just to be safe
        # group = group.sort_index()

        # Extract vault metadata
        vault_spec = VaultSpec.parse_string(id_val, separator="-")
        vault_metadata: VaultRow = vaults_by_id.get(vault_spec)

        assert vault_metadata, f"Vault metadata not found for {id_val}. This vault is present in price data, but not in metadata entries. We have {len(vaults_by_id)} metadata entries."

        name = vault_metadata.get("Name")
        denomination = vault_metadata.get("Denomination")

        max_nav = group["total_assets"].max()
        current_nav = group["total_assets"].iloc[-1]
        chain_id = group["chain"].iloc[-1]
        mgmt_fee = vault_metadata["Mgmt fee"]
        perf_fee = vault_metadata["Perf fee"]
        deposit_fee = vault_metadata.get("Deposit fee")
        withdrawal_fee = vault_metadata.get("Withdraw fee")
        event_count = group["event_count"].iloc[-1]
        protocol = vault_metadata["Protocol"]
        risk = get_vault_risk(protocol, vault_metadata["Address"])
        risk_numeric = risk.value if isinstance(risk, VaultTechnicalRisk) else None

        lockup = vault_metadata.get("Lock up", datetime.timedelta(0))

        # Do we know fees for this vault
        known_fee = mgmt_fee is not None and perf_fee is not None

        # Calculate lifetime return using cumulative product approach
        with warnings.catch_warnings():
            # We may have severeal division by zero if the share price starts at 0
            warnings.simplefilter("ignore", RuntimeWarning)

            start_date = group.index.min()
            end_date = group.index.max()

            lifetime_return = group.iloc[-1]["share_price"] / group.iloc[0]["share_price"] - 1

            if known_fee:
                lifetime_return_net = calculate_net_profit(
                    start=start_date,
                    end=end_date,
                    share_price_start=group.iloc[0]["share_price"],
                    share_price_end=group.iloc[-1]["share_price"],
                    management_fee_annual=mgmt_fee,
                    performance_fee=perf_fee,
                    deposit_fee=deposit_fee,
                    withdrawal_fee=withdrawal_fee,
                )
            else:
                lifetime_return_net = None

            # Calculate CAGR
            # Get the first and last date
            age = years = (end_date - start_date).days / 365.25
            cagr = (1 + lifetime_return) ** (1 / years) - 1 if years > 0 else np.nan

            if known_fee:
                cagr_net = (1 + lifetime_return_net) ** (1 / years) - 1 if years > 0 else np.nan
            else:
                cagr_net = None

            last_three_months = group.loc[three_months_ago:]
            last_month = group.loc[month_ago:]

            # Calculate 3 months CAGR
            # Get the first and last date
            if len(last_three_months) >= 2:
                start_date = last_three_months.index.min()
                end_date = last_three_months.index.max()
                years = (end_date - start_date).days / 365.25

                returns_series = resample_returns(
                    last_three_months["returns_1h"],
                    freq="D",
                )

                three_month_returns = last_three_months.iloc[-1]["share_price"] / last_three_months.iloc[0]["share_price"] - 1

                three_months_cagr = (1 + three_month_returns) ** (1 / years) - 1 if years > 0 else np.nan

                if known_fee:
                    three_months_return_net = calculate_net_profit(
                        start=start_date,
                        end=end_date,
                        share_price_start=last_three_months.iloc[0]["share_price"],
                        share_price_end=last_three_months.iloc[-1]["share_price"],
                        management_fee_annual=mgmt_fee,
                        performance_fee=perf_fee,
                        deposit_fee=deposit_fee,
                        withdrawal_fee=withdrawal_fee,
                    )
                    three_months_cagr_net = (1 + three_months_return_net) ** (1 / years) - 1 if years > 0 else np.nan
                else:
                    three_months_return_net = None
                    three_months_cagr_net = None

                # Daily-equivalent volatility from hourly returns (multiply by sqrt(24) to scale from hourly to daily)
                three_months_volatility = returns_series.std() * np.sqrt(30)
                # three_months_volatility = 0

                three_months_sharpe = calculate_sharpe_ratio_from_returns(returns_series)
                three_months_sharpe_net = calculate_sharpe_ratio_from_returns(returns_series)

            else:
                # We have not collected data for the last three months,
                # because our stateful reader decided the vault is dead
                three_months_cagr = 0
                three_months_cagr_net = 0
                three_months_volatility = 0
                three_month_returns = 0
                three_months_return_net = 0
                three_months_sharpe_net = 0
                three_months_sharpe = 0

            if len(last_month) >= 2:
                start_date = last_month.index.min()
                end_date = last_month.index.max()
                years = (end_date - start_date).days / 365.25

                one_month_returns = last_month.iloc[-1]["share_price"] / last_month.iloc[0]["share_price"] - 1
                one_month_cagr = (1 + one_month_returns) ** (1 / years) - 1 if years > 0 else np.nan

                if known_fee:
                    one_month_returns_net = calculate_net_profit(
                        start=start_date,
                        end=end_date,
                        share_price_start=last_month.iloc[0]["share_price"],
                        share_price_end=last_month.iloc[-1]["share_price"],
                        management_fee_annual=mgmt_fee,
                        performance_fee=perf_fee,
                        deposit_fee=deposit_fee,
                        withdrawal_fee=withdrawal_fee,
                    )
                    one_month_cagr_net = (1 + one_month_returns_net) ** (1 / years) - 1 if years > 0 else np.nan
                else:
                    one_month_returns_net = None
                    one_month_cagr_net = None

            else:
                # We have not collected data for the last month,
                # because our stateful reader decided the vault is dead
                one_month_cagr = 0
                one_month_returns = 0
                one_month_returns_net = 0
                one_month_cagr_net = 0

        fee_label = create_fee_label(
            management_fee_annual=mgmt_fee,
            performance_fee=perf_fee,
            deposit_fee=deposit_fee,
            withdrawal_fee=withdrawal_fee,
        )

        last_updated_at = group.index.max()
        last_updated_block = group.loc[last_updated_at]["block_number"]

        return pd.Series(
            {
                "name": name,
                "lifetime_return": lifetime_return,
                "lifetime_return_net": lifetime_return_net,
                "cagr": cagr,
                "cagr_net": cagr_net,
                "three_months_returns": three_month_returns,
                "three_months_returns_net": three_months_return_net,
                "three_months_cagr": three_months_cagr,
                "three_months_cagr_net": three_months_cagr_net,
                "three_months_sharpe": three_months_sharpe,
                "three_months_sharpe_net": three_months_sharpe_net,
                "three_months_volatility": three_months_volatility,
                "one_month_returns": one_month_returns,
                "one_month_returns_net": one_month_returns_net,
                "one_month_cagr": one_month_cagr,
                "one_month_cagr_net": one_month_cagr_net,
                "denomination": denomination,
                "chain": get_chain_name(chain_id),
                "peak_nav": max_nav,
                "current_nav": current_nav,
                "years": age,
                "mgmt_fee": mgmt_fee,
                "perf_fee": perf_fee,
                "deposit_fee": deposit_fee,
                "withdraw_fee": withdrawal_fee,
                "fee_label": fee_label,
                "lockup": lockup,
                "event_count": event_count,
                "protocol": protocol,
                "risk": risk,
                "risk_numeric": risk_numeric,
                "id": id_val,
                "start_date": start_date,
                "end_date": end_date,
                "address": vault_spec.vault_address,
                "chain_id": vault_spec.chain_id,
                "stablecoinish": is_stablecoin_like(denomination),
                "last_updated_at": last_updated_at,
                "last_updated_block": last_updated_block,
            }
        )

    def process_vault_group_wrapped(group):
        try:
            process_vault_group(group)
        except Exception as e:
            logger.error("Error processing vault group %s: %s", group.name, e)
            raise RuntimeError(f"Error processing vault group {group.name}: {e}") from e

    # Enable tqdm progress bar for pandas
    tqdm.pandas(desc="Calculating vault performance metrics")

    # Use progress_apply instead of the for loop
    # results_df = df.groupby("id").progress_apply(process_vault_group)
    results_df = df.groupby("id", group_keys=False).progress_apply(process_vault_group)

    # Reset index to convert the grouped results to a regular DataFrame
    results_df = results_df.reset_index(drop=True)

    return results_df


def clean_lifetime_metrics(
    lifetime_data_df: pd.DataFrame,
    broken_max_nav_value=99_000_000_000,
    lifetime_min_nav_threshold=100.00,
    max_annualised_return=3.0,  # 300% max return
    min_events=25,
    logger=print,
) -> pd.DataFrame:
    """Clean lifetime data so we have only valid vaults.

    - Filter out vaults that have broken records or never saw daylight
    - See :py:func:`calculate_lifetime_metrics`.

    :return:
        Cleaned lifetime dataframe
    """

    # Filter FRAX vault with broken interface
    lifetime_data_df = lifetime_data_df[~lifetime_data_df.index.isna()]

    # Filter out MAAT Stargate V2 USDT
    # Not sure what's going on with this one and other ones with massive returns.
    # Rebase token?
    # Consider 10,000x returns as "valid"
    lifetime_data_df = lifetime_data_df[lifetime_data_df["cagr"] < 10_000]

    # Filter out some vaults that report broken NAV
    broken_mask = lifetime_data_df["peak_nav"] > broken_max_nav_value
    logger(f"Vault entries with too high NAV values filtered out: {len(lifetime_data_df[broken_mask])}")
    lifetime_data_df = lifetime_data_df[~broken_mask]

    # Filter out some vaults that have too little NAV (ATH NAV)
    broken_mask = lifetime_data_df["peak_nav"] <= lifetime_min_nav_threshold
    logger(f"Vault entries with too small ATH NAV values filtered out: {len(lifetime_data_df[broken_mask])}")
    lifetime_data_df = lifetime_data_df[~broken_mask]

    # Filter out with too HIGH CAGR
    broken_mask = lifetime_data_df["cagr"] >= max_annualised_return
    logger(f"Vaults abnormally high returns: {len(lifetime_data_df[broken_mask])}")
    lifetime_data_df = lifetime_data_df[~broken_mask]

    # Filter out some vaults that have not seen many deposit and redemptions
    broken_mask = lifetime_data_df["event_count"] < min_events
    logger(f"Vault entries with too few deposit and redeem events (min {min_events}) filtered out: {len(lifetime_data_df[broken_mask])}")
    lifetime_data_df = lifetime_data_df[~broken_mask]
    return lifetime_data_df


def combine_return_columns(
    gross: pd.Series,
    net: pd.Series,
    new_line=" ",
    mode: Literal["percent", "usd"] = "percent",
):
    """Create combined net / (gross) returns column for display.

    E.g. 8.3% (10.5%)

    :param gross:
        Gross returns series

    :param net:
        Net returns series

    :return:
        Combined string series
    """

    assert gross.index.equals(net.index), f"Gross and net series must have the same index {len(gross)} != {len(net)}"

    def _format_combined_percent(g, n):
        if n is not None and pd.isna(n) == False:
            return f"{n:.1%}{new_line}({g:.1%})"
        else:
            return f"---{new_line}({g:.1%})"

    def _format_combined_usd(g, n):
        if n:
            return f"{n:,.0f}{new_line}({g:,.0f})"
        else:
            return f"---{new_line}({g:.0f})"

    if mode == "percent":
        _format_combined = _format_combined_percent
    else:
        _format_combined = _format_combined_usd

    return pd.Series([_format_combined(g, n) for g, n in zip(gross, net)], index=gross.index)


def format_lifetime_table(
    df: pd.DataFrame,
    add_index=False,
    add_address=False,
    drop_blacklisted=True,
) -> pd.DataFrame:
    """Format table for human readable output.

    See :py:func:`calculate_lifetime_metrics`

    :param add_index:
        Add 1, 2, 3... index column

    :param add_address:
        Add address as a separate column.

        For vault address list copy-pasted.

    :param drop_blacklisted:
        Remove vaults we have manually flagged as troublesome.

    :return:
        Human readable data frame
    """

    df = df.copy()

    if drop_blacklisted:
        df = df.loc[df["risk"] != VaultTechnicalRisk.blacklisted]

    del df["start_date"]  # We have Age

    df["cagr"] = combine_return_columns(
        gross=df["cagr"],
        net=df["cagr_net"],
    )

    df["lifetime_return"] = combine_return_columns(
        gross=df["lifetime_return"],
        net=df["lifetime_return_net"],
    )

    df["three_months_cagr"] = combine_return_columns(
        gross=df["three_months_cagr"],
        net=df["three_months_cagr_net"],
    )

    # df["three_months_returns"] = combine_return_columns(
    #    gross=df["three_months_returns"],
    #    net=df["three_months_returns_net"],
    # )

    df["one_month_cagr"] = combine_return_columns(
        gross=df["one_month_cagr"],
        net=df["one_month_cagr_net"],
    )

    # df["one_month_returns"] = combine_return_columns(
    #    gross=df["one_month_returns"],
    #    net=df["one_month_returns_net"],
    # )

    df["current_nav"] = combine_return_columns(
        gross=df["peak_nav"],
        net=df["current_nav"],
        mode="usd",
    )

    df["three_months_volatility"] = df["three_months_volatility"].apply(lambda x: f"{x:.4f}")
    df["three_months_sharpe"] = df["three_months_sharpe"].apply(lambda x: f"{x:.1f}")
    df["event_count"] = df["event_count"].apply(lambda x: f"{x:,}")
    df["risk"] = df["risk"].apply(lambda x: x.get_risk_level_name() if x is not None else "Unknown")
    df["lockup"] = df["lockup"].apply(lambda x: f"{x.days}" if pd.notna(x) else "---")

    def _del(x):
        if x in df.columns:
            del df[x]
        return x

    # Combined to fee_label
    _del("mgmt_fee")
    _del("perf_fee")
    _del("deposit_fee")
    _del("withdraw_fee")

    # Combined
    _del("cagr_net")
    _del("lifetime_return_net")
    _del("three_months_cagr_net")
    _del("three_months_returns_net")
    _del("one_month_returns")
    _del("one_month_cagr_net")
    _del("one_month_returns_net")
    _del("three_months_sharpe_net")
    _del("three_months_returns")
    _del("peak_nav")
    _del("address")
    _del("chain_id")
    _del("end_date")
    _del("risk_numeric")
    _del("stablecoinish")
    _del("last_updated_at")
    _del("last_updated_block")

    df = df.rename(
        columns={
            "cagr": "Lifetime return ann. (net / gross)",
            "lifetime_return": "Lifetime return abs. (net / gross)",
            # "three_months_returns": "3M return abs. (net / gross)",
            "three_months_cagr": "3M return ann. (net / gross)",
            "three_months_volatility": "3M volatility",
            "three_months_sharpe": "3M sharpe",
            # "one_month_returns": "1M return abs. (net / gross)",
            "one_month_cagr": "1M return ann. (net / gross)",
            "event_count": "Deposit events",
            "current_nav": "TVL USD (current / peak)",
            "years": "Age (years)",
            "denomination": "Denomination",
            "chain": "Chain",
            "protocol": "Protocol",
            "risk": "Risk",
            # "end_date": "Latest deposit",
            "name": "Name",
            "lockup": "Lock up est. days",
            "fee_label": "Fees (mgmt / perf / dep / with)",
            "id": "id",
        }
    )

    # Check for manual humbling
    for c in df.columns:
        if c != "id":
            assert c[0].isupper() or c[0].isdigit(), f"Did not properly human label lifetime table column: {c}"

    if add_index:
        df.insert(0, "#", range(1, len(df) + 1))
        df = df.set_index("#")
    else:
        df = df.set_index("Name")

    if add_address:
        df["Address"] = df["id"].apply(lambda x: x.split("-")[1])

    return df


@dataclass(frozen=True, slots=True)
class VaultReport:
    """One vault data analysed"""

    vault_metadata: dict

    #: Rolling returns chart
    rolling_returns_chart: Figure

    #: Performance table
    #:
    #: Needs to have quantstats installed
    # performance_metrics_df: pd.DataFrame | None

    performance_stats: PerformanceStats

    daily_returns: pd.Series
    hourly_returns: pd.Series

    #: All hourly columns
    hourly_df: pd.DataFrame


def analyse_vault(
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
    spec: VaultSpec,
    returns_col: str = "returns_1h",
    logger=print,
    chart_frequency: Literal["hourly", "daily"] = "daily",
) -> VaultReport | None:
    """Create charts and tables to analyse a vault performance.

    - We plot our annualised 1 month rolling returns on the chart, to see how vaults move in the direction of the markets, or what kind of outliers there are

    :param vault_db:
        Database of all vault metadata

    :param price_df:
        Cleaned price and returns data for all vaults.

        Can be be in any time frame.

    :param id:
        Vault chain + address to analyse, e.g. "1-0x1234567890abcdef1234567890abcdef12345678"

    :param chart_frequency:
        Do we plot based on daily or hourly datapoints.

        Hourly data has too many points, chocking Plotly.

    :return:
        Analysis report to display.

        None if the vault does not have price data.
    """
    returns_df = prices_df

    id = spec.as_string_id()

    vault_metadata = vault_db.get(spec)
    if vault_metadata is None:
        assert vault_metadata, f"Vault with id {spec} not found in vault database"

    chain_name = get_chain_name(spec.chain_id)
    name = vault_metadata["Name"]
    subtitle = f"{vault_metadata['Symbol']} / {vault_metadata['Denomination']} {vault_metadata['Address']} on {chain_name}, on {vault_metadata['Protocol']} protocol"

    # Use cleaned returns data and resample it to something useful
    vault_df = returns_df.loc[returns_df["id"] == id]
    returns_series = returns_df.loc[returns_df["id"] == id][returns_col]

    cleaned_price_series = vault_df["share_price"]
    cleaned_price_series = cleaned_price_series
    daily_prices = cleaned_price_series.resample("D").last()  # Take last price of each day
    daily_returns = daily_prices.dropna().pct_change().dropna()

    hourly_prices = cleaned_price_series.resample("h").last()  # Take last price of each day
    hourly_returns = hourly_prices.dropna().pct_change().dropna()

    # logger(f"Examining vault {name}: {id}, having {len(returns_series):,} raw returns, {len(hourly_returns):,} hourly and {len(daily_returns):,} daily returns")
    nav_series = vault_df["total_assets"]

    # Uncleaned share price that may contain abnormal return values
    price_series = vault_df["share_price"]

    # Calculate cumulative returns (what $1 would grow to)
    cumulative_returns = (1 + hourly_returns).cumprod()

    if len(price_series) < 2:
        # f"Price data must have at least two rows: {vault_df}"
        return None

    start_share_price = vault_df["share_price"].iloc[0]
    end_share_price = vault_df["share_price"].iloc[-1]
    logger(f"Share price movement: {start_share_price:.4f} {vault_df.index[0]} -> {end_share_price:.4f} {vault_df.index[-1]}")

    # Create figure with secondary y-axis
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    if chart_frequency == "daily":
        price_series = price_series.resample("D").last()  # Resample to daily prices
        cumulative_returns = (1 + daily_returns).cumprod()
        nav_series = nav_series.resample("D").last()  # Resample NAV to daily
    else:
        # Assume default data is hourly
        pass

    # Add cumulative returns trace on a separate y-axis (share same axis as share price)
    # fig.add_trace(
    #    go.Scatter(x=cumulative_returns.index, y=cumulative_returns.values, name="Cumulative returns (cleaned)", line=dict(color="darkgreen", width=4), opacity=0.75),
    #     secondary_y=False,
    # )

    # Add share price trace on primary y-axis
    fig.add_trace(
        go.Scatter(x=price_series.index, y=price_series.values, name="Share Price", line=dict(color="green", width=4), opacity=0.75),
        secondary_y=False,
    )

    # Add NAV trace on secondary y-axis
    fig.add_trace(
        go.Scatter(x=nav_series.index, y=nav_series.values, name="TVL", line=dict(color="blue", width=4), opacity=0.75),
        secondary_y=True,
    )

    # Set titles and labels
    fig.update_layout(
        title=dict(text=f"{name}: Cumulative returns, TVL and share price<br><sub>{subtitle}</sub>", x=0.5, xanchor="center", y=0.95),
        hovermode="x unified",
        template=pio.templates.default,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="center", x=0.5),
        margin=dict(t=150),
    )

    # Set y-axes titles
    fig.update_yaxes(title_text=f"Share Price ({vault_metadata['Denomination']})", secondary_y=False)
    fig.update_yaxes(title_text=f"TVL ({vault_metadata['Denomination']})", secondary_y=True)

    performance_stats = calc_stats(daily_prices)
    performance_stats.name = name

    return VaultReport(
        vault_metadata=vault_metadata,
        rolling_returns_chart=fig,
        performance_stats=performance_stats,
        daily_returns=daily_returns,
        hourly_returns=hourly_returns,
        hourly_df=vault_df,
    )


def calculate_performance_metrics_for_all_vaults(
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
    logger=print,
    lifetime_min_nav_threshold=100.00,
    broken_max_nav_value=99_000_000_000,
    cagr_too_high=10_000,
    min_events=25,
) -> pd.DataFrame:
    """Calculate performance metrics for each vault.

    - Only applicable to stablecoin vaults as cleaning units are in USD
    - Clean up idle vaults that have never seen enough events to be considered active
    - Calculate lifetime returns, CAGR, NAV, etc.
    - Filter out results with abnormal values

    :return:
        DataFrame with lifetime metrics for each vault, indexed by vault name.
    """

    vaults_by_id = {f"{vault['_detection_data'].chain}-{vault['_detection_data'].address}": vault for vault in vault_db.values()}

    # Numpy complains about something
    # - invalid value encountered in reduce
    # - Boolean Series key will be reindexed to match DataFrame index.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        warnings.simplefilter("ignore", RuntimeWarning)
        lifetime_data_df = calculate_lifetime_metrics(
            prices_df,
            vaults_by_id,
            returns_column="returns_1h",
        )

    lifetime_data_df = lifetime_data_df.sort_values(by="cagr", ascending=False)
    lifetime_data_df = lifetime_data_df.set_index("name")

    assert not lifetime_data_df.index.duplicated().any(), f"There are duplicate ids in the index: {lifetime_data_df.index}"

    # Verify we no longer have duplicates
    # display(lifetime_data_df.index)
    assert not lifetime_data_df.index.dropna().duplicated().any(), f"There are still duplicate names in the index: {lifetime_data_df.index}"
    logger("Successfully made all vault names unique by appending chain information")

    logger(f"Calculated lifetime data for {len(lifetime_data_df):,} vaults")
    logger("Sample entrys of lifetime data:")

    #
    # Clean data
    #

    # Filter FRAX vault with broken interface
    lifetime_data_df = lifetime_data_df[~lifetime_data_df.index.isna()]

    # Filter out MAAT Stargate V2 USDT
    # Not sure what's going on with this one and other ones with massive returns.
    # Rebase token?
    # Consider 10,000x returns as "valid"
    lifetime_data_df = lifetime_data_df[lifetime_data_df["cagr"] < cagr_too_high]

    # Filter out some vaults that report broken NAV
    broken_mask = lifetime_data_df["peak_nav"] > broken_max_nav_value
    logger(f"Vault entries with too high NAV values filtered out: {len(lifetime_data_df[broken_mask])}")
    lifetime_data_df = lifetime_data_df[~broken_mask]

    # Filter out some vaults that have too little NAV (ATH NAV)
    broken_mask = lifetime_data_df["peak_nav"] <= lifetime_min_nav_threshold
    logger(f"Vault entries with too small ATH NAV values filtered out: {len(lifetime_data_df[broken_mask])}")
    lifetime_data_df = lifetime_data_df[~broken_mask]

    # Filter out some vaults that have not seen many deposit and redemptions
    broken_mask = lifetime_data_df["event_count"] < min_events
    logger(f"Vault entries with too few deposit and redeem events (min {min_events}) filtered out: {len(lifetime_data_df[broken_mask])}")
    lifetime_data_df = lifetime_data_df[~broken_mask]

    return lifetime_data_df


def format_vault_database(
    vault_db: VaultDatabase,
    index=True,
) -> pd.DataFrame:
    """Format vault database for human readable output.

    :param vault_db:
        Vault database to format

    :return:
        DataFrame with vault metadata, with human readable columns
    """

    rows = vault_db.rows
    data = list(rows.values())
    df = pd.DataFrame(data)

    # Build useful columns out of raw pickled Python data
    # _detection_data contains entries as ERC4262VaultDetection class
    entry: ERC4262VaultDetection
    df["Chain"] = df["_detection_data"].apply(lambda entry: get_chain_name(entry.chain))
    df["Protocol identified"] = df["_detection_data"].apply(lambda entry: entry.is_protocol_identifiable())
    df["Stablecoin denominated"] = df["_denomination_token"].apply(lambda token_data: is_stablecoin_like(token_data["symbol"]) if pd.notna(token_data) else False)
    df["ERC-7540"] = df["_detection_data"].apply(lambda entry: entry.is_erc_7540())
    df["ERC-7575"] = df["_detection_data"].apply(lambda entry: entry.is_erc_7575())
    df["Fee detected"] = df.apply(lambda row: (row["Mgmt fee"] is not None) or (row["Perf fee"] is not None), axis=1)
    # Event counts
    df["Deposit count"] = df["_detection_data"].apply(lambda entry: entry.deposit_count)
    df["Redeem count"] = df["_detection_data"].apply(lambda entry: entry.redeem_count)
    df["Total events"] = df["Deposit count"] + df["Redeem count"]
    df["Mgmt fee"] = df["Mgmt fee"].fillna("<unknown>")
    df["Perf fee"] = df["Mgmt fee"].fillna("<unknown>")
    df["Age"] = native_datetime_utc_now() - df["First seen"]
    df["NAV"] = df["NAV"].astype("float64")
    if index:
        df = df.sort_values(["Chain", "Address"])
        df = df.set_index(["Chain", "Address"])
    return df


def format_vault_header(vault_row: pd.Series) -> pd.Series:
    """Format vault header for human readable output.

    See :py:func:`format_vault_database`

    :return:
        DataFrame with formatted performance metrics
    """

    assert isinstance(vault_row, pd.Series), f"vault_row must be a pandas Series, got {type(vault_row)}"

    keys = [
        "Name",
        "Chain",
        "Address",
        "Denomination",
        "NAV",
        "First seen",
        "Total events",
        "Age",
    ]

    return vault_row[keys]


def format_ffn_performance_stats(
    report: PerformanceStats,
    prefix_series: pd.Series | None = None,
) -> pd.Series:
    """Format FFN report for human readable output.

    - Return a Series with formatted performance metrics
    - Multiple series can be combined to a comparison table

    :param prefix_data:
        Extra header data to insert.

    :param report:
        FFN performance report to format

    :return:
        DataFrame with formatted performance metrics
    """
    assert isinstance(report, PerformanceStats), f"report must be an instance of PerformanceStats, got {type(report)}"

    # Get the keys
    stat_definitions = report._stats()

    def _format(k, f, raw):
        # if rf is a series print nan
        if k == "rf" and not isinstance(raw, float):
            return np.nan
        elif f is None:
            return raw
        elif f == "p":
            return fmtp(raw)
        elif f == "n":
            return fmtn(raw)
        elif f == "dt":
            return raw.strftime("%Y-%m-%d")
        else:
            raise NotImplementedError("unsupported format %s" % f)

    keys = []
    values = []
    for key, name, typ in stat_definitions:
        if not name:
            continue
        keys.append(name)
        raw = getattr(report, key, "")
        values.append(_format(key, typ, raw))

    data_series = pd.Series(values, index=keys)

    if prefix_series is not None:
        return pd.concat([prefix_series, data_series])
    else:
        return data_series


def cross_check_data(
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
    printer=print,
) -> int:
    """Check that VaultDatabase has metadata for all price_df vaults and vice versa.

    :return:
        Number of problem entries.

        Should be zero.
    """

    vault_db_entries = set(k.as_string_id() for k in vault_db.keys())

    prices_df_ids = set(prices_df["chain"].astype(str) + "-" + prices_df["address"].astype(str))

    errors = 0
    for entry in prices_df_ids:
        if entry not in vault_db_entries:
            printer(f"Price data has entry {entry} that is not in vault database")
            errors += 1

    return errors


def calculate_daily_returns_for_all_vaults(df_work: pd.DataFrame) -> pd.DataFrame:
    """Calculate daily returns for each vault in isolation"""

    # Group by chain and address, then resample and forward fill

    df_work = df_work.set_index("timestamp")

    result_dfs = []
    for (chain_val, addr_val), group in df_work.groupby(["chain", "address"]):
        # Resample this group to daily frequency and forward fill
        resampled = group.resample("D").last()

        # Calculate daily returns
        resampled["daily_returns"] = resampled["share_price"].pct_change(fill_method=None).fillna(0)

        # Add back the groupby keys as they'll be dropped during resampling
        resampled["chain"] = chain_val
        resampled["address"] = addr_val

        result_dfs.append(resampled)

    # Concatenate all the processed groups
    df_result = pd.concat(result_dfs)

    return df_result


def calculate_hourly_returns_for_all_vaults(df_work: pd.DataFrame) -> pd.DataFrame:
    """Calculate hourly returns for each vault in isolation"""

    # Group by chain and address, then resample and forward fill

    assert isinstance(df_work, pd.DataFrame)
    assert isinstance(df_work.index, pd.DatetimeIndex), "DataFrame index must be a DatetimeIndex"

    result_dfs = []
    for (chain_val, addr_val), group in df_work.groupby(["chain", "address"]):
        # Resample this group to daily frequency and forward fill
        resampled = group.resample("D").last()

        # Calculate daily returns
        resampled["returns_1h"] = resampled["share_price"].pct_change(fill_method=None).fillna(0)

        # Add back the groupby keys as they'll be dropped during resampling
        resampled["chain"] = chain_val
        resampled["address"] = addr_val

        result_dfs.append(resampled)

    # Concatenate all the processed groups
    df_result = pd.concat(result_dfs)

    return df_result


def display_vault_chart_and_tearsheet(
    vault_spec: VaultSpec,
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
    render=True,
):
    """Render a chart and tearsheet for a single vault.

    - Use in notebooks

    :param render;
        Disable rendering in tests
    """

    from IPython.display import display, HTML

    vault_report = analyse_vault(
        vault_db=vault_db,
        prices_df=prices_df,
        spec=vault_spec,
        chart_frequency="daily",
        logger=lambda x: None,
    )

    chain_name = get_chain_name(vault_spec.chain_id)
    vault_name = vault_report.vault_metadata["Name"]

    title = HTML(f"<h2>Vault {vault_name} ({chain_name}): {vault_spec.vault_address})</h2><br>")

    if render:
        display(title)

    # Display returns figur
    returns_chart_fig = vault_report.rolling_returns_chart

    if render:
        returns_chart_fig.show()

    # Check raw montly share price numbers for each vault
    hourly_price_df = vault_report.hourly_df
    last_price_at = hourly_price_df.index[-1]
    last_price = hourly_price_df["share_price"].asof(last_price_at)
    last_block = hourly_price_df["block_number"].asof(last_price_at)
    month_ago = last_price_at - pd.DateOffset(months=1)
    month_ago_price = hourly_price_df["share_price"].asof(month_ago)
    month_ago_block = hourly_price_df["block_number"].asof(month_ago)

    # Price may be NA if vault is less than month old
    # assert not pd.isna(month_ago_price), f"Vault {vault_spec.chain_id}-{vault_spec.vault_address}: no price data for month ago {month_ago} found, last price at {last_price_at} is {last_price}"

    data = {
        "Vault": f"{vault_name} ({chain_name})",
        "Last price at": last_price_at,
        "Last price": last_price,
        "Block last price": f"{month_ago_block:,}",
        "Month ago": month_ago,
        "Block month ago": f"{month_ago_block:,}",
        "Month ago price": month_ago_price,
        "Monthly change %": (last_price - month_ago_price) / month_ago_price * 100,
    }

    df = pd.Series(data)
    # display(df)

    # Display FFN stats
    performance_stats = vault_report.performance_stats
    if performance_stats is not None:
        stats_df = format_ffn_performance_stats(performance_stats)

        multi_column_df = format_series_as_multi_column_grid(stats_df)

        # display(stats_df)
        out_table = HTML(multi_column_df.to_html(float_format="{:,.2f}".format, index=True))
        if render:
            display(out_table)
    else:
        if render:
            print(f"Vault {vault_spec.chain_id}-{vault_spec.vault_address}: performance metrics not available, is quantstats library installed?")


def export_lifetime_row(row: pd.Series) -> dict:
    """Export lifetime row to JSON serializable dict.

    :param row:
        Lifetime metrics row

    :return:
        JSON serializable dict
    """

    out = row.to_dict()

    # Convert any non-serializable values
    for key, value in out.items():

        if isinstance(value, (np.floating, np.integer)):
            out[key] = value.item()
        elif isinstance(value, pd.Timestamp):
            out[key] = value.isoformat()
        elif isinstance(value, pd.Timedelta):
            out[key] = value.total_seconds()
        elif isinstance(value, datetime.timedelta):
            out[key] = value.total_seconds()
        elif isinstance(value, VaultTechnicalRisk):
            out[key] = value.get_risk_level_name()
        elif pd.isna(value):
            out[key] = None

    if row["address"] == "0x00c8a649c9837523ebb406ceb17a6378ab5c74cf":
        import ipdb ; ipdb.set_trace()

    # Map some legacy names
    # TODO: Remove after confirmed frontend does not need these
    out["management_fee"] = out.get("mgmt_fee", None)
    out["performance_fee"] = out.get("perf_fee", None)

    # Fix some legacy data which did not use these values yet
    if out.get("mgmt_fee", None) is None:
        out["deposit_fee"] = None
        out["withdraw_fee"] = None

    return out
