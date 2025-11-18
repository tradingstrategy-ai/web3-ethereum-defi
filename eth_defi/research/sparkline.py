"""Render sparkline charts for ERC-4626 vault data.

- Sparkline is a mini price chart, popularised by CoinMarketCap
- Charts contain share price and TVL
"""

from io import BytesIO

import pandas as pd

import matplotlib.pyplot as plt

from eth_defi.vault.base import VaultSpec


def render_sparkline(
    spec: VaultSpec,
    prices_df: pd.DataFrame,
    width: int = 256,
    height: int = 64,
) -> plt.Figure:
    """Render a sparkline chart for a single vault.

    :param spec::
        chain-vault address identifier
    """

    assert isinstance(spec, VaultSpec), f"spec must be VaultSpec: {type(spec)}"

    # Filter data for the specific vault
    vault_data = prices_df.loc[(prices_df["chain"] == spec.chain_id) & (prices_df["address"] == spec.vault_address)]

    assert len(vault_data) > 0, f"No data for vault: {id}"

    # Sort by timestamp to ensure proper plotting
    vault_data = vault_data.sort_values("timestamp")

    # Convert pixels to inches (matplotlib uses inches)
    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.patch.set_facecolor("black")

    # Full-extent axis (no margins)
    ax1 = fig.add_axes([0, 0, 1, 1])
    ax1.set_facecolor("black")
    ax1.plot(vault_data["timestamp"], vault_data["share_price"], color="lime", linewidth=1)

    ax2 = ax1.twinx()
    ax2.set_facecolor("black")
    ax2.plot(vault_data["timestamp"], vault_data["total_assets"], color="lightgray", linewidth=1)

    # Remove all spines, ticks, labels
    for ax in (ax1, ax2):
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.get_xaxis().set_visible(False)
        ax.get_yaxis().set_visible(False)
        ax.margins(x=0, y=0)  # eliminate data padding

    # Fill entire canvas
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    fig.patch.set_facecolor("black")
    ax1.patch.set_facecolor("black")

    return fig


def render_sparkline_as_png(
    spec: VaultSpec,
    prices_df: pd.DataFrame,
    width: int = 256,
    height: int = 64,
) -> bytes:
    """Render a sparkline chart and return as PNG bytes.

    :param id:
        chain-vault address identifier
    :param prices_df:
        DataFrame containing price data
    :param width:
        Width of the chart in pixels
    :param height:
        Height of the chart in pixels
    :return:
        PNG image as bytes
    """
    # Generate the matplotlib figure
    fig = render_sparkline(spec, prices_df, width, height)

    # Create a BytesIO buffer to save the PNG
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=100)
    plt.close(fig)

    # Get the PNG bytes
    buffer.seek(0)
    png_bytes = buffer.read()

    return png_bytes
