"""Render sparkline charts for ERC-4626 vault data.

- Sparkline is a mini price chart, popularised by CoinMarketCap
- Charts contain share price and TVL
"""

from io import BytesIO

import pandas as pd

import matplotlib.pyplot as plt


def render_sparkline(
    id: str,
    prices_df: pd.DataFrame,
    width: int = 256,
    height: int = 64,
) -> plt.Figure:
    """Render a sparkline chart for a single vault.

    :param id:
        chain-vault address identifier
    """

    assert type(id) == str, f"id must be str: {id}"

    # Filter data for the specific vault
    vault_data = prices_df[prices_df["id"] == id :].copy()

    assert len(vault_data) > 0, f"No data for vault: {id}"

    # Sort by timestamp to ensure proper plotting
    vault_data = vault_data.sort_values("timestamp")

    # Convert pixels to inches (matplotlib uses inches)
    dpi = 100
    width_inches = width / dpi
    height_inches = height / dpi

    # Create figure and primary axis
    fig, ax1 = plt.subplots(figsize=(width_inches, height_inches), dpi=dpi)

    # Plot share price on primary y-axis in green
    ax1.plot(vault_data["timestamp"], vault_data["share_price"], color="green")
    # x1.tick_params(axis='y', labelcolor='green')

    # Create secondary y-axis for TVL
    ax2 = ax1.twinx()
    ax2.plot(vault_data["timestamp"], vault_data["tvl"], color="gray")
    # ax2.tick_params(axis='y', labelcolor='gray')

    plt.xticks(rotation=45)
    plt.tight_layout()

    return fig


def render_sparkline_as_png(
    id: str,
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
    fig = render_sparkline(id, prices_df, width, height)

    # Create a BytesIO buffer to save the PNG
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=100)
    plt.close(fig)

    # Get the PNG bytes
    buffer.seek(0)
    png_bytes = buffer.read()

    return png_bytes
