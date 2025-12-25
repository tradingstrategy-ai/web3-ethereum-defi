"""Render sparkline charts for ERC-4626 vault data.

- Sparkline is a mini price chart, popularised by CoinMarketCap
- Charts contain share price and TVL
"""

import gzip
import warnings
from io import BytesIO

import pandas as pd
import numpy as np

import matplotlib.pyplot as plt

from eth_defi.research.wrangle_vault_prices import forward_fill_vault
from eth_defi.vault.base import VaultSpec


def extract_vault_price_data(
    spec: VaultSpec,
    prices_df: pd.DataFrame,
) -> pd.DataFrame:
    """Extract price data for a specific vault from a DataFrame.

    :param spec:
        chain-vault address identifier
    :param prices_df:
        DataFrame containing price data
    :return:
        Filtered DataFrame for the specified vault
    """
    assert isinstance(spec, VaultSpec), f"spec must be VaultSpec: {type(spec)}"

    # Filter data for the specific vault
    vault_data = prices_df.loc[(prices_df["chain"] == spec.chain_id) & (prices_df["address"] == spec.vault_address)]

    assert len(vault_data) > 0, f"No data for vault: {spec}"

    return vault_data


def render_sparkline_simple(
    vault_prices_df: pd.DataFrame,
    width: int = 256,
    height: int = 64,
    ffill=True,
) -> plt.Figure:
    """Render a sparkline chart for a single vault.

    :param spec:
        chain-vault address identifier

    :param ffill:
        Forward-fill the sparse source data
    """

    vault_data = vault_prices_df

    assert len(vault_data) > 0, f"No data for vault: {id}"
    assert isinstance(vault_data.index, pd.DatetimeIndex), f"Expected DatetimeIndex, got: {type(vault_data.index)}"

    if ffill:
        # old_data = vault_data.copy()
        vault_data = forward_fill_vault(vault_data)

    # Convert pixels to inches (matplotlib uses inches)
    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.patch.set_facecolor("black")

    # Full-extent axis (no margins)
    ax1 = fig.add_axes([0, 0, 1, 1])
    ax1.patch.set_alpha(0.0)
    ax1.plot(vault_data.index, vault_data["share_price"], color="#a6a4a0", linewidth=2)

    # Alpha = 0 = hidden for now
    ax2 = ax1.twinx()
    # ax2.patch.set_alpha(0.0)
    # ax2.plot(vault_data.index, vault_data["total_assets"], color="#999999", linewidth=2, alpha=0.0)

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


def render_sparkline_gradient(
    vault_prices_df: pd.DataFrame,
    width: int = 300,
    height: int = 300,
    ffill=True,
    line_color="#22B452",
    bg_color="#282827",
) -> plt.Figure:
    """Render a sparkline chart with green-to-black gradient fill."""

    vault_data = vault_prices_df

    if ffill:
        vault_data = forward_fill_vault(vault_data)

    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.patch.set_facecolor(bg_color)

    ax1 = fig.add_axes([0, 0, 1, 1])
    ax1.patch.set_facecolor(bg_color)

    # Get y-axis limits with margin
    y_min = vault_data["share_price"].min()
    y_max = vault_data["share_price"].max()
    y_range = y_max - y_min

    # Calculate margin in data units (50px / height * y_range)
    margin_ratio = 50 / height
    y_margin = y_range * margin_ratio

    # Apply margins (top only)
    y_min_with_margin = y_min - y_margin
    # y_min_with_margin = y_min
    y_max_with_margin = y_max + y_margin

    # Set y-axis limits with margin
    # UserWarning: Attempting to set identical low and high xlims makes transformation singular; automatically expanding.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ax1.set_ylim(y_min_with_margin, y_max_with_margin)

        # Rest of the code remains the same, but use original y_min for gradient extent
        gradient = np.linspace(0, 1, 256).reshape(256, 1)
        im = ax1.imshow(
            gradient,
            extent=[vault_data.index[0], vault_data.index[-1], y_min, y_max],
            aspect="auto",
            cmap=plt.cm.colors.LinearSegmentedColormap.from_list("green_black", [line_color, bg_color]),
            alpha=0.4,
            zorder=0,
        )

        collection = ax1.fill_between(vault_data.index, vault_data["share_price"], y_min, alpha=0)
        im.set_clip_path(collection.get_paths()[0], transform=ax1.transData)

        ax1.plot(
            vault_data.index,
            vault_data["share_price"],
            color="#00ff88",
            linewidth=2,
            zorder=2,
        )

        for spine in ax1.spines.values():
            spine.set_visible(False)
        ax1.set_xticks([])
        ax1.set_yticks([])
        ax1.get_xaxis().set_visible(False)
        ax1.get_yaxis().set_visible(False)
        ax1.margins(x=0, y=0)

        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    return fig


def export_sparkline_as_png(
    fig: plt.Figure,
) -> bytes:
    """Render a sparkline chart and return as PNG bytes."""

    # Create a BytesIO buffer to save the PNG
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=100, transparent=False)
    plt.close(fig)

    # Get the PNG bytes
    buffer.seek(0)
    png_bytes = buffer.read()

    return png_bytes


def export_sparkline_as_svg(
    fig: plt.Figure,
) -> bytes:
    """Render a sparkline chart and return as SVG bytes."""

    # Create a BytesIO buffer to save the SVG
    buffer = BytesIO()
    fig.savefig(buffer, format="svg", transparent=True)
    plt.close(fig)

    # Get the SVG bytes
    buffer.seek(0)
    svg_bytes = buffer.read()

    return svg_bytes


def upload_to_r2_compressed(
    payload: bytes,
    bucket_name: str,
    object_name: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    content_type: str,
):
    """Uploads a the vault sparklines payload to a Cloudflare R2 bucket.

    - Exported to the frontend listings
    - Compress SVGs with gzip

    :param payload: The bytes data to upload.
    :param bucket_name: The name of the R2 bucket.
    :param object_name: The destination object name (e.g., "my-image.png").
    :param account_id: Your Cloudflare R2 account ID.
    :param access_key_id: Your R2 access key ID.
    :param secret_access_key: Your R2 secret access key.
    :param content_type: The MIME type of the file.
    """

    import boto3

    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",  # Must be "auto"
    )

    s3_client.put_object(
        Bucket=bucket_name,
        Key=object_name,
        Body=gzip.compress(payload),
        ContentType=content_type,
        ContentEncoding="gzip",
    )
