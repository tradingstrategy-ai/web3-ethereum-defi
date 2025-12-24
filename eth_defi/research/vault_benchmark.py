import pandas as pd

from eth_defi.compat import native_datetime_utc_now
from eth_defi.research.vault_metrics import calculate_returns, calculate_net_returns_from_price, calculate_cumulative_returns, calculate_net_returns_from_gross
from eth_defi.vault.base import VaultSpec

from plotly.colors import qualitative
from plotly.graph_objects import Figure
import plotly.express as px

from eth_defi.vault.vaultdb import VaultDatabase, has_good_fee_data


def visualise_vault_return_benchmark(
    vault_spec: list[VaultSpec],
    vault_db: VaultDatabase,
    prices_df: pd.DataFrame,
    lookback=pd.Timedelta("90 days"),
    title="Vault benchmark (returns after fees)",
    color_discrete_sequence=qualitative.Dark24,
    printer=print,
) -> tuple[Figure, pd.DataFrame]:
    """Plot the net returns benchmark chart for multiple vaults.

    - Ues net returns
    - If fee information is not available, skips the vault

    .. note ::

        Does not account fees changing over the time

    :param printer:
        Echo missing vault fee data warnings

    :return:
        tuple (Figure, net returns for all assets as DF)
    """

    ids = {spec.as_string_id() for spec in vault_spec}
    vaults_df = prices_df.loc[prices_df["id"].isin(ids)]
    cut_off = native_datetime_utc_now() - lookback
    period_prices_df = vaults_df.loc[vaults_df.index >= cut_off]

    net_returns_data = {}

    for vault_id, group in period_prices_df.groupby("id"):
        vault = vault_db.get(VaultSpec.parse_string(vault_id))
        assert vault, f"Data mismatch: vault metadata not found for scanned prices: {vault_id}"

        name = vault["Name"]

        if not has_good_fee_data(vault):
            printer(f"Skipping vault {vault_id}: {name} due to missing fee data")
            # name = name + " (fees unk.)"
            # net_returns_series = pd.Series([], dtype="float64", index=pd.DatetimeIndex([]))
            continue
        else:
            cleaned_returns_series = calculate_cumulative_returns(
                cleaned_returns=group["returns_1h"],
            )

            net_returns_series = calculate_net_returns_from_gross(
                name=name,
                cumulative_returns=cleaned_returns_series,
                management_fee_annual=vault.get("Mgmt fee"),
                performance_fee=vault.get("Perf fee"),
                deposit_fee=vault.get("Deposit fee"),
                withdrawal_fee=vault.get("Withdrawal fee"),
            )

        net_returns_data[name] = net_returns_series

        # Catch some bad data
        if len(net_returns_series) > 0:
            assert isinstance(net_returns_series.index, pd.DatetimeIndex)
            final_date = net_returns_series.index.max()
            assert final_date < pd.Timestamp("2100-01-01"), f"Future date in returns series: {name}: {net_returns_data}: {final_date}"

    net_returns_df = pd.DataFrame(net_returns_data)

    # TODO:
    # Something add bogus NaN data at the future dates at the end of the data frame,
    # and could not figure out why.
    # Just work around because could not figure out why.
    # Likely a Pandas bug.
    # We just remove all rows where all values are NaN.
    # 2025-10-23                                              0.03
    # 2025-10-24                                              0.03
    # 4763-06-26                                               NaN
    # 4763-07-18                                               NaN
    net_returns_df = net_returns_df.dropna(how="all")

    # Time series might not be aligned if some vaults start in the middle of the period
    # net_returns_df = net_returns_df.fillna(0)

    # Turn returns to cumulative returns
    # cleaned_returns_df = (1 + cleaned_returns_df).cumprod() - 1
    net_returns_df = net_returns_df.ffill()

    # Convert to percent
    net_returns_df = net_returns_df.mul(100)

    fig = px.line(
        net_returns_df,
        title=title,
        color_discrete_sequence=color_discrete_sequence,
    )

    # Set axis labels
    fig.update_layout(
        xaxis_title="Date",
        yaxis_title="Cumulative returns %",
    )

    return fig, net_returns_df
