"""Vault metrics calculations."""
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from plotly.graph_objects import Figure

from eth_defi.chain import get_chain_name
from eth_defi.vault.vaultdb import VaultDatabase

try:
    import quantstats
except ImportError:
    quantstats = None


def calculate_lifetime_metrics(
    df: pd.DataFrame,
    vaults_by_id: dict,
):
    """Calculate lifetime metrics for each vault in the provided DataFrame.

    - All-time returns
    - 3M returns
    - 1M returns
    - Volatility (3M)

    :param df:
        See notebooks

    """
    results = []

    month_ago = df.index.max() - pd.Timedelta(days=30)
    three_months_ago = df.index.max() - pd.Timedelta(days=90)

    for id_val, group in df.groupby("id"):
        # Sort by timestamp just to be safe
        group = group.sort_index()
        name = vaults_by_id[id_val]["Name"] if id_val in vaults_by_id else None

        # Calculate lifetime return using cumulative product approach
        lifetime_return = (1 + group["daily_returns"]).prod() - 1

        last_three_months = group["daily_returns"].loc[three_months_ago:]
        three_month_returns = (1 + last_three_months).prod() - 1

        last_month = group["daily_returns"].loc[month_ago:]
        one_month_returns = (1 + last_month).prod() - 1

        # Calculate volatility so we can separate actively trading vaults (market making, such) from passive vaults (lending optimisaiton)
        three_months_volatility = last_three_months.std()

        max_nav = group["total_assets"].max()
        current_nav = group["total_assets"].iloc[-1]
        chain_id = group["chain"].iloc[-1]
        mgmt_fee = group["management_fee"].iloc[-1]
        perf_fee = group["performance_fee"].iloc[-1]
        event_count = group["event_count"].iloc[-1]
        protocol = group["protocol"].iloc[-1]

        # Calculate CAGR
        # Get the first and last date
        start_date = group.index.min()
        end_date = group.index.max()
        age = years = (end_date - start_date).days / 365.25
        cagr = (1 + lifetime_return) ** (1 / years) - 1 if years > 0 else np.nan

        # Calculate 3 months CAGR
        # Get the first and last date
        start_date = last_three_months.index.min()
        end_date = last_three_months.index.max()
        years = (end_date - start_date).days / 365.25
        three_months_cagr = (1 + three_month_returns) ** (1 / years) - 1 if years > 0 else np.nan

        start_date = last_month.index.min()
        end_date = last_month.index.max()
        years = (end_date - start_date).days / 365.25
        one_month_cagr = (1 + one_month_returns) ** (1 / years) - 1 if years > 0 else np.nan

        results.append(
            {
                "name": name,
                "cagr": cagr,
                "lifetime_return": lifetime_return,
                "three_months_cagr": three_months_cagr,
                "one_month_cagr": one_month_cagr,
                "three_months_volatility": three_months_volatility,
                "denomination": vaults_by_id[id_val]["Denomination"] if id_val in vaults_by_id else None,
                "chain": get_chain_name(chain_id),
                "peak_nav": max_nav,
                "current_nav": current_nav,
                "years": age,
                "mgmt_fee": mgmt_fee,
                "perf_fee": perf_fee,
                "event_count": event_count,
                "protocol": protocol,
                "id": id_val,
                "three_months_returns": three_month_returns,
                "one_month_returns": one_month_returns,
                "start_date": start_date,
                "end_date": end_date,
            }
        )

    return pd.DataFrame(results)


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
            "cagr": "Annualised lifetime return",
            "lifetime_return": "Lifetime return",
            "three_months_cagr": "Last 3M return annualised",
            "three_months_volatility": "Last 3M months volatility",
            "one_month_cagr": "Last 1M return annualised",
            "three_months_volatility": "Last 3M months volatility",
            "three_months_returns": "Last 3M return",
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
        }
    )
    return df


@dataclass(frozen=True, slots=True)
class VaultReport:
    """One vault data analysed"""

    rolling_returns_freq: str

    #: Rolling returns chart
    rolling_returns_chart: Figure

    #: Performance table
    #:
    #: Needs to have quantstats installed
    performance_metrics_df: pd.DataFrame | None


def analyse_vault(
    vault_db: VaultDatabase,
    all_returns_df: pd.DataFrame,
    id: str,
) -> VaultReport:
    """Create charts and tables to analyse a vault performance.

    - We plot our annualised 1 month rolling returns on the chart, to see how vaults move in the direction of the markets, or what kind of outliers there are

    :param vault_db:
        Database of all vault metadata

    :param all_returns_df:
        Cleaned returns of all vaults

    :param id:
        Vault chain + address to analyse, e.g. "1-0x1234567890abcdef1234567890abcdef12345678"

    :return:
        Analysis report to display
    """
    returns_df = all_returns_df
    name = vault_db[id]["Name"]

    vault_df = returns_df.loc[returns_df["id"] == id]
    daily_returns = returns_df.loc[returns_df["id"] == id]["daily_returns"]
    vault_metadata = vault_db[id]
    print(f"Examining vault {name}: {id}, having {len(daily_returns):,} daily returns rows")
    nav_series = vault_df["total_assets"]

    price_series = vault_df["share_price"]

    # Calculate cumulative returns (what $1 would grow to)
    cumulative_returns = (1 + daily_returns).cumprod()

    # Create figure with secondary y-axis
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Add cumulative returns trace on a separate y-axis (share same axis as share price)
    fig.add_trace(
        go.Scatter(x=cumulative_returns.index, y=cumulative_returns.values, name="Cumulative returns (cleaned)", line=dict(color="darkgreen", width=4), opacity=0.75),
        secondary_y=False,
    )

    # Add share price trace on primary y-axis
    fig.add_trace(
        go.Scatter(x=price_series.index, y=price_series.values, name="Share Price", line=dict(color="green", width=4, dash="dash"), opacity=0.75),
        secondary_y=False,
    )

    # Add NAV trace on secondary y-axis
    fig.add_trace(
        go.Scatter(x=nav_series.index, y=nav_series.values, name="TVL", line=dict(color="blue", width=4), opacity=0.75),
        secondary_y=True,
    )

    # Set titles and labels
    fig.update_layout(title_text=f"{name} - Returns TVL and share price", hovermode="x unified", template=pio.templates.default, showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5))

    # Set y-axes titles
    fig.update_yaxes(title_text=f"Share Price ({vault_metadata['Denomination']})", secondary_y=False)
    fig.update_yaxes(title_text=f"TVL ({vault_metadata['Denomination']})", secondary_y=True)

    # Show portfolio metrics
    if quantstats:
        with warnings.catch_warnings():
            warnings.simplefilter(action="ignore", category=RuntimeWarning)
            warnings.simplefilter(action="ignore", category=FutureWarning)

            metrics = quantstats.reports.metrics
            performance_metrics_df = metrics(
                daily_returns,
                benchmark=None,
                as_pct=True,  # QuantStats codebase is a mess
                periods_per_year=365,
                mode="simple",
                display=False,
                internal=True,
            )
            performance_metrics_df.rename(columns={"Strategy": name}, inplace=True)
    else:
        performance_metrics_df = None

    return VaultReport(
        rolling_returns_chart=fig,
        performance_metrics_df=performance_metrics_df,
    )

