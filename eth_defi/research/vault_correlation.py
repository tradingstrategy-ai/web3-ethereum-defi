"""Vault returns correlations heatmap and such."""

from collections import Counter

import pandas as pd

import plotly.graph_objects as go
from plotly.graph_objects import Figure


def choose_vaults_for_correlation_comparison(
    lifetime_data_filtered_df: pd.DataFrame,
    min_nav=50_000,
    per_protocol = 2,
    max = 20,
    printer=print,
) -> pd.DataFrame:
    """Pick meaningful vaults for the comparison"""

    protocols_counts = Counter()
    chosen_rows = []

    lifetime_data_filtered_df = lifetime_data_filtered_df.copy()

    lifetime_data_filtered_df = lifetime_data_filtered_df[lifetime_data_filtered_df["current_nav"] >= min_nav]

    lifetime_data_filtered_df = lifetime_data_filtered_df.sort_values(by="three_months_returns", ascending=True)

    printer(f"For the correlation matrix, we choose the top {max} vaults by their 3M returns, with minimum TVL of {min_nav:,} USD and then limiting to {per_protocol} vaults per protocol to have more variety.")

    for idx, row in lifetime_data_filtered_df.iterrows():
        protocol = row["protocol"]
        protocols_counts[protocol] += 1

        # Alwaas include protocols we have not been able to tag yet
        if protocols_counts[protocol] <= per_protocol or ("unknown" in protocol.lower()):
            chosen_rows.append(row)

        if len(chosen_rows) >= max:
            break

    return pd.DataFrame(chosen_rows)


def visualise_vault_returns_correlation(
    selected_lifetime_data_df: pd.DataFrame,
    returns_df: pd.DataFrame,
    width=1000,
    height=1000,
) -> Figure:
    """Draw a correlation matrix.

    - Daily returns
    """

    included_ids = selected_lifetime_data_df["id"].tolist()

    returns_1d = returns_df["returns_1h"].resample("1d").last().pct_change()

    returns_data = {}

    selected_lifetime_data_df = selected_lifetime_data_df.set_index("id")

    for id in included_ids:
        name = selected_lifetime_data_df.loc[id]["name"]
        import ipdb ; ipdb.set_trace()
        returns_data[name] = returns_1d.loc[returns_1d["id"] == id]

    returns_df = pd.DataFrame(returns_data)

    # Calculate correlation matrix
    correlation_matrix = returns_df.corr()

    # Create heatmap using Plotly
    fig = go.Figure(data=go.Heatmap(
        z=correlation_matrix.values,
        x=correlation_matrix.columns,
        y=correlation_matrix.index,
        colorscale='RdBu',
        zmid=0,
        text=correlation_matrix.round(2).values,
        texttemplate="%{text}",
        textfont={"size": 10},
        hoverongaps=False
    ))

    fig.update_layout(
        title="Vault returns correlation",
        xaxis_title="Vault",
        yaxis_title="Vault",
        width=width,
        height=height
    )

    return fig
