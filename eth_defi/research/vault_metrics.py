"""Vault metrics calculations.

- Calculate various performance reports and charts for vaults.
- `For performance stats see FFN <https://pmorissette.github.io/ffn/quick.html>`__.
"""

import datetime
import logging
import math
import warnings
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Literal, Optional, TypeAlias

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from ffn.core import PerformanceStats, calc_stats
from ffn.utils import fmtn, fmtp
from plotly.graph_objects import Figure
from plotly.subplots import make_subplots
from slugify import slugify
from tqdm.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.research.value_table import format_grouped_series_as_multi_column_grid, format_series_as_multi_column_grid
from eth_defi.research.wrangle_vault_prices import forward_fill_vault
from eth_defi.token import is_stablecoin_like, normalise_token_symbol
from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.flag import ABNORMAL_SHARE_PRICE, ABNORMAL_TVL, ABNORMAL_VOLATILITY, VaultFlag, get_notes
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

logger = logging.getLogger(__name__)

#: Percent as the floating point.
#:
#: 0.01 = 1%
Percent: TypeAlias = float

Period: TypeAlias = Literal["1W", "1M", "3M", "6M", "1Y", "lifetime"]

USDollarAmount: TypeAlias = float

#: Vaults with TVL above this are considered broken smart contracts
MAX_VALID_NAV: USDollarAmount = 100_000_000_000

#: Vaults with share price above this are considered broken smart contracts
MAX_VALID_SHARE_PRICE: USDollarAmount = 1_000_000

#: Vaults with annualised volatility above this are considered broken.
#: This catches low-TVL Hyperliquid vaults with one or few trades
#: that produce extreme volatility numbers.
MAX_VALID_VOLATILITY: Percent = 10_000


@dataclass(slots=True)
class PeriodMetrics:
    """Tearsheet metrics for one period."""

    period: Period

    #: Error reason if metrics could not be calculated, None if successful
    error_reason: str | None = None

    #: When was start share price sampled
    period_start_at: pd.Timestamp | None = None

    #: When was end share price sampled
    period_end_at: pd.Timestamp | None = None

    #: Share price at beginning
    share_price_start: USDollarAmount | None = None

    #: Share price at end
    share_price_end: USDollarAmount | None = None

    #: Number of raw datapoints used
    raw_samples: int = 0

    samples_start_at: pd.Timestamp | None = None

    samples_end_at: pd.Timestamp | None = None

    #: Number of daily datapoitns used
    daily_samples: int = 0

    #: How much absolute returns we had
    returns_gross: Percent | None = None

    returns_net: Percent | None = None

    #: Compounding annual returns
    cagr_gross: Percent | None = None

    cagr_net: Percent | None = None

    #: Annualised volatility, calculated based on daily returns
    volatility: Percent | None = None

    #: Sharpe ratio
    sharpe: float | None = None

    #: Period maximum drawdown
    max_drawdown: Percent | None = None

    #: TVL at the start of the period
    tvl_start: USDollarAmount | None = None

    #: TVL at the end of the period
    tvl_end: USDollarAmount | None = None

    #: Minimum TVL in the period
    tvl_low: USDollarAmount | None = None

    #: Maximum TVL in the period
    tvl_high: USDollarAmount | None = None

    #: Rank among all vaults (1 = best), based on CAGR
    ranking_overall: int | None = None

    #: Rank among vaults on the same chain (1 = best), based on CAGR
    ranking_chain: int | None = None

    #: Rank among vaults in the same protocol (1 = best), based on CAGR
    ranking_protocol: int | None = None


#: Period -> Perioud duration, max sparse sample mismatch
LOOKBACK_AND_TOLERANCES: dict[Period, tuple[pd.DateOffset, pd.Timedelta]] = {
    "1W": (pd.DateOffset(days=7), pd.Timedelta(days=7 + 5)),
    "1M": (pd.DateOffset(days=30), pd.Timedelta(days=60)),
    "3M": (pd.DateOffset(days=3 * 30), pd.Timedelta(days=90 + 45)),
    "6M": (pd.DateOffset(days=6 * 30), pd.Timedelta(days=180 + 45)),
    "1Y": (pd.DateOffset(days=12 * 30), pd.Timedelta(days=365 + 45)),
    "lifetime": (pd.DateOffset(years=100), pd.Timedelta(days=100 * 365)),
}


def fmt_one_decimal_or_int(x: float | None) -> str:
    """Display fees to .1 accuracy if there are .1 fractions, otherwise as int."""

    if x is None or x == "-":
        # "-" is legacy data, should not be used anymore
        return "?"

    y = round(float(x * 100), 1)
    return f"{y:.0f}%" if y.is_integer() else f"{y:.1f}%"


def slugify_protocol(protocol: str) -> str:
    """Create a slug from protocol name for URLs.

    :param protocol:
        The protocol name.
    """

    if "unknown" in protocol.lower() or "not identifier" in protocol.lower():
        return "unknown"

    return slugify(protocol)


def slugify_vault(
    name: str | None,
    symbol: str | None,
    address: str,
    existing_slugs: set[str],
) -> str:
    """Create a slug from vault metadata for URLs."""

    # We have name but no symbol
    if (not name) and symbol and len(symbol) >= 3:
        name = symbol

    if not name or len(name) <= 2:
        return address

    base_slug = slugify(name)

    if base_slug not in existing_slugs:
        return base_slug

    for attempt in range(2, 20):
        new_slug = f"{base_slug}-{attempt}"
        if new_slug not in existing_slugs:
            return new_slug

    return address


#: Map internal chain names to tradingstrategy.ai website slugs.
#:
#: Only chains that differ from ``chain_name.lower()`` need an entry.
_CHAIN_SLUG_OVERRIDES = {
    "hypercore": "hyperliquid",
}


def _get_chain_slug(chain_name: str) -> str:
    """Get the tradingstrategy.ai website slug for a chain name."""
    slug = chain_name.lower()
    return _CHAIN_SLUG_OVERRIDES.get(slug, slug)


def _get_trading_strategy_chain_link(chain_name: str) -> str:
    """Get the tradingstrategy.ai vault listing URL for a chain."""
    chain_slug = _get_chain_slug(chain_name)
    return f"https://tradingstrategy.ai/trading-view/{chain_slug}/vaults"


def _get_trading_strategy_protocol_link(protocol_slug: str) -> str:
    """Get the tradingstrategy.ai vault listing URL for a protocol."""
    return f"https://tradingstrategy.ai/trading-view/vaults/protocols/{protocol_slug}"


