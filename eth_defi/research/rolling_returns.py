"""Used in vault report notebooks to calculate and rolling returns for vaults."""

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.io as pio
from plotly.colors import qualitative
from plotly.graph_objects import Figure

from eth_defi.chain import get_chain_name

CHART_BENCHMARK_COUNT: int = 10

RETURNS_ROLLING_WINDOW = 30  # Bars, 1M

CHART_HISTORY = pd.Timedelta(days=30 * 5)  #


def wrap_legend_text(text: str, max_length: int = 30) -> str:
    """Wrap long legend text by inserting line breaks."""
    if len(text) <= max_length:
        return text

    words = text.split()
    lines = []
    current_line = []
    current_length = 0

    for word in words:
        # If adding this word would exceed the limit, start a new line
        if current_length + len(word) + len(current_line) > max_length and current_line:
            lines.append(" ".join(current_line))
            current_line = [word]
            current_length = len(word)
        else:
            current_line.append(word)
            current_length += len(word)

    # Add the last line
    if current_line:
        lines.append(" ".join(current_line))

    return "<br>".join(lines)


def visualise_rolling_returns(
    df: pd.DataFrame,
    title: str,
    legend_wrap_length: int = 30,
) -> Figure:
    assert isinstance(df, pd.DataFrame), "df must be a pandas DataFrame"
    assert isinstance(title, str), f"title must be a string: {title}"

    # Create a copy of the dataframe to avoid modifying the original
    df_plot = df.copy()

    # Wrap long legend names
    df_plot["name_wrapped"] = df_plot["name"].apply(lambda x: wrap_legend_text(x, legend_wrap_length))

    fig = px.line(
        df_plot,
        x="timestamp",
        y="rolling_1m_returns_annualized",
        color="name_wrapped",
        title=title,
        labels={"rolling_1m_returns": "1M rolling return annualised (%)", "timestamp": "Time", "name_wrapped": "Vault"},
        hover_data=["id"],
        color_discrete_sequence=qualitative.Dark24,
    )

    fig.update_layout(
        xaxis_title="Date",
        yaxis_title="1M rolling return annualised (%)",
        legend_title="Vaults",
        hovermode="closest",
        template=pio.templates.default,
        legend=dict(
            valign="top",  # Align legend items to top
            itemsizing="constant",  # Keep consistent item sizing
        ),
    )

    fig.update_traces(line=dict(width=4))
    return fig


def _calculate_rolling_returns_from_prices(
    price_series: pd.Series,
    rolling_period: pd.Timedelta = pd.Timedelta(days=30),
) -> pd.Series:
    """Calculate rolling returns from hourly share prices.

    :param price_series:
        Share price time series with a DatetimeIndex.

    :param rolling_period:
        The rolling window size for return calculation.
    """

    def _window_returns(window):
        if len(window) == 0:
            return np.nan
        return window.iloc[-1] / window.iloc[0] - 1

    windowed = price_series.rolling(
        window=rolling_period,
        min_periods=1,
    )
    rolling_returns = windowed.apply(_window_returns)

    rolling_returns_pct = rolling_returns * 100
    return rolling_returns_pct


