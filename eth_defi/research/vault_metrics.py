"""Vault metrics calculations.

- Calculate various performance reports and charts for vaults.
- `For performance stats see FFN <https://pmorissette.github.io/ffn/quick.html>`__.
"""

from typing import Literal
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from plotly.subplots import make_subplots
from plotly.graph_objects import Figure
import plotly.io as pio
from tqdm.auto import tqdm


from eth_defi.chain import get_chain_name
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultLead

from ffn.core import PerformanceStats
from ffn.core import calc_stats


def calculate_lifetime_metrics(
    df: pd.DataFrame,
    vaults_by_id: VaultDatabase,
    returns_column: str = "returns_1h",
):
    """Calculate lifetime metrics for each vault in the provided DataFrame.

    - All-time returns
    - 3M returns, latest
    - 1M returns, latest
    - Volatility (3M)
    """
    assert isinstance(df.index, pd.DatetimeIndex)
    assert isinstance(vaults_by_id, dict), "vaults_by_id should be a dictionary of vault metadata"

    month_ago = df.index.max() - pd.Timedelta(days=30)
    three_months_ago = df.index.max() - pd.Timedelta(days=90)

    def process_vault_group(group):
        """Process a single vault group to calculate metrics."""
        # Extract the group name (id_val)
        id_val = group["id"].iloc[0]

        # Sort by timestamp just to be safe
        # group = group.sort_index()

        # Extract vault metadata
        vault_spec = VaultSpec.parse_string(id_val, separator="-")
        vault_metadata: VaultLead = vaults_by_id[vault_spec]
        name = vault_metadata.get("Name")
        denomination = vault_metadata.get("Denomination")

        max_nav = group["total_assets"].max()
        current_nav = group["total_assets"].iloc[-1]
        chain_id = group["chain"].iloc[-1]
        mgmt_fee = group["management_fee"].iloc[-1]
        perf_fee = group["performance_fee"].iloc[-1]
        event_count = group["event_count"].iloc[-1]
        protocol = group["protocol"].iloc[-1]

        # Calculate lifetime return using cumulative product approach
        lifetime_return = group.iloc[-1]["share_price"] / group.iloc[0]["share_price"] - 1
        # Calculate CAGR
        # Get the first and last date
        start_date = group.index.min()
        end_date = group.index.max()
        age = years = (end_date - start_date).days / 365.25
        cagr = (1 + lifetime_return) ** (1 / years) - 1 if years > 0 else np.nan

        last_three_months = group.loc[three_months_ago:]
        last_month = group.loc[month_ago:]

        # Calculate 3 months CAGR
        # Get the first and last date
        if len(last_three_months) >= 2:
            start_date = last_three_months.index.min()
            end_date = last_three_months.index.max()
            years = (end_date - start_date).days / 365.25
            three_month_returns = last_three_months.iloc[-1]["share_price"] / last_three_months.iloc[0]["share_price"] - 1
            three_months_cagr = (1 + three_month_returns) ** (1 / years) - 1 if years > 0 else np.nan
            # Calculate volatility so we can separate actively trading vaults (market making, such) from passive vaults (lending optimisaiton)
            hourly_returns = last_three_months[returns_column]

            # Daily-equivalent volatility from hourly returns (multiply by sqrt(24) to scale from hourly to daily)
            three_months_volatility = hourly_returns.std() * np.sqrt(30)
            # three_months_volatility = 0

        else:
            # We have not collected data for the last three months,
            # because our stateful reader decided the vault is dead
            three_months_cagr = 0
            three_months_volatility = 0
            three_month_returns = 0

        if len(last_month) >= 2:
            start_date = last_month.index.min()
            end_date = last_month.index.max()
            years = (end_date - start_date).days / 365.25
            one_month_returns = last_month.iloc[-1]["share_price"] / last_month.iloc[0]["share_price"] - 1
            one_month_cagr = (1 + one_month_returns) ** (1 / years) - 1 if years > 0 else np.nan

        else:
            # We have not collected data for the last month,
            # because our stateful reader decided the vault is dead
            one_month_cagr = 0
            one_month_returns = 0

        return pd.Series(
            {
                "name": name,
                "lifetime_return": lifetime_return,
                "cagr": cagr,
                "three_months_returns": three_month_returns,
                "three_months_cagr": three_months_cagr,
                "one_month_returns": one_month_returns,
                "one_month_cagr": one_month_cagr,
                "three_months_volatility": three_months_volatility,
                "denomination": denomination,
                "chain": get_chain_name(chain_id),
                "peak_nav": max_nav,
                "current_nav": current_nav,
                "years": age,
                "mgmt_fee": mgmt_fee,
                "perf_fee": perf_fee,
                "event_count": event_count,
                "protocol": protocol,
                "id": id_val,
                "start_date": start_date,
                "end_date": end_date,
            }
        )

    # Enable tqdm progress bar for pandas
    tqdm.pandas(desc="Calculating vault performance metrics")

    # Use progress_apply instead of the for loop
    results_df = df.groupby("id").progress_apply(process_vault_group)

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