def _get_trading_strategy_vault_link(
    chain_id: int,
    chain_name: str,
    protocol_slug: str,
    vault_slug: str,
    vault_address: str,
):
    chain_slug = _get_chain_slug(chain_name)
    return f"https://tradingstrategy.ai/trading-view/{chain_slug}/vaults/{vault_slug}?a={vault_address}"


def create_fee_label(
    fee_data: FeeData,
):
    """Create 2% / 20% style labels to display variosu kinds of vault fees.

    Order is: management / performance / deposit / withdrawal fees.
    """

    management_fee_annual = fee_data.management
    performance_fee: Percent = fee_data.performance
    deposit_fee: Percent = fee_data.deposit
    withdrawal_fee: Percent = fee_data.withdraw

    internalised_label = " (int.)" if fee_data.internalised else ""

    # All fees zero
    if management_fee_annual == 0 and performance_fee == 0 and deposit_fee == 0 and withdrawal_fee == 0:
        return "0% / 0%"

    if deposit_fee in (0, None) and withdrawal_fee in (0, None):
        return f"{fmt_one_decimal_or_int(management_fee_annual)} / {fmt_one_decimal_or_int(performance_fee)}{internalised_label}"

    return f"{fmt_one_decimal_or_int(management_fee_annual)} / {fmt_one_decimal_or_int(performance_fee)} / {fmt_one_decimal_or_int(deposit_fee)} / {fmt_one_decimal_or_int(withdrawal_fee)}{internalised_label}"


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
    """Replace values with abs(x) < eps by 0. Optionally clip negatives to 0.

    Keeps NaN as-is, turns +/- inf into NaN.
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
    sample_count: int | None = None,
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

    :param sample_count:
        If we have not enough returns data, do not try to calculate profit.

    :return:
        Net profit as a floating point (0.10 = 10% profit).
    """
    assert isinstance(start, datetime.datetime), f"start must be datetime, got {type(start)}"
    assert isinstance(end, datetime.datetime), f"end must be datetime, got {type(end)}"

    assert end >= start, f"End datetime must be after start datetime: {start} - {end}"

    if start == end:
        # Only 1 sample
        return 0

    if share_price_start == 0:
        # Some broken vaults give zero share price periods
        return 0

    # Min 2 day
    if sample_count is not None and sample_count < 2:
        return 0

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


def slugify_vaults(vaults: dict[VaultSpec, VaultRow]) -> list[VaultRow] | None:
    """Create slugs for a set of vaults.

    - Always give the primary slug to the vault that was created first.
    - Mutates VaultRow data in-place

    :param vaults:
        The vault metadata entries.

    """
    used_slugs: set[str] = set()

    def _get_creation_date(row: VaultRow) -> datetime.datetime:
        _detection_data = row.get("_detection_data")
        return _detection_data.first_seen_at

    def _slugify(vault_metadata) -> VaultRow:
        existing_slug = vault_metadata.get("vault_slug")
        if existing_slug:
            return

        name = vault_metadata.get("Name")
        share_token = vault_metadata.get("Share token")
        vault_address = vault_metadata["Address"]

        vault_slug = slugify_vault(
            name=name,
            symbol=share_token,
            address=vault_address,
            existing_slugs=used_slugs,
        )

        protocol_slug = slugify_protocol(vault_metadata["Protocol"])

        vault_metadata["vault_slug"] = vault_slug
        vault_metadata["protocol_slug"] = protocol_slug

        used_slugs.add(vault_slug)
        return vault_metadata

    first_vault = next(iter(vaults.values()))
    if "vault_slug" in first_vault:
        return None

    # Sort vaults by creation date
    # To always slugify in the same order
    vaults = sorted(vaults.values(), key=_get_creation_date)

    result = list(map(_slugify, vaults))
    return result


def _unnullify(x: str | None, default: str = "<unknown>") -> str:
    """Make sure we do not pass null value on human readable names beyond this point."""
    if x is None or pd.isna(x):
        return default
    return x


