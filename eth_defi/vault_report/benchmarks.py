"""US Treasury bill benchmark for the monthly vault report.

Readers compare stablecoin vault returns against the risk-free rate, so return
charts show the three-month US Treasury bill as a benchmark, like the vault
pages on the website.

Data comes from the `FRED DGS3MO series <https://fred.stlouisfed.org/series/DGS3MO>`__:
the daily 3-month Treasury constant maturity yield, quoted on an investment
basis and therefore directly comparable to an annualised vault return. The
website's vault pages use the same series (``src/lib/top-vaults/treasury-benchmark.ts``
in the frontend), so the blog and the website show the same benchmark. The CSV
export needs no API key.

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

#: FRED series id of the 3-month Treasury constant maturity yield
TREASURY_SERIES_ID = "DGS3MO"

#: FRED CSV export of :py:data:`TREASURY_SERIES_ID`
TREASURY_CSV_URL = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={TREASURY_SERIES_ID}"


def fetch_treasury_bill_yields(cache_dir: Path, max_age: datetime.timedelta = datetime.timedelta(days=1)) -> pd.Series | None:
    """Fetch daily 3-month Treasury bill yields.

    Downloads the FRED :py:data:`TREASURY_SERIES_ID` CSV at most once per
    ``max_age``. On a download or parse failure, falls back to an older cached
    copy, or returns ``None``.

    :param cache_dir:
        Download cache directory.

    :param max_age:
        Re-download the CSV when the cached copy is older than this.

    :return:
        Daily yields as fractions (0.04 = 4%), indexed by naive UTC date, or
        ``None`` when no data is available.
    """
    path = cache_dir / f"fred-{TREASURY_SERIES_ID.lower()}.csv"
    try:
        fetch_file(TREASURY_CSV_URL, path, max_age=max_age, timeout=60)
    except RuntimeError as e:
        logger.warning("Could not download US Treasury bill rates, using cached data if available: %s", e)

    if not path.exists():
        return None

    try:
        df = pd.read_csv(io.StringIO(path.read_text()), na_values=["."])
        rates = df.set_index(pd.to_datetime(df.iloc[:, 0]))[TREASURY_SERIES_ID].dropna() / 100
    except (ValueError, KeyError, IndexError) as e:
        # A missing series column means FRED changed the export format or series
        logger.warning("Could not parse US Treasury bill rates in %s: %s", path, e)
        return None

    if rates.empty:
        return None
    return rates


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
