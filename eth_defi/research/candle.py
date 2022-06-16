"""Create OHLCV candle diagrams.

Allows analysing of cryptocurrency price data in notebooks.
"""

from typing import Optional

import pandas as pd


def convert_to_ohlc_candles(
        df: pd.DataFrame,
        time_bucket: pd.Timedelta = pd.Timedelta("1D"),
        price_column: str="price",
        timestamp_index_column: Optional[str]="timestamp",
) -> pd.DataFrame:
    """Create OHLCV candles based on raw trade events.

    :param timestamp_index_column:
        If given then convert this timestamp column to an index.
        It can contain ISO8601 string timestamp, or be a timestamp column.
    """


    # https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.resample.html
    # https://pandas.pydata.org/docs/reference/api/pandas.core.resample.Resampler.ohlc.html
    # https://blog.quantinsti.com/tick-tick-ohlc-data-pandas-tutorial/
    # https://pandas.pydata.org/docs/reference/api/pandas.Timedelta.html
    # https://stackoverflow.com/questions/47365575/pandas-resampling-hourly-ohlc-to-daily-ohlc

    if timestamp_index_column:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index(timestamp_index_column, drop=False)

    candles = df[price_column].resample(time_bucket).ohlc(_method='ohlc')
    return candles