def calculate_period_metrics(
    period: Period,
    gross_fee_data: FeeData,
    net_fee_data: FeeData,
    share_price_hourly: pd.Series,
    share_price_daily: pd.Series,
    tvl: pd.Series,
    now_: pd.Timestamp,
) -> PeriodMetrics:
    """Calculate metrics for one period.

    :param period:
        Period identifier (1W, 1M, 3M, 6M, 1Y, lifetime)

    :param gross_fee_data:
        Fee data before fee mode adjustments

    :param net_fee_data:
        Fee data after fee mode adjustments (for net return calculations)

    :param share_price_hourly:
        Hourly share price series with DatetimeIndex

    :param share_price_daily:
        Daily share price series with DatetimeIndex

    :param tvl:
        Total value locked series with DatetimeIndex

    :param now_:
        The reference timestamp (usually the last timestamp in the data)

    :return:
        PeriodMetrics dataclass with calculated metrics
    """
    period_duration, period_tolerance = LOOKBACK_AND_TOLERANCES[period]

    if period == "lifetime":
        period_start_at = share_price_hourly.index[0]
        period_end_at = now_
    else:
        period_start_at = now_ - period_duration
        period_end_at = now_

    # Find the nearest available sample at or before period_start_at
    samples_start_at = share_price_hourly.index.asof(period_start_at)

    # Handle case where no sample exists at or before period_start_at
    if pd.isna(samples_start_at):
        # Fall back to the first available sample
        samples_start_at = share_price_hourly.index[0]

    period_samples_hourly = share_price_hourly.loc[samples_start_at:]

    if len(period_samples_hourly) == 0:
        return PeriodMetrics(period=period, raw_samples=0, period_start_at=period_start_at, period_end_at=period_end_at, error_reason="Period did not contain any samples")

    samples_end_at = period_samples_hourly.index[-1]
    raw_samples = len(period_samples_hourly)

    if len(period_samples_hourly) == 1:
        return PeriodMetrics(
            period=period,
            raw_samples=raw_samples,
            period_start_at=period_start_at,
            period_end_at=period_end_at,
            error_reason="Period contained only one sample",
            samples_start_at=samples_start_at,
            samples_end_at=samples_end_at,
        )

    # Check if sample duration exceeds tolerance
    sample_duration = samples_end_at - samples_start_at
    if sample_duration > period_tolerance:
        return PeriodMetrics(
            period=period,
            raw_samples=raw_samples,
            period_start_at=period_start_at,
            period_end_at=period_end_at,
            samples_start_at=samples_start_at,
            samples_end_at=samples_end_at,
            error_reason=f"Sample duration {sample_duration} exceeds tolerance {period_tolerance}",
        )

    # Filter daily samples for the period
    # Use asof to find nearest daily sample at or before samples_start_at
    daily_start = share_price_daily.index.asof(samples_start_at)
    if pd.isna(daily_start):
        return PeriodMetrics(
            period=period,
            raw_samples=raw_samples,
            period_start_at=period_start_at,
            period_end_at=period_end_at,
            error_reason="No daily samples available at or before period start",
            samples_start_at=samples_start_at,
            samples_end_at=samples_end_at,
        )

    period_samples_daily = share_price_daily.loc[daily_start:samples_end_at]
    daily_samples = len(period_samples_daily)

    # Extract start and end share prices
    share_price_start = period_samples_hourly.iloc[0]
    share_price_end = period_samples_hourly.iloc[-1]

    # Calculate gross returns
    if share_price_start == 0:
        returns_gross = 0
    else:
        returns_gross = (share_price_end / share_price_start) - 1

    # Calculate net returns using calculate_net_profit()
    returns_net = calculate_net_profit(
        start=samples_start_at,
        end=samples_end_at,
        share_price_start=share_price_start,
        share_price_end=share_price_end,
        management_fee_annual=net_fee_data.management,
        performance_fee=net_fee_data.performance,
        deposit_fee=net_fee_data.deposit,
        withdrawal_fee=net_fee_data.withdraw,
        sample_count=raw_samples,
    )

    # Calculate CAGR (gross and net)
    # CAGR formula: (1 + return) ^ (1/years) - 1
    years = sample_duration.days / 365.25
    base_gross = 1 + returns_gross
    base_net = 1 + returns_net

    if base_gross < 0 or base_net < 0:
        return PeriodMetrics(
            period=period,
            raw_samples=raw_samples,
            period_start_at=period_start_at,
            period_end_at=period_end_at,
            error_reason=f"Gross base ({base_gross}) or net base ({base_net}) negative, cannot compute CAGR ",
            samples_start_at=samples_start_at,
            samples_end_at=samples_end_at,
        )

    # Too short period
    if years < 3 / 365:
        return PeriodMetrics(
            period=period,
            raw_samples=raw_samples,
            period_start_at=period_start_at,
            period_end_at=period_end_at,
            error_reason=f"Period too short, days={sample_duration.days}, years={years:.4f}, to calculate metrics",
            samples_start_at=samples_start_at,
            samples_end_at=samples_end_at,
        )

    cagr_gross = base_gross ** (1 / years) - 1
    cagr_net = base_net ** (1 / years) - 1

    # Cap CAGR at a reasonable maximum.
    # Short-lived vaults (e.g. 14 days with 600% return) extrapolate to
    # absurd annual rates via (1+r)^(365/days). A 10,000% (100x) annual cap
    # is generous enough for any legitimate vault while preventing
    # astronomical numbers from polluting rankings.
    max_cagr = 100.0  # 10,000%
    cagr_gross = min(cagr_gross, max_cagr)
    cagr_net = min(cagr_net, max_cagr)

    # Calculate daily returns for volatility and max drawdown
    # Filter to only numeric values, drop NaN and infinite values
    daily_returns = period_samples_daily.pct_change(fill_method=None).dropna()
    # Ensure numeric dtype and filter out inf values to avoid std() errors
    daily_returns = pd.to_numeric(daily_returns, errors="coerce")
    daily_returns = daily_returns[np.isfinite(daily_returns)].dropna()

    # Calculate volatility (annualized from daily)
    if len(daily_returns) >= 2:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            volatility = daily_returns.std() * np.sqrt(365)
            if not np.isfinite(volatility):
                volatility = 0
    else:
        volatility = 0

    # Calculate Sharpe ratio using hourly returns
    hourly_returns = period_samples_hourly.pct_change(fill_method=None).dropna()
    # Ensure numeric dtype and filter out inf values
    hourly_returns = pd.to_numeric(hourly_returns, errors="coerce")
    hourly_returns = hourly_returns[np.isfinite(hourly_returns)].dropna()
    sharpe = calculate_sharpe_ratio_from_returns(hourly_returns)
    if not np.isfinite(sharpe):
        sharpe = 0

    # Calculate max drawdown
    if len(daily_returns) >= 2:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            wealth = (1 + daily_returns).cumprod()
            running_max = wealth.cummax()
            drawdown = (wealth - running_max) / running_max
            max_drawdown = drawdown.min()  # Most negative value
            if not np.isfinite(max_drawdown):
                max_drawdown = 0
    else:
        max_drawdown = 0

    # Extract TVL metrics
    period_tvl = tvl.loc[samples_start_at:samples_end_at]
    if len(period_tvl) > 0:
        tvl_start = period_tvl.iloc[0]
        tvl_end = period_tvl.iloc[-1]
        tvl_low = period_tvl.min()
        tvl_high = period_tvl.max()
    else:
        tvl_start = tvl_end = tvl_low = tvl_high = 0

    return PeriodMetrics(
        period=period,
        error_reason=None,
        period_start_at=period_start_at,
        period_end_at=period_end_at,
        share_price_start=share_price_start,
        share_price_end=share_price_end,
        raw_samples=raw_samples,
        samples_start_at=samples_start_at,
        samples_end_at=samples_end_at,
        daily_samples=daily_samples,
        returns_gross=returns_gross,
        returns_net=returns_net,
        cagr_gross=cagr_gross,
        cagr_net=cagr_net,
        volatility=volatility,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        tvl_start=tvl_start,
        tvl_end=tvl_end,
        tvl_low=tvl_low,
        tvl_high=tvl_high,
    )


