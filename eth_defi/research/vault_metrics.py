"""Vault metrics calculations.

- Calculate various performance reports and charts for vaults.
- `For performance stats see FFN <https://pmorissette.github.io/ffn/quick.html>`__.
"""

import datetime
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
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.token import is_stablecoin_like
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase, VaultLead

from ffn.core import PerformanceStats
from ffn.core import calc_stats
from ffn.utils import fmtn, fmtp, fmtpn, get_freq_name


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

    key = next(iter(vaults_by_id.keys()))
    assert isinstance(key, VaultSpec), f"Wrong kind of VaultDatabase detected: {type(key)}: {key}"

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
        vault_metadata: VaultLead = vaults_by_id.get(vault_spec)

        assert vault_metadata, f"Vault metadata not found for {id_val}. This vault is present in price data, but not in metadata entries. We have {len(vaults_by_id)} metadata entries."

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

            # if not printed:
            #    print(f"Name: {name}, last month: {start_date} - {end_date}, years: {years}, exp. {1/years}, one month returns: {one_month_returns}, one month CAGR: {one_month_cagr}")
            #    printed = True

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
            "three_months_returns": "3M return",
            "three_months_cagr": "3M return ann.",
            "three_months_volatility": "3M months volatility",
            "one_month_returns": "1M return",
            "one_month_cagr": "1M return ann.",
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

    logger(f"Examining vault {name}: {id}, having {len(returns_series):,} raw returns, {len(hourly_returns):,} hourly and {len(daily_returns):,} daily returns")
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
    data = list(vault_db.values())
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
    df["Age"] = datetime.datetime.utcnow() - df["First seen"]
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
