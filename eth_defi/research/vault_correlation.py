"""Vault returns correlations heatmap and such."""

from collections.abc import Callable

import pandas as pd
import plotly.graph_objects as go
from plotly.graph_objects import Figure

from eth_defi.vault.flag import is_flagged_vault


def _resample_returns(group, period="1D"):
    returns = group["returns_1h"]
    return (1 + returns).resample(period).prod() - 1


def choose_vaults_for_correlation_comparison(
    lifetime_data_filtered_df: pd.DataFrame,
    min_nav: float = 50_000,
    per_protocol: int = 2,
    max: int = 20,
    printer: Callable[[str], None] = print,
) -> pd.DataFrame:
    """Pick meaningful vaults for the returns correlation comparison.

    Takes the vaults with the best three-month returns, limiting the number
    of vaults per protocol to get more variety. Vaults of protocols we have
    not tagged yet (``unknown`` in the protocol name) are not limited.

    :param lifetime_data_filtered_df:
        Vault metrics with ``three_months_returns``, ``three_months_cagr``,
        ``current_nav``, ``address`` and ``protocol`` columns.

    :param min_nav:
        Minimum current TVL in USD.

    :param per_protocol:
        Maximum vaults per protocol.

    :param max:
        Maximum vaults in total.

    :param printer:
        Where to write a description of the selection criteria.

    :return:
        Selected vault rows sorted by ``three_months_cagr``. Empty if no vault matches.
    """
    df = lifetime_data_filtered_df.dropna(subset=["three_months_returns", "current_nav"])
    df = df[df["current_nav"] >= min_nav]
    df = df[~df["address"].str.lower().apply(is_flagged_vault).astype(bool)]
    df = df.sort_values(by="three_months_returns", ascending=False)

    printer(f"For the correlation matrix, we choose the top {max} vaults by their 3M returns, with minimum TVL of {min_nav:,} USD and then limiting to {per_protocol} vaults per protocol to have more variety.")

    rank_in_protocol = df.groupby("protocol").cumcount()
    untagged = df["protocol"].str.lower().str.contains("unknown")
    chosen = df[(rank_in_protocol < per_protocol) | untagged].head(max)
    return chosen.sort_values(by="three_months_cagr", ascending=False)


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

    # Creates MultiIndex (id, timestamp) series
    returns_1d = returns_df.groupby("id").apply(_resample_returns, include_groups=False)

    # Vault name -> daily returns as a column
    returns_data = {}

    selected_lifetime_data_df = selected_lifetime_data_df.set_index("id")

    for id in included_ids:
        row = selected_lifetime_data_df.loc[id]
        name = row["name"]
        try:
            returns_data[name] = returns_1d.loc[id]
        except KeyError as e:
            raise RuntimeError(f"The returns data did not have series for vault id {id}, name {name}. Full vault row is {row}") from e

    returns_df = pd.DataFrame(returns_data)

    # Calculate correlation matrix
    correlation_matrix = returns_df.corr()

    # Create heatmap using Plotly
    fig = go.Figure(data=go.Heatmap(z=correlation_matrix.values, x=correlation_matrix.columns, y=correlation_matrix.index, colorscale="RdBu", zmid=0, text=correlation_matrix.round(2).values, texttemplate="%{text}", textfont={"size": 10}, hoverongaps=False))

    fig.update_layout(title="Vault daily returns correlation, last 3 months", xaxis_title="Vault", yaxis_title="Vault", width=width, height=height)

    return fig