def apply_abnormal_value_checks(
    risk: VaultTechnicalRisk,
    notes: str,
    flags: set[VaultFlag],
    current_nav: USDollarAmount | None = None,
    current_share_price: float | None = None,
    three_months_volatility: Percent | None = None,
) -> tuple[VaultTechnicalRisk, str, set[VaultFlag]]:
    """Check for broken vaults by detecting abnormal metric values.

    Automatically blacklists vaults with unrealistic TVL, share price,
    or volatility. These thresholds catch broken smart contracts and
    low-TVL vaults that produce meaningless metrics.

    Called multiple times as more metrics become available
    (first with NAV/price, then again after 3M volatility is computed).

    :param risk:
        Current risk classification.

    :param notes:
        Current notes string.

    :param flags:
        Current vault flags set.

    :param current_nav:
        Current TVL in USD. Checked against :py:data:`MAX_VALID_NAV`.

    :param current_share_price:
        Current share price. Checked against :py:data:`MAX_VALID_SHARE_PRICE`.

    :param three_months_volatility:
        3-month annualised volatility. Checked against :py:data:`MAX_VALID_VOLATILITY`.
        Catches low-TVL Hyperliquid vaults with one or few trades that
        produce extreme volatility numbers.

    :return:
        Updated (risk, notes, flags) tuple.
    """

    def _ensure_mutable_flags(flags):
        # TODO: Hack to break somewhere reused empty set object
        # which gets shared across all vaults
        if not flags:
            return set()
        return flags

    if current_nav is not None and current_nav > MAX_VALID_NAV:
        risk = VaultTechnicalRisk.blacklisted
        notes = ABNORMAL_TVL
        flags = _ensure_mutable_flags(flags)
        flags.add(VaultFlag.abnormal_tvl)

    if current_share_price is not None and current_share_price > MAX_VALID_SHARE_PRICE:
        risk = VaultTechnicalRisk.blacklisted
        notes = ABNORMAL_SHARE_PRICE
        flags = _ensure_mutable_flags(flags)
        flags.add(VaultFlag.abnormal_share_price)

    if three_months_volatility is not None and three_months_volatility > MAX_VALID_VOLATILITY:
        risk = VaultTechnicalRisk.blacklisted
        notes = ABNORMAL_VOLATILITY
        flags = _ensure_mutable_flags(flags)
        flags.add(VaultFlag.abnormal_volatility)

    return risk, notes, flags