def calculate_rolling_returns(
    returns_df: pd.DataFrame,
    interesting_vaults: pd.Series | None = None,
    filtered_vault_list_df: pd.DataFrame | None = None,
    history_length: pd.Timedelta = CHART_HISTORY,
    rolling_period: pd.Timedelta = pd.Timedelta(days=30),
    cap: float = None,
    clip_down: float = None,
    clip_up: float = None,
    drop_threshold: float = None,
    benchmark_count: int = CHART_BENCHMARK_COUNT,
    returns_column: str = "returns_1h",
    chainify=True,
    logger=print,
) -> pd.DataFrame:
    """Calculate rolling returns stats for vaults.

    - Take a snapshot of returns from the return data pool of all vaults
    - Calculate rolling return chart metrics for those vaults

    :param returns_df:
        Hourly cleaned return data of all vaults as a DataFrame.

        See notebook examples.

    :param interesting_vaults:
        A Series of vault ids to limit the results to.

        A list of chain id-address strings.

    :param history_length:
        How far back to include data in the result.

    :param rolling_period:
        The rolling window size for return calculation.

        Default is 30 days (1 month).

    :param chainify:
        Add the chain name in the title.

    :return:
        A DataFrame with MultiIndex(id, timestamp) and columns like rolling_1m_returns_annualized.
    """

    assert isinstance(returns_df.index, pd.DatetimeIndex)

    def _apply_chain_name(name: str, chain_id: int) -> str:
        if not isinstance(name, str) or not name:
            name = ""
        chain_name = get_chain_name(chain_id)
        if chain_name in name:
            return name
        return f"{name} ({chain_name})"

    # Add chain part of the name so it appears int he chart legend
    if chainify:
        returns_df = returns_df.copy()
        returns_df["name"] = returns_df.apply(
            lambda row: _apply_chain_name(row["name"], row["chain"]),
            axis=1,
        )

    # Pick N top vaults to show,
    # assume returns_df is sorted by wanted order
    if benchmark_count:
        assert isinstance(benchmark_count, int), "benchmark_count must be an integer"

        if interesting_vaults is None:
            assert isinstance(filtered_vault_list_df, pd.DataFrame), "filtered_vault_list_df must be a pandas DataFrame"
            interesting_vaults = filtered_vault_list_df[0:benchmark_count]["id"]

    # Limit to benchmarked vaults
    if interesting_vaults is not None:
        # Limit to the vaults on interesting vaults list
        df = returns_df[returns_df["id"].isin(interesting_vaults)]
    else:
        # All vaults
        df = returns_df

    def _calc_returns(df):
        # Calculate rolling returns

        df["rolling_1m_returns"] = df["share_price"].transform(
            _calculate_rolling_returns_from_prices,
            rolling_period=rolling_period,
        )

        # df["rolling_1m_returns_annualized"] = ((1 + df["rolling_1m_returns"] / 100) ** 12 - 1) * 100
        return df

    df = df.groupby("id").apply(_calc_returns, include_groups=False)
    df = df.reset_index()

    if len(df) == 0:
        return df

    # When vault launches it has usually near-infinite APY
    # Cap it here so charts are readable
    if cap is not None:
        # Using mask (replaces values WHERE condition is True)
        df["rolling_1m_returns"] = df["rolling_1m_returns"].mask((df["rolling_1m_returns"] > cap) | (df["rolling_1m_returns"] < -cap), np.nan)

    if clip_down is not None:
        df["rolling_1m_returns"] = df["rolling_1m_returns"].clip(lower=clip_down)

    if clip_up is not None:
        df["rolling_1m_returns"] = df["rolling_1m_returns"].clip(upper=clip_up)

    if drop_threshold is not None:
        # Step 1: Identify vaults with extreme returns
        extreme_return_vaults = returns_df.groupby("name")[returns_column].apply(lambda x: (x > 1000).any())
        extreme_return_names = extreme_return_vaults[extreme_return_vaults].index.tolist()

        print("Removing extreme return vaults: ", extreme_return_names)

        # Step 2: Filter the DataFrame to exclude these vaults
        df = df[~df["name"].isin(extreme_return_names)]

    # Limit chart width
    df = df.loc[df["timestamp"] >= (pd.Timestamp.now() - history_length)]

    return df


def calculate_daily_returns_for_all_vaults(df_work: pd.DataFrame) -> pd.DataFrame:
    """Calculate daily returns for each vault in isolation.

    Sparse observations are resampled and forward filled to produce one row
    per consecutive calendar day for each vault.

    :param df_work:
        DataFrame with sparse or hourly share-price values.
    :return:
        Daily vault rows with ``share_price_daily`` and ``daily_returns``
        columns.
    """

    df_work = df_work.set_index("timestamp")

    result_dfs = []

    # Group by chain and address, then resample and forward fill
    for (chain_val, addr_val), group in df_work.groupby(["chain", "address"]):
        # Resample this group to daily frequency and forward fill
        resampled = group.resample("D").last().ffill()
        resampled["share_price_daily"] = resampled["share_price"]

        # Calculate daily returns
        resampled["daily_returns"] = resampled["share_price_daily"].pct_change(fill_method=None).fillna(0)

        # Add back the groupby keys as they'll be dropped during resampling
        resampled["chain"] = chain_val
        resampled["address"] = addr_val

        result_dfs.append(resampled)

    # Concatenate all the processed groups
    df_result = pd.concat(result_dfs)

    return df_result


def visualise_rolling_returns(
    rolling_returns_df: pd.DataFrame,
    title="1M rolling returns by vault",
    yaxis_title="1-Month Rolling Returns (%)",
    yaxis_range: tuple[float, float] | None = None,
) -> Figure:
    """Visualise rolling returns from a DataFrame.

    :param df:
        Calculated with :py:func`calculate_rolling_returns`.
    """
    assert isinstance(rolling_returns_df, pd.DataFrame), "rolling_returns_df must be a pandas DataFrame"

    assert "timestamp" in rolling_returns_df.columns, "rolling_returns_df must have a 'timestamp' column, index not supported"
    assert "rolling_1m_returns" in rolling_returns_df.columns, "rolling_returns_df must have a 'rolling_1m_returns' column"

    df = rolling_returns_df.copy()

    # Wrap long legend names
    df["name"] = df["name"].apply(lambda x: wrap_legend_text(x, max_length=30))

    # Remove entries with all zero returns.
    # TODO: Get rid of Hyped USDB and others with zero returns still showing up in the charts
    mask = df.groupby("name")["returns_1h"].transform(lambda x: (x != 0).any())
    filtered_returns_df = df[mask]

    fig = px.line(
        filtered_returns_df,
        x="timestamp",
        y="rolling_1m_returns",
        color="name",
        title=title,
        labels={"rolling_1m_returns": yaxis_title, "timestamp": "Date", "name": "Vault"},
        hover_data=["id"],
        color_discrete_sequence=qualitative.Dark24,
    )
    fig.update_layout(
        xaxis_title="Date",
        yaxis_title=yaxis_title,
        legend_title="Vault",
        hovermode="closest",
        template=pio.templates.default,
        yaxis_range=list(yaxis_range) if yaxis_range else None,
    )
    fig.update_traces(line=dict(width=3))

    return fig