def format_lifetime_table(df: pd.DataFrame) -> pd.DataFrame:
    """Format table for human readable output.

    See :py:func:`calculate_lifetime_metrics`
    """

    df = df.copy()
    df["cagr"] = df["cagr"].apply(lambda x: f"{x:.2%}")
    df["lifetime_return"] = df["lifetime_return"].apply(lambda x: f"{x:.2%}")
    df["three_months_cagr"] = df["three_months_cagr"].apply(lambda x: f"{x:.2%}")
    df["three_months_returns"] = df["three_months_returns"].apply(lambda x: f"{x:.2%}")
    df["one_month_cagr"] = df["one_month_cagr"].apply(lambda x: f"{x:.2%}")
    df["one_month_returns"] = df["one_month_returns"].apply(lambda x: f"{x:.2%}")
    df["three_months_volatility"] = df["three_months_volatility"].apply(lambda x: f"{x:.4f}")
    df["event_count"] = df["event_count"].apply(lambda x: f"{x:,}")
    df["mgmt_fee"] = df["mgmt_fee"].apply(lambda x: f"{x:.2%}" if pd.notna(x) else "unknown")
    df["perf_fee"] = df["perf_fee"].apply(lambda x: f"{x:.2%}" if pd.notna(x) else "unknown")

    df = df.rename(
        columns={
            "lifetime_return": "Lifetime return",
            "cagr": "Lifetime return ann.",
            "three_months_returns": "Last 3M return",
            "three_months_cagr": "Last 3M return ann.",
            "three_months_volatility": "Last 3M months volatility",
            "one_month_returns": "Last 1M return",
            "one_month_cagr": "Last 1M return ann.",
            "event_count": "Deposit/redeem count",
            "peak_nav": "Peak TVL USD",
            "current_nav": "Current TVL USD",
            "years": "Age (years)",
            "mgmt_fee": "Management fee",
            "perf_fee": "Performance fee",
            "denomination": "Denomination",
            "chain": "Chain",
            "protocol": "Protocol",
            "start_date": "First deposit",
            "end_date": "Last deposit",
            "name": "Name",
        }
    )

    df = df.set_index("Name")

    return df


@dataclass(frozen=True, slots=True)
class VaultReport:
    """One vault data analysed"""

    #: Rolling returns chart
    rolling_returns_chart: Figure

    #: Performance table
    #:
    #: Needs to have quantstats installed
    # performance_metrics_df: pd.DataFrame | None

    performance_stats: PerformanceStats

    daily_returns: pd.Series
    hourly_returns: pd.Series


def analyse_vault(
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
    spec: VaultSpec,
    returns_col: str = "returns_1h",
    logger=print,
    chart_frequency: Literal["hourly", "daily"] = "daily",
) -> VaultReport:
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
        Analysis report to display
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

    logger(f"Examining vault {name}: {id}, having {len(returns_series):,} raw returns, {len(hourly_returns):,} hourly and {len(daily_returns):,} daily returns")
    nav_series = vault_df["total_assets"]

    # Uncleaned share price that may contain abnormal return values
    price_series = vault_df["share_price"]

    # Calculate cumulative returns (what $1 would grow to)
    cumulative_returns = (1 + hourly_returns).cumprod()

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
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="center", x=0.5),
        margin=dict(t=120),
    )

    # Set y-axes titles
    fig.update_yaxes(title_text=f"Share Price ({vault_metadata['Denomination']})", secondary_y=False)
    fig.update_yaxes(title_text=f"TVL ({vault_metadata['Denomination']})", secondary_y=True)

    performance_stats = calc_stats(daily_prices)
    performance_stats.name = name

    return VaultReport(
        rolling_returns_chart=fig,
        performance_stats=performance_stats,
        daily_returns=daily_returns,
        hourly_returns=hourly_returns,
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