def calculate_vault_record(
    prices_df: pd.DataFrame,
    vault_metadata_rows: dict[VaultSpec, VaultRow],
    month_ago: pd.Timestamp,
    three_months_ago: pd.Timestamp,
    vault_id: str | None = None,
) -> pd.Series:
    """Process a single vault metadata + prices to calculate its full data.

    - Exported to frontend, everything

    :param prices_df:
        Price DataFrame for a single vault

    :param vault_metadata_rows:
        Dictionary of vault metadata keyed by VaultSpec

    :param month_ago:
        Timestamp for 1-month lookback

    :param three_months_ago:
        Timestamp for 3-month lookback

    :param vault_id:
        Vault ID string. If not provided, extracted from prices_df["id"].

    :return:
        Series with calculated metrics
    """
    # Extract the group name (id_val)
    id_val = vault_id if vault_id is not None else prices_df["id"].iloc[0]

    # Extract vault metadata
    vault_spec = VaultSpec.parse_string(id_val, separator="-")
    vault_metadata: VaultRow = vault_metadata_rows.get(vault_spec)

    assert vault_metadata, f"Vault metadata not found for {id_val}. This vault is present in price data, but not in metadata entries. We have {len(vault_metadata_rows)} metadata entries."

    name = _unnullify(vault_metadata.get("Name"), "<unnamed>")
    denomination = _unnullify(vault_metadata.get("Denomination"), "<broken>")
    share_token = _unnullify(vault_metadata.get("Share token"), "<broken>")
    normalised_denomination = normalise_token_symbol(denomination)
    denomination_slug = normalised_denomination.lower()

    max_nav = prices_df["total_assets"].max()
    current_nav = prices_df["total_assets"].iloc[-1]
    chain_id = prices_df["chain"].iloc[-1]

    fee_data: FeeData = vault_metadata.get("_fees")
    gross_fee_data = fee_data

    if fee_data is None:
        # Legacy, unit tests,etc.
        # _fees not in the exported pickle we use for testing
        fee_data = FeeData(
            fee_mode=VaultFeeMode.externalised,
            management=vault_metadata["Mgmt fee"],
            performance=vault_metadata["Perf fee"],
            deposit=vault_metadata.get("Deposit fee", 0),  # Rare: assume 0 if not explicitly set
            withdraw=vault_metadata.get("Withdrawal fee", 0),  # Rare: assume 0 if not explicitly set
        )

    fee_mode = fee_data.fee_mode
    net_fee_data = fee_data.get_net_fees()

    mgmt_fee = fee_data.management
    perf_fee = fee_data.performance
    deposit_fee = fee_data.deposit
    withdrawal_fee = fee_data.withdraw

    vault_address = vault_metadata["Address"]
    link = vault_metadata.get("Link")
    event_count = prices_df["event_count"].iloc[-1]
    protocol = vault_metadata["Protocol"]

    risk = vault_metadata.get("_risk") or get_vault_risk(protocol, vault_address)
    notes = get_notes(vault_address, chain_id=chain_id)

    flags = vault_metadata.get("_flags", set())

    current_share_price = prices_df.iloc[-1]["share_price"]
    risk, notes, flags = apply_abnormal_value_checks(
        risk=risk,
        notes=notes,
        flags=flags,
        current_nav=current_nav,
        current_share_price=current_share_price,
    )

    vault_slug = vault_metadata["vault_slug"]
    protocol_slug = vault_metadata["protocol_slug"]
    risk_numeric = risk.value if isinstance(risk, VaultTechnicalRisk) else None

    trading_strategy_link = _get_trading_strategy_vault_link(
        chain_id=chain_id,
        chain_name=get_chain_name(chain_id),
        protocol_slug=protocol_slug,
        vault_slug=vault_slug,
        vault_address=vault_address,
    )

    lockup = vault_metadata.get("_lockup", None)
    if pd.isna(lockup):
        # Clean up some legacy data
        lockup = None

    # Deposit/redemption status from vault scan
    deposit_closed_reason = vault_metadata.get("_deposit_closed_reason")
    redemption_closed_reason = vault_metadata.get("_redemption_closed_reason")
    deposit_next_open = vault_metadata.get("_deposit_next_open")
    redemption_next_open = vault_metadata.get("_redemption_next_open")

    # Lending statistics from vault scan metadata
    available_liquidity_metadata = vault_metadata.get("_available_liquidity")
    utilisation_metadata = vault_metadata.get("_utilisation")

    # Lending statistics from historical price data (latest values)
    available_liquidity = None
    utilisation = None
    if "available_liquidity" in prices_df.columns:
        last_liquidity = prices_df["available_liquidity"].iloc[-1]
        if pd.notna(last_liquidity):
            available_liquidity = float(last_liquidity)
    if "utilisation" in prices_df.columns:
        last_utilisation = prices_df["utilisation"].iloc[-1]
        if pd.notna(last_utilisation):
            utilisation = float(last_utilisation)

    # Fall back to metadata if historical data not available
    if available_liquidity is None and available_liquidity_metadata is not None:
        available_liquidity = float(available_liquidity_metadata)
    if utilisation is None and utilisation_metadata is not None:
        utilisation = float(utilisation_metadata)

    # Vault descriptions from offchain metadata (Euler, Lagoon, etc.)
    description = vault_metadata.get("_description")
    short_description = vault_metadata.get("_short_description")

    detection: ERC4262VaultDetection = vault_metadata["_detection_data"]
    features = sorted([f.name for f in detection.features])

    # Token addresses
    share_token_data = vault_metadata.get("_share_token")
    if isinstance(share_token_data, dict):
        share_token_address = share_token_data.get("address")
    else:
        share_token_address = None

    # Most ERC-4626 vaults are also the share token (ERC-20) contract.
    if not share_token_address:
        share_token_address = vault_metadata.get("Address") or detection.address

    denomination_token_data = vault_metadata.get("_denomination_token")
    denomination_token_address = denomination_token_data.get("address") if isinstance(denomination_token_data, dict) else None

    # Do we know fees for this vault
    known_fee = mgmt_fee is not None and perf_fee is not None

    # Ensure prices_df index is monotonic and clean
    prices_df = prices_df.loc[~prices_df.index.isna()].sort_index(kind="stable")

    # Calculate period metrics using the new structured approach
    # Resample share price once for all period calculations
    share_price_hourly = prices_df["share_price"]
    share_price_daily = share_price_hourly.resample("D").last()
    tvl_series = prices_df["total_assets"]
    now_ = prices_df.index.max()

    period_results = []
    for period in LOOKBACK_AND_TOLERANCES.keys():
        period_metric = calculate_period_metrics(
            period=period,
            gross_fee_data=gross_fee_data,
            net_fee_data=net_fee_data,
            share_price_hourly=share_price_hourly,
            share_price_daily=share_price_daily,
            tvl=tvl_series,
            now_=now_,
        )
        period_results.append(period_metric)

    # Extract period metrics for backward compatibility
    lifetime_pm = get_period_metrics(period_results, "lifetime")
    three_months_pm = get_period_metrics(period_results, "3M")
    one_month_pm = get_period_metrics(period_results, "1M")

    # Lifetime metrics
    lifetime_start_date = prices_df.index[0]
    lifetime_end_date = prices_df.index[-1]
    lifetime_samples = len(prices_df)
    age = (lifetime_end_date - lifetime_start_date).days / 365.25

    # Legacy: Lifetime metrics
    if lifetime_pm and lifetime_pm.error_reason is None:
        lifetime_return = lifetime_pm.returns_gross
        lifetime_return_net = lifetime_pm.returns_net if known_fee else None
        cagr = lifetime_pm.cagr_gross
        cagr_net = lifetime_pm.cagr_net if known_fee else None
    else:
        lifetime_return = 0
        lifetime_return_net = 0 if known_fee else None
        cagr = 0
        cagr_net = 0 if known_fee else None

    # Legacy: three months metrics
    if three_months_pm and three_months_pm.error_reason is None:
        three_month_returns = three_months_pm.returns_gross
        three_months_return_net = three_months_pm.returns_net if known_fee else None
        three_months_cagr = three_months_pm.cagr_gross
        three_months_cagr_net = three_months_pm.cagr_net if known_fee else None
        three_months_volatility = three_months_pm.volatility
        three_months_sharpe = three_months_pm.sharpe
        three_months_sharpe_net = three_months_pm.sharpe  # Same as gross for now
        three_months_start = three_months_pm.samples_start_at
        three_months_end = three_months_pm.samples_end_at
        three_months_samples = three_months_pm.raw_samples
    else:
        three_month_returns = 0
        three_months_return_net = 0 if known_fee else None
        three_months_cagr = 0
        three_months_cagr_net = 0 if known_fee else None
        three_months_volatility = 0
        three_months_sharpe = 0
        three_months_sharpe_net = 0
        three_months_start = None
        three_months_end = None
        three_months_samples = 0

    # Check abnormal volatility now that 3M metrics are available
    risk, notes, flags = apply_abnormal_value_checks(
        risk=risk,
        notes=notes,
        flags=flags,
        three_months_volatility=three_months_volatility,
    )

    # Legacy: One month metrics
    if one_month_pm and one_month_pm.error_reason is None:
        one_month_returns = one_month_pm.returns_gross
        one_month_returns_net = one_month_pm.returns_net if known_fee else None
        one_month_cagr = one_month_pm.cagr_gross
        one_month_cagr_net = one_month_pm.cagr_net if known_fee else None
        one_month_start = one_month_pm.samples_start_at
        one_month_end = one_month_pm.samples_end_at
        one_month_samples = one_month_pm.raw_samples
    else:
        one_month_returns = 0
        one_month_returns_net = 0 if known_fee else None
        one_month_cagr = 0
        one_month_cagr_net = 0 if known_fee else None
        one_month_start = None
        one_month_end = None
        one_month_samples = None

    fee_label = create_fee_label(fee_data)

    last_updated_at = prices_df.index.max()
    last_updated_block = prices_df.loc[last_updated_at]["block_number"]
    last_share_price = prices_df.iloc[-1]["share_price"]
    first_updated_at = prices_df.index.min()
    first_updated_block = prices_df.iloc[0]["block_number"]

    return pd.Series(
        {
            "name": name,
            "vault_slug": vault_slug,
            "protocol_slug": protocol_slug,
            "share_token_address": share_token_address,
            "denomination_token_address": denomination_token_address,
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
            "normalised_denomination": normalised_denomination,
            "denomination_slug": denomination_slug,
            "share_token": share_token,
            "chain": get_chain_name(chain_id),
            "peak_nav": max_nav,
            "current_nav": current_nav,
            "years": age,
            "mgmt_fee": mgmt_fee,
            "perf_fee": perf_fee,
            "deposit_fee": deposit_fee,
            "withdraw_fee": withdrawal_fee,
            "fee_mode": fee_mode,
            "fee_internalised": fee_mode.is_internalised() if fee_mode else None,
            "gross_fees": gross_fee_data,
            "net_fees": net_fee_data,
            "fee_label": fee_label,
            "lockup": lockup,
            "event_count": event_count,
            "protocol": protocol,
            "risk": risk,
            "risk_numeric": risk_numeric,
            "id": id_val,
            "start_date": lifetime_start_date,
            "end_date": lifetime_end_date,
            "address": vault_spec.vault_address,
            "chain_id": vault_spec.chain_id,
            "stablecoinish": is_stablecoin_like(denomination),
            "first_updated_at": first_updated_at,
            "first_updated_block": first_updated_block,
            "last_updated_at": last_updated_at,
            "last_updated_block": last_updated_block,
            "last_share_price": last_share_price,
            "features": features,
            "flags": flags,
            "notes": notes,
            "link": link,
            "trading_strategy_link": trading_strategy_link,
            # Debug and diagnostics for sparse data
            "one_month_start": one_month_start,
            "one_month_end": one_month_end,
            "one_month_samples": one_month_samples,
            "three_months_start": three_months_start,
            "three_months_end": three_months_end,
            "three_months_samples": three_months_samples,
            "lifetime_start": lifetime_start_date,
            "lifetime_end": lifetime_end_date,
            "lifetime_samples": lifetime_samples,
            # New structured period metrics
            "period_results": period_results,
            # Deposit/redemption status
            "deposit_closed_reason": deposit_closed_reason,
            "redemption_closed_reason": redemption_closed_reason,
            "deposit_next_open": deposit_next_open,
            "redemption_next_open": redemption_next_open,
            # Lending protocol statistics
            "available_liquidity": available_liquidity,
            "utilisation": utilisation,
            # Offchain vault descriptions (Euler, Lagoon, etc.)
            "description": description,
            "short_description": short_description,
        }
    )


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

    # Enable tqdm progress bar for pandas
    tqdm.pandas(desc="Calculating vault performance metrics")

    slugify_vaults(
        vaults=vaults_by_id,
    )

    # Use progress_apply instead of the for loop
    # Sort is needed for slug stability
    # We pass include_groups=False to avoid FutureWarning, and pass id via group.name
    def _apply_vault_record(group):
        return calculate_vault_record(group, vaults_by_id, month_ago, three_months_ago, vault_id=group.name)

    results_df = df.groupby("id", group_keys=False, sort=True).progress_apply(
        _apply_vault_record,
        include_groups=False,
    )

    # Reset index to convert the grouped results to a regular DataFrame
    results_df = results_df.reset_index(drop=True)

    # Add ranking columns
    results_df = calculate_vault_rankings(results_df)

    return results_df


