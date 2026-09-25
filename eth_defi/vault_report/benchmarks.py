"""US Treasury bill benchmark for the monthly vault report.

Readers compare stablecoin vault returns against the risk-free rate, so return
charts show the three-month US Treasury bill as a benchmark, like the vault
pages on the website.

Data comes from the `FRED DTB3 series <https://fred.stlouisfed.org/series/DTB3>`__:
the daily 3-month Treasury bill secondary market rate. The CSV export needs no
API key. FRED quotes bills on a bank-discount basis; the report converts them to
a bond-equivalent (investment) yield, which is comparable to an annualised vault
return. See the `Treasury bill rate definitions <https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rates-frequently-asked-questions>`__.

The benchmark is optional: a FRED outage leaves it out of the charts.
"""

import datetime
import io
import logging
from pathlib import Path

import pandas as pd

from eth_defi.types import Percent
from eth_defi.vault_report.data import fetch_file

logger = logging.getLogger(__name__)

#: FRED CSV export of the daily 3-month Treasury bill discount rate
DTB3_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"

#: Days to maturity of a 13-week bill, used in the bond-equivalent yield conversion
BILL_DAYS_TO_MATURITY = 91


def convert_discount_to_investment_yield(discount_rate: pd.Series) -> pd.Series:
    """Convert Treasury bill bank-discount rates to bond-equivalent yields.

    ``yield = 365 × d / (360 − days × d)`` for bills with up to half a year to maturity.

    :param discount_rate:
        Discount rates as fractions, 0.04 = 4%.

    :return:
        Bond-equivalent yields as fractions.
    """
    return 365 * discount_rate / (360 - BILL_DAYS_TO_MATURITY * discount_rate)


def fetch_treasury_bill_yields(cache_dir: Path, max_age: datetime.timedelta = datetime.timedelta(days=1)) -> pd.Series | None:
    """Fetch daily 3-month Treasury bill investment yields.

    Downloads the FRED DTB3 CSV at most once per ``max_age``. On a download or
    parse failure, falls back to an older cached copy, or returns ``None``.

    :param cache_dir:
        Download cache directory.

    :param max_age:
        Re-download the CSV when the cached copy is older than this.

    :return:
        Daily yields as fractions (0.04 = 4%), indexed by naive UTC date, or
        ``None`` when no data is available.
    """
    path = cache_dir / "fred-dtb3.csv"
    try:
        fetch_file(DTB3_CSV_URL, path, max_age=max_age, timeout=60)
    except RuntimeError as e:
        logger.warning("Could not download US Treasury bill rates, using cached data if available: %s", e)

    if not path.exists():
        return None

    try:
        df = pd.read_csv(io.StringIO(path.read_text()), na_values=["."])
        rates = df.set_index(pd.to_datetime(df.iloc[:, 0]))[df.columns[1]].dropna() / 100
    except (ValueError, KeyError, IndexError) as e:
        logger.warning("Could not parse US Treasury bill rates in %s: %s", path, e)
        return None

    if rates.empty:
        return None
    return convert_discount_to_investment_yield(rates)


def calculate_treasury_bill_rolling_returns(yields: pd.Series, window: datetime.timedelta, index: pd.DatetimeIndex) -> pd.Series:
    """Calculate the return of rolling three-month Treasury bill holdings.

    Accrues each calendar day at the day's yield (``1 + y / 365``), forward
    filling weekends and holidays, which matches how vault share prices accrue.

    :param yields:
        Output of :py:func:`fetch_treasury_bill_yields`.

    :param window:
        Rolling window, the same as the vault rolling returns.

    :param index:
        Daily index of the vault rolling returns chart.

    :return:
        Rolling returns in percent on ``index``.
    """
    daily = yields.resample("D").last().ffill().reindex(pd.date_range(yields.index.min(), index.max(), freq="D")).ffill()
    growth = (1 + daily / 365).cumprod()
    rolling = (growth / growth.shift(window.days) - 1) * 100
    return rolling.reindex(index)


def get_latest_yield(yields: pd.Series) -> Percent:
    """Get the most recent Treasury bill yield.

    :param yields:
        Output of :py:func:`fetch_treasury_bill_yields`.

    :return:
        Latest yield as a fraction.
    """
    return float(yields.iloc[-1])
