"""Create OHLCV candle charts.

Allows analysing of cryptocurrency price data in notebooks.
Create OHLCV charts out from :py:class:`pandas.DataFrame` price data.
"""

from typing import Optional

import pandas as pd


def convert_to_ohlcv_candles(
    df: pd.DataFrame,
    time_bucket: pd.Timedelta = pd.Timedelta("1D"),
    price_column: str = "price",
    value_column: str = "value",
    timestamp_index_column: Optional[str] = "timestamp",
) -> pd.DataFrame:
    """Create OHLCV candles based on raw trade events.

    Example:

    .. code-block:: python

        candles = convert_to_ohlcv_candles(df, time_bucket=pd.Timedelta("4h"))

    See :ref:`the full example in Uniswap v3 OHLCV notebook </tutorials/uniswap-v3-price-analysis.ipynb>`.

    :param df:
        Input data frame.

    :param time_bucket:
        What's the duration of a single candle.

    :param price_column:
        The dataframe column containing the price of a trade.
        Used to generate `open`, `high`, `low` and `close` columns.

    :param value_column:
        The dataframe column containing the price of a trade.
        Used to generate `volume` column.

    :param timestamp_index_column:
        If given then convert this timestamp column to an index.
        It can contain ISO8601 string timestamp, or be a timestamp column.

    :return:
        :py:class:`pd.DataFrame` with
        `open`, `high`, `low`, `close` and `volume` columns.
        Index is resampled timestamp.
    """

    # https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.resample.html
    # https://pandas.pydata.org/docs/reference/api/pandas.core.resample.Resampler.ohlc.html
    # https://blog.quantinsti.com/tick-tick-ohlc-data-pandas-tutorial/
    # https://pandas.pydata.org/docs/reference/api/pandas.Timedelta.html
    # https://stackoverflow.com/questions/47365575/pandas-resampling-hourly-ohlc-to-daily-ohlc

    if timestamp_index_column:
        df[timestamp_index_column] = pd.to_datetime(df[timestamp_index_column])
        df = df.set_index(timestamp_index_column, drop=False)

    candles = df[price_column].resample(time_bucket).ohlc(_method="ohlc")
    candles["volume"] = df[value_column].resample(time_bucket).sum()
    return candles