def get_period_metrics(period_results: list[PeriodMetrics], period: Period) -> PeriodMetrics | None:
    """Get PeriodMetrics for a specific period from the results list.

    :param period_results:
        List of PeriodMetrics objects from a vault record

    :param period:
        The period to find (e.g., "1W", "1M", "3M", "6M", "1Y", "lifetime")

    :return:
        The matching PeriodMetrics or None if not found
    """
    for pm in period_results:
        if pm.period == period:
            return pm
    return None


def calculate_vault_rankings(
    results_df: pd.DataFrame,
    min_tvl_chain_protocol: float = 10_000,
    min_tvl_overall: float = 50_000,
) -> pd.DataFrame:
    """Calculate rankings for all periods inside PeriodMetrics objects.

    Updates PeriodMetrics objects in-place within the period_results lists.
    Rankings are calculated for all 6 periods (1W, 1M, 3M, 6M, 1Y, lifetime).

    Vaults are excluded from rankings if:
    - They have no CAGR data (zero or NaN)
    - They have an error_reason set
    - They are blacklisted (risk == VaultTechnicalRisk.blacklisted)
    - Their period TVL is below the threshold

    :param results_df:
        DataFrame from calculate_lifetime_metrics()

    :param min_tvl_chain_protocol:
        Minimum TVL required for chain and protocol rankings (default: $10,000)

    :param min_tvl_overall:
        Minimum TVL required for overall rankings (default: $50,000)

    :return:
        DataFrame with rankings updated in PeriodMetrics objects
    """
    periods: list[Period] = list(LOOKBACK_AND_TOLERANCES.keys())

    for period in periods:
        # Build Series of CAGR values for this period with different TVL thresholds
        cagr_values_chain_protocol = []
        cagr_values_overall = []

        for idx in results_df.index:
            row = results_df.loc[idx]
            period_results = row["period_results"]
            pm = get_period_metrics(period_results, period)

            if pm is None or pm.error_reason is not None:
                cagr_values_chain_protocol.append(pd.NA)
                cagr_values_overall.append(pd.NA)
                continue

            # Use net CAGR, fall back to gross
            cagr = pm.cagr_net if pm.cagr_net is not None else pm.cagr_gross

            # Apply exclusion criteria (common checks)
            is_blacklisted = row["risk"] == VaultTechnicalRisk.blacklisted
            tvl = pm.tvl_end or 0
            has_no_cagr = cagr is None or cagr == 0 or pd.isna(cagr)

            # Chain/protocol rankings use lower TVL threshold
            has_low_tvl_chain_protocol = tvl < min_tvl_chain_protocol
            if is_blacklisted or has_low_tvl_chain_protocol or has_no_cagr:
                cagr_values_chain_protocol.append(pd.NA)
            else:
                cagr_values_chain_protocol.append(cagr)

            # Overall rankings use higher TVL threshold
            has_low_tvl_overall = tvl < min_tvl_overall
            if is_blacklisted or has_low_tvl_overall or has_no_cagr:
                cagr_values_overall.append(pd.NA)
            else:
                cagr_values_overall.append(cagr)

        cagr_series_chain_protocol = pd.Series(cagr_values_chain_protocol, index=results_df.index)
        cagr_series_overall = pd.Series(cagr_values_overall, index=results_df.index)

        # Calculate rankings with different series
        overall_ranks = cagr_series_overall.rank(method="min", ascending=False, na_option="keep")
        chain_ranks = cagr_series_chain_protocol.groupby(results_df["chain"]).rank(method="min", ascending=False, na_option="keep")
        protocol_ranks = cagr_series_chain_protocol.groupby(results_df["protocol_slug"]).rank(method="min", ascending=False, na_option="keep")

        # Update PeriodMetrics objects in-place
        for idx in results_df.index:
            pm = get_period_metrics(results_df.loc[idx, "period_results"], period)
            if pm is not None:
                pm.ranking_overall = int(overall_ranks[idx]) if pd.notna(overall_ranks[idx]) else None
                pm.ranking_chain = int(chain_ranks[idx]) if pd.notna(chain_ranks[idx]) else None
                pm.ranking_protocol = int(protocol_ranks[idx]) if pd.notna(protocol_ranks[idx]) else None

    return results_df


#: Protocol slugs for vaults where we do not necessarily have
#: on-chain deposit/redeem event data.
#:
#: These vaults get their data from off-chain APIs (e.g. GRVT, Hyperliquid)
#: or are hardcoded protocol entries that may lack standard ERC-4626 events.
SPECIAL_VAULT_PROTOCOL_SLUGS = {"grvt", "hyperliquid"}


