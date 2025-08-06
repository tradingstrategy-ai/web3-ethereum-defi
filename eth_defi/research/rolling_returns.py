"""Used in vault report notebooks to calculate and rolling returns for vaults."""

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.io as pio
from plotly.colors import qualitative
from plotly.graph_objects import Figure
from plotly.subplots import make_subplots

CHART_BENCHMARK_COUNT: int = 10

RETURNS_ROLLING_WINDOW = 30  # Bars, 1M

CHART_HISTORY = pd.Timedelta(days=30 * 4)  #


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


def calculate_rolling_returns(
    returns_df: pd.DataFrame,
    interesting_vaults: pd.Series | None = None,
    filtered_vault_list_df: pd.DataFrame | None = None,
    window: int = RETURNS_ROLLING_WINDOW,  # Bars
    period: pd.Timedelta = CHART_HISTORY,
    cap: float = None,
    clip_down: float = None,
    clip_up: float = None,
    drop_threshold: float = None,
    benchmark_count: int = CHART_BENCHMARK_COUNT,
):
    """Calculate rolling returns starts for vaults.

    :param returns_df:
        See notebook examples.
    """

    # Pick N top vaults to show,
    # assume returns_df is sorted by wanted order
    if benchmark_count:
        assert isinstance(benchmark_count, int), "benchmark_count must be an integer"
        assert isinstance(filtered_vault_list_df, pd.DataFrame), "filtered_vault_list_df must be a pandas DataFrame"
        interesting_vaults = filtered_vault_list_df[0:benchmark_count]["id"]

    # Limit to benchmarked vaults
    if interesting_vaults is not None:
        df = returns_df[returns_df["id"].isin(interesting_vaults)]
    else:
        df = returns_df
    df = df.reset_index().sort_values(by=["id", "timestamp"])

    # Manually blacklist one vault where we get data until fixed
    df = df[df["name"] != "Revert Lend Arbitrum USDC,"]

    # Calculate rollling returns
    df["rolling_1m_returns"] = df.groupby("id")["daily_returns"].transform(lambda x: (((1 + x).rolling(window=window).apply(np.prod) - 1) * 100))

    df["rolling_1m_returns_annualized"] = ((1 + df["rolling_1m_returns"] / 100) ** 12 - 1) * 100

    # When vault launches it has usually near-infinite APY
    # Cap it here so charts are readable
    if cap is not None:
        # Using mask (replaces values WHERE condition is True)
        df["rolling_1m_returns_annualized"] = df["rolling_1m_returns_annualized"].mask((df["rolling_1m_returns_annualized"] > cap) | (df["rolling_1m_returns_annualized"] < -cap), np.nan)

    if clip_down is not None:
        df["rolling_1m_returns_annualized"] = df["rolling_1m_returns_annualized"].clip(lower=clip_down)

    if clip_up is not None:
        df["rolling_1m_returns_annualized"] = df["rolling_1m_returns_annualized"].clip(upper=clip_up)

    if drop_threshold is not None:
        # Step 1: Identify vaults with extreme returns
        extreme_return_vaults = returns_df.groupby("name")["daily_returns"].apply(lambda x: (x > 1000).any())
        extreme_return_names = extreme_return_vaults[extreme_return_vaults].index.tolist()

        print("Removing extreme return vaults: ", extreme_return_names)

        # Step 2: Filter the DataFrame to exclude these vaults
        df = df[~df["name"].isin(extreme_return_names)]

    # Limit chart width
    df = df.loc[df["timestamp"] >= (pd.Timestamp.now() - period)]

    return df


def calculate_daily_returns_for_all_vaults(df_work: pd.DataFrame) -> pd.DataFrame:
    """Calculate daily returns for each vault in isolation.
    
    :param df_work: 
        DataFrame with hourly share price values.
    """

    df_work = df_work.set_index("timestamp")

    result_dfs = []
    
    # Group by chain and address, then resample and forward fill
    for (chain_val, addr_val), group in df_work.groupby(["chain", "address"]):
        # Resample this group to daily frequency and forward fill
        resampled = group
        resampled["share_price_daily"] = group["share_price"].resample("D").ffill()

        # Calculate daily returns
        resampled["daily_returns"] = resampled["share_price_daily"].pct_change(fill_method=None).fillna(0)

        # Add back the groupby keys as they'll be dropped during resampling
        resampled["chain"] = chain_val
        resampled["address"] = addr_val

        result_dfs.append(resampled)

    # Concatenate all the processed groups
    df_result = pd.concat(result_dfs)

    return df_result