"""Render sparkline charts for ERC-4626 vault data.

- Sparkline is a mini price chart, popularised by CoinMarketCap
- Charts contain share price and TVL
"""

from io import BytesIO

import pandas as pd

import matplotlib.pyplot as plt

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


def render_sparkline(
    vault_prices_df: pd.DataFrame,
    width: int = 256,
    height: int = 64,
) -> plt.Figure:
    """Render a sparkline chart for a single vault.

    :param spec::
        chain-vault address identifier
    """

    vault_data = vault_prices_df

    assert len(vault_data) > 0, f"No data for vault: {id}"

    assert isinstance(vault_data.index, pd.DatetimeIndex), f"Expected DatetimeIndex, got: {type(vault_data.index)}"

    # Convert pixels to inches (matplotlib uses inches)
    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.patch.set_facecolor("black")

    # Full-extent axis (no margins)
    ax1 = fig.add_axes([0, 0, 1, 1])
    ax1.set_facecolor("black")
    ax1.plot(vault_data.index, vault_data["share_price"], color="lime", linewidth=1)

    ax2 = ax1.twinx()
    ax2.set_facecolor("black")
    ax2.plot(vault_data.index, vault_data["total_assets"], color="lightgray", linewidth=1)

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


def export_sparkline_as_png(
    fig: plt.Figure,
) -> bytes:
    """Render a sparkline chart and return as PNG bytes."""

    # Create a BytesIO buffer to save the PNG
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=100)
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
    fig.savefig(buffer, format="svg")
    plt.close(fig)

    # Get the SVG bytes
    buffer.seek(0)
    svg_bytes = buffer.read()

    return svg_bytes

def upload_to_r2(
    payload: bytes,
    bucket_name: str,
    object_name: str,
    account_id: str,
    access_key_id: str,
    secret_access_key: str,
    content_type: str,
):
    """Uploads a the vault sparklines payload to a Cloudflare R2 bucket.

    - Exported to the frontend listings

    :param payload: The bytes data to upload.
    :param bucket_name: The name of the R2 bucket.
    :param object_name: The destination object name (e.g., "my-image.png").
    :param account_id: Your Cloudflare R2 account ID.
    :param access_key_id: Your R2 access key ID.
    :param secret_access_key: Your R2 secret access key.
    :param content_type: The MIME type of the file.
    """

    import boto3

    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com/vault-sparklines"

    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",  # Must be "auto"
    )

    import ipdb ; ipdb.set_trace()
    s3_client.put_object(
        Bucket=bucket_name,
        Key=object_name,
        Body=payload,
        ContentType=content_type,
    )