def is_special_vault(
    protocol_slug: str,
    vault_address: str,
) -> bool:
    """Check if a vault is a special vault that may not have deposit/redeem event data.

    - GRVT and Hyperliquid vaults get data from off-chain APIs
    - Hardcoded protocol vaults may lack standard ERC-4626 deposit/redeem events

    :param protocol_slug:
        Protocol slug (e.g. "grvt", "hyperliquid", "morpho")

    :param vault_address:
        Vault contract address

    :return:
        True if this vault should bypass the minimum event count filter
    """
    if protocol_slug in SPECIAL_VAULT_PROTOCOL_SLUGS:
        return True
    if vault_address.lower() in HARDCODED_PROTOCOLS:
        return True
    return False


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

    # Filter out some vaults that have not seen many deposit and redemptions.
    # Special vaults (GRVT, Hyperliquid, hardcoded protocols) are exempt
    # because we do not necessarily have on-chain deposit/redeem event data for them.
    special_mask = lifetime_data_df.apply(
        lambda row: is_special_vault(row["protocol_slug"], row["address"]),
        axis=1,
    )
    broken_mask = (lifetime_data_df["event_count"] < min_events) & ~special_mask
    logger(f"Vault entries with too few deposit and redeem events (min {min_events}) filtered out: {len(lifetime_data_df[broken_mask])}")
    lifetime_data_df = lifetime_data_df[~broken_mask]
    return lifetime_data_df


def combine_return_columns(
    gross: pd.Series,
    net: pd.Series,
    new_line=" ",
    mode: Literal["percent", "usd"] = "percent",
    profit_presentation: Literal["split", "net_only"] = "split",
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
        match profit_presentation:
            case "split":
                if n is not None and pd.isna(n) == False:
                    return f"{n:.1%}{new_line}({g:.1%})"
                else:
                    return f"---{new_line}({g:.1%})"
            case "net_only":
                if n is not None and pd.isna(n) == False:
                    return f"{n:.1%} (n)"
                else:
                    if g and pd.isna(g) == False:
                        return f"{g:.1%} (g)"
                    else:
                        return "---"

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
    add_share_token=False,
    drop_blacklisted=True,
    profit_presentation: Literal["split", "net_only"] = "split",
    html_links=False,
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

    :param html_links:
        Wrap Name, Chain, and Protocol values in ``<a>`` tags
        linking to `tradingstrategy.ai <https://tradingstrategy.ai>`__.
        Use :py:func:`display_lifetime_table` to render the result
        with compact styling in a Jupyter notebook.

        Example::

            from eth_defi.research.vault_metrics import (
                format_lifetime_table,
                display_lifetime_table,
            )

            formatted = format_lifetime_table(df, html_links=True)
            display_lifetime_table(formatted)

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
        profit_presentation=profit_presentation,
    )

    df["lifetime_return"] = combine_return_columns(
        gross=df["lifetime_return"],
        net=df["lifetime_return_net"],
        profit_presentation=profit_presentation,
    )

    df["three_months_cagr"] = combine_return_columns(
        gross=df["three_months_cagr"],
        net=df["three_months_cagr_net"],
        profit_presentation=profit_presentation,
    )

    # df["three_months_returns"] = combine_return_columns(
    #    gross=df["three_months_returns"],
    #    net=df["three_months_returns_net"],
    # )

    df["one_month_cagr"] = combine_return_columns(
        gross=df["one_month_cagr"],
        net=df["one_month_cagr_net"],
        profit_presentation=profit_presentation,
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

    def _str_enum_set(v: set[VaultFlag] | list[VaultFlag] | None) -> str:
        if v is None or pd.isna(v):
            return ""
        return ", ".join(str(val) for val in v)

    df["three_months_volatility"] = df["three_months_volatility"].apply(lambda x: f"{x:.1%}")
    df["three_months_sharpe"] = df["three_months_sharpe"].apply(lambda x: f"{x:.1f}")
    df["event_count"] = df["event_count"].apply(lambda x: f"{x:,}")
    df["risk"] = df["risk"].apply(lambda x: x.get_risk_level_name() if x is not None else "Unknown")
    df["lockup"] = df["lockup"].apply(lambda x: f"{x.days}" if pd.notna(x) else "---")
    df["flags"] = df["flags"].apply(_str_enum_set)

    # Format lending protocol statistics
    df["utilisation"] = df["utilisation"].apply(lambda x: f"{x:.1%}" if pd.notna(x) else "---")
    df["available_liquidity"] = df["available_liquidity"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "---")

    # Optionally wrap name, chain, and protocol in <a> links.
    # Must be done before the columns they depend on are deleted below.
    if html_links:
        if "trading_strategy_link" in df.columns:
            df["name"] = df.apply(
                lambda row: f'<a href="{row["trading_strategy_link"]}">{row["name"]}</a>',
                axis=1,
            )
        if "chain" in df.columns:
            df["chain"] = df["chain"].apply(
                lambda name: f'<a href="{_get_trading_strategy_chain_link(name)}">{name}</a>',
            )
        if "protocol" in df.columns and "protocol_slug" in df.columns:
            df["protocol"] = df.apply(
                lambda row: f'<a href="{_get_trading_strategy_protocol_link(row["protocol_slug"])}">{row["protocol"]}</a>',
                axis=1,
            )

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
    _del("features")
    _del("fee_mode")
    _del("fee_internalised")
    _del("gross_fees")
    _del("net_fees")
    _del("index")

    # Time range diagnostics variables
    _del("one_month_start")
    _del("one_month_end")
    _del("one_month_samples")
    _del("three_months_start")
    _del("three_months_end")
    _del("three_months_samples")
    _del("lifetime_start")
    _del("lifetime_end")
    _del("lifetime_samples")
    _del("vault_slug")
    _del("protocol_slug")

    _del("normalised_denomination")
    _del("denomination_slug")

    _del("first_updated_at")
    _del("first_updated_block")
    _del("last_updated_block")
    _del("last_share_price")

    # Addresses
    _del("share_token_address")
    _del("denomination_token_address")

    _del("link")

    # New structured period metrics (not for human-readable table)
    _del("period_results")

    # Ranking columns (not for human-readable table)
    _del("ranking_overall_3m")
    _del("ranking_chain_3m")
    _del("ranking_protocol_3m")

    # Lending protocol statistics are kept in the table (formatted above)

    # Offchain descriptions are exported via JSON API, not human-readable table
    _del("description")
    _del("short_description")

    if not add_share_token:
        _del("share_token")
    else:
        df = df.rename(columns={"share_token": "Share token"})

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
            "flags": "Flags",
            "notes": "Notes",
            "id": "id",
            "trading_strategy_link": "Link",
            "deposit_closed_reason": "Deposit closed reason",
            "redemption_closed_reason": "Redemption closed reason",
            "deposit_next_open": "Deposit next open",
            "redemption_next_open": "Redemption next open",
            "available_liquidity": "Available liquidity",
            "utilisation": "Utilisation",
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


def display_lifetime_table(df: pd.DataFrame):
    """Render a formatted lifetime table as compact HTML in a Jupyter notebook.

    Produces an HTML table with minimal cell padding and renders
    ``<a>`` links created by :py:func:`format_lifetime_table` with
    ``html_links=True``.

    Example::

        formatted = format_lifetime_table(df, html_links=True)
        display_lifetime_table(formatted)

    :param df:
        DataFrame returned by :py:func:`format_lifetime_table`.
    """
    from IPython.display import display, HTML

    style = "<style>table.lifetime-table td, table.lifetime-table th { padding: 2px 6px; white-space: nowrap; }</style>"
    table_html = df.to_html(escape=False, classes="lifetime-table")
    display(HTML(style + table_html))


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

    vault_df = forward_fill_vault(vault_df)

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
    # cumulative_returns = (1 + hourly_returns).cumprod()

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

    # Filter out some vaults that have not seen many deposit and redemptions.
    # Special vaults (GRVT, Hyperliquid, hardcoded protocols) are exempt
    # because we do not necessarily have on-chain deposit/redeem event data for them.
    special_mask = lifetime_data_df.apply(
        lambda row: is_special_vault(row["protocol_slug"], row["address"]),
        axis=1,
    )
    broken_mask = (lifetime_data_df["event_count"] < min_events) & ~special_mask
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


#: Group headings for FFN performance stats, matching the None
#: separators in ``PerformanceStats._stats()``.
FFN_GROUP_HEADINGS = [
    "Overview",
    "Returns",
    "Period returns",
    "Daily statistics",
    "Monthly statistics",
    "Yearly statistics",
    "Drawdown statistics",
]


def format_ffn_performance_stats_grouped(
    report: PerformanceStats,
    prefix_series: pd.Series | None = None,
) -> list[tuple[str, pd.Series]]:
    """Format FFN report as logically grouped sections.

    Returns a list of ``(heading, series)`` tuples where each tuple
    represents a group of related metrics. The groups correspond to
    the ``None`` separators in FFN's ``PerformanceStats._stats()``.

    :param report:
        FFN performance report to format.

    :param prefix_series:
        Extra header data to prepend to the first group.

    :return:
        List of ``(heading, series)`` tuples for each logical group.
    """
    assert isinstance(report, PerformanceStats), f"report must be an instance of PerformanceStats, got {type(report)}"

    stat_definitions = report._stats()

    def _format(k, f, raw):
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

    # Split stats into groups at None boundaries
    groups = []
    current_keys = []
    current_values = []

    for key, name, typ in stat_definitions:
        if not name:
            # None separator = group boundary
            if current_keys:
                groups.append(pd.Series(current_values, index=current_keys))
                current_keys = []
                current_values = []
            continue
        current_keys.append(name)
        raw = getattr(report, key, "")
        current_values.append(_format(key, typ, raw))

    # Don't forget the last group (no trailing None)
    if current_keys:
        groups.append(pd.Series(current_values, index=current_keys))

    # Prepend prefix_series to first group
    if prefix_series is not None and len(groups) > 0:
        groups[0] = pd.concat([prefix_series, groups[0]])

    # Pair groups with headings
    result = []
    for i, group_series in enumerate(groups):
        heading = FFN_GROUP_HEADINGS[i] if i < len(FFN_GROUP_HEADINGS) else f"Group {i + 1}"
        result.append((heading, group_series))

    return result


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

    from IPython.display import HTML, display

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

    # Display FFN stats as grouped sections
    performance_stats = vault_report.performance_stats
    if performance_stats is not None:
        grouped = format_ffn_performance_stats_grouped(performance_stats)
        grouped_html = format_grouped_series_as_multi_column_grid(grouped)
        if render:
            display(HTML(grouped_html))
    else:
        if render:
            print(f"Vault {vault_spec.chain_id}-{vault_spec.vault_address}: performance metrics not available, is quantstats library installed?")


def export_lifetime_row(row: pd.Series) -> dict:
    """Export lifetime metrics row to a fully JSON-serializable dict.

    - Recursively handles nested dicts, lists, tuples, sets, and dataclasses.
    - Normalizes pandas, numpy, datetime, and custom types.
    - Preserves legacy fee field names.
    """

    def _serialize(value):
        # Check for NaT first, before any isinstance checks
        # (pd.NaT can match isinstance checks for datetime/Timestamp)
        if value is pd.NaT:
            return None
        # Numpy scalar
        if isinstance(value, (np.floating, np.integer)):
            return value.item()
        # Pandas timestamp
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        # Datetime (naive or aware)
        if isinstance(value, datetime.datetime):
            return value.isoformat()
        # Timedelta types
        if isinstance(value, (pd.Timedelta, datetime.timedelta)):
            return value.total_seconds()
        # Custom enum-like risk object
        if isinstance(value, VaultTechnicalRisk):
            return value.get_risk_level_name()
        # Dataclass -> dict then recurse
        if is_dataclass(value):
            return {k: _serialize(v) for k, v in asdict(value).items()}
        # Mapping types
        if isinstance(value, dict):
            return {str(k): _serialize(v) for k, v in value.items()}
        # Sequence / set types (exclude strings/bytes)
        if isinstance(value, (list, tuple, set)):
            return [_serialize(v) for v in value]
        # Pandas Series/DataFrame: convert to dict or list
        if isinstance(value, pd.Series):
            return _serialize(value.to_dict())
        if isinstance(value, pd.DataFrame):
            return [_serialize(rec) for rec in value.to_dict(orient="records")]
        if isinstance(value, Enum):
            return value.value

        # Na-like scalar (NaN, None, etc.)
        if pd.isna(value):
            return None

        if isinstance(value, float):
            if math.isinf(value) or math.isnan(value):
                # JSON cannot handle inf
                return None

        return value

    out = {k: _serialize(v) for k, v in row.to_dict().items()}

    # Legacy field mappings
    out["management_fee"] = out.get("mgmt_fee")
    out["performance_fee"] = out.get("perf_fee")

    # Legacy compatibility: if mgmt fee missing, nullify deposit/withdraw fees
    if out.get("mgmt_fee") is None:
        out["deposit_fee"] = None
        out["withdraw_fee"] = None

    return out
