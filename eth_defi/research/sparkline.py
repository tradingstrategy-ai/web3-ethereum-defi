"""Prepare, render and upload share-price sparklines for vault data."""

import gzip
import warnings
from dataclasses import dataclass
from io import BytesIO

import matplotlib as mpl
import numpy as np
import pandas as pd

mpl.use("Agg")
import matplotlib.pyplot as plt

from eth_defi.cloudflare_r2 import calculate_bytes_digest, create_r2_client, upload_bytes_to_r2
from eth_defi.research.wrangle_vault_prices import forward_fill_vault
from eth_defi.vault.base import VaultSpec

#: Width of the time axis used by published vault sparklines.
DEFAULT_SPARKLINE_WINDOW = pd.Timedelta(days=90)

#: Minimum elapsed finite share-price history required for publication.
MIN_SPARKLINE_HISTORY = pd.Timedelta(days=14)


@dataclass(slots=True)
class SparklineData:
    """Daily share prices and their fixed chart bounds.

    Every chart spans :data:`DEFAULT_SPARKLINE_WINDOW` and ends on the latest
    UTC day containing a finite observation. A vault younger than the window
    occupies only the right-hand side; the period before its first observation
    remains blank.
    """

    #: Daily finite share-price observations. Established sparse vaults include
    #: one carried observation at ``start_at``.
    prices_df: pd.DataFrame

    #: Inclusive left edge of the chart.
    start_at: pd.Timestamp

    #: Inclusive right edge and latest UTC day containing an observation.
    end_at: pd.Timestamp


def filter_finite_share_prices(vault_prices_df: pd.DataFrame) -> pd.DataFrame:
    """Return chart data with only finite, float share prices.

    PyArrow-backed parquet data can expose ``share_price`` as a nullable or
    object-backed pandas series. Matplotlib compares y-axis bounds internally,
    so passing ``pd.NA`` through causes ``TypeError: boolean value of NA is
    ambiguous``.

    :param vault_prices_df:
        Single-vault price data with a ``share_price`` column.

    :return:
        Copy of the input rows whose share prices are finite Python floats.

    :raise ValueError:
        If the vault has no finite share-price observations to render.
    """
    numeric_prices = pd.to_numeric(vault_prices_df["share_price"], errors="coerce")
    numeric_values = numeric_prices.to_numpy(dtype=float, na_value=np.nan)
    finite_mask = np.isfinite(numeric_values)
    if not finite_mask.any():
        message = "Cannot render sparkline without finite share prices"
        raise ValueError(message)

    filtered = vault_prices_df.iloc[finite_mask].copy()
    filtered["share_price"] = numeric_values[finite_mask]
    return filtered


def prepare_sparkline_data(
    vault_prices_df: pd.DataFrame,
    minimum_history: pd.Timedelta = MIN_SPARKLINE_HISTORY,
    window: pd.Timedelta = DEFAULT_SPARKLINE_WINDOW,
) -> SparklineData | None:
    """Prepare one vault's observations for a fixed-width sparkline.

    Eligibility is based on elapsed time between the first and latest finite
    source observations, not row count. Once that span reaches two weeks by
    default, observations are reduced to daily points. Charts always retain a
    90-day axis by default. Young vaults leave the period before their first
    observation blank, while older sparse vaults carry their last pre-window
    value to the left boundary.

    :param vault_prices_df:
        Single-vault data indexed by naive UTC timestamps with a
        ``share_price`` column.
    :param minimum_history:
        Minimum elapsed finite share-price history required for publication.
    :param window:
        Full elapsed time represented by the horizontal axis.
    :return:
        Daily observations and chart bounds, or ``None`` when the finite price
        history is shorter than ``minimum_history``.
    """
    assert isinstance(vault_prices_df.index, pd.DatetimeIndex), f"Expected DatetimeIndex, got {type(vault_prices_df.index)}"
    assert minimum_history > pd.Timedelta(0), f"Minimum history must be positive, got {minimum_history}"
    assert window >= minimum_history, f"Sparkline window {window} must cover minimum history {minimum_history}"

    try:
        finite_prices_df = filter_finite_share_prices(vault_prices_df).sort_index()
    except ValueError:
        return None

    if finite_prices_df.index[-1] - finite_prices_df.index[0] < minimum_history:
        return None

    # Eligibility uses exact source timestamps. Published charts use day
    # boundaries so the final plotted point reaches the fixed axis edge.
    daily_prices_df = finite_prices_df.resample("D").last().dropna(subset=["share_price"])
    end_at = daily_prices_df.index[-1]
    start_at = end_at - window
    visible_prices_df = daily_prices_df.loc[(daily_prices_df.index > start_at) & (daily_prices_df.index <= end_at)]

    # Forward filling cannot carry a value that was cropped away. Seed an
    # established vault at the boundary without inventing history for a vault
    # whose first observation is inside the window.
    previous_prices_df = daily_prices_df.loc[daily_prices_df.index <= start_at].tail(1)
    if not previous_prices_df.empty:
        previous_prices_df.index = pd.DatetimeIndex([start_at], name=vault_prices_df.index.name)
        visible_prices_df = pd.concat((previous_prices_df, visible_prices_df))

    return SparklineData(prices_df=visible_prices_df, start_at=start_at, end_at=end_at)


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
    ffill: bool = True,  # noqa: FBT001, FBT002
) -> plt.Figure:
    """Render a simple share-price sparkline for one vault.

    :param vault_prices_df:
        Single-vault prices indexed by naive UTC timestamps.
    :param width:
        Output width in pixels.
    :param height:
        Output height in pixels.
    :param ffill:
        Forward-fill sparse observations to an hourly line before rendering.
    :return:
        Matplotlib figure ready for export.
    """
    assert not vault_prices_df.empty, "Cannot render an empty vault price series"
    assert isinstance(vault_prices_df.index, pd.DatetimeIndex), f"Expected DatetimeIndex, got: {type(vault_prices_df.index)}"

    vault_data = vault_prices_df
    if ffill:
        vault_data = forward_fill_vault(vault_data)
    vault_data = filter_finite_share_prices(vault_data)

    # Convert pixels to inches (matplotlib uses inches)
    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.patch.set_facecolor("black")

    # Full-extent axis (no margins)
    ax1 = fig.add_axes([0, 0, 1, 1])
    ax1.patch.set_alpha(0.0)
    ax1.plot(vault_data.index, vault_data["share_price"], color="#a6a4a0", linewidth=2)

    # Remove all spines, ticks, labels
    for spine in ax1.spines.values():
        spine.set_visible(False)
    ax1.set_axis_off()
    ax1.margins(x=0, y=0)

    return fig


def render_sparkline_gradient(  # noqa: PLR0917
    vault_prices_df: pd.DataFrame,
    width: int = 300,
    height: int = 300,
    ffill: bool = True,  # noqa: FBT001, FBT002
    line_color: str = "#22B452",
    bg_color: str = "#282827",
    line_width: int = 2,
    margin_ratio: int = 50,
    x_axis_range: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> plt.Figure:
    """Render a sparkline chart with green-to-black gradient fill.

    The optional explicit horizontal range lets callers render a short price
    history within a longer fixed chart period. Matplotlib leaves time before
    the first observation blank instead of stretching the available data to
    fill the canvas.

    :param vault_prices_df:
        Single-vault prices indexed by naive UTC timestamps. ``share_price``
        must contain at least one finite observation.
    :param width:
        Output figure width in pixels.
    :param height:
        Output figure height in pixels.
    :param ffill:
        Forward-fill sparse observations to an hourly line before rendering.
    :param line_color:
        Colour at the top of the gradient fill.
    :param bg_color:
        Figure and axes background colour.
    :param line_width:
        Price line width in pixels.
    :param margin_ratio:
        Vertical margin in pixels relative to the output height.
    :param x_axis_range:
        Optional inclusive chart bounds. The first value must precede the
        second and both must be naive UTC timestamps.
    :return:
        Matplotlib figure ready for PNG or SVG export.
    """

    if x_axis_range is not None:
        assert x_axis_range[0] < x_axis_range[1], f"Invalid sparkline x-axis range: {x_axis_range}"

    vault_data = vault_prices_df

    if ffill:
        vault_data = forward_fill_vault(vault_data)

    vault_data = filter_finite_share_prices(vault_data)

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
    margin_ratio /= height
    y_margin = y_range * margin_ratio

    # Apply equal vertical margins.
    y_min_with_margin = y_min - y_margin
    y_max_with_margin = y_max + y_margin

    # Constant-price series produce identical y limits. Matplotlib expands them
    # automatically; the warning is expected and does not affect the image.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ax1.set_ylim(y_min_with_margin, y_max_with_margin)

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
    ax1.plot(vault_data.index, vault_data["share_price"], color="#00ff88", linewidth=line_width, zorder=2)

    for spine in ax1.spines.values():
        spine.set_visible(False)
    ax1.set_axis_off()
    ax1.margins(x=0, y=0)
    if x_axis_range is not None:
        ax1.set_xlim(x_axis_range)

    return fig


def export_sparkline_as_png(
    fig: plt.Figure,
) -> bytes:
    """Render a sparkline chart and return as PNG bytes."""

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=100, transparent=False)
    plt.close(fig)
    return buffer.getvalue()


def export_sparkline_as_svg(
    fig: plt.Figure,
) -> bytes:
    """Render a sparkline chart and return as SVG bytes."""

    buffer = BytesIO()
    # Matplotlib otherwise embeds the current time and randomises SVG element
    # identifiers, making an unchanged chart look different to R2 on each run.
    with mpl.rc_context({"svg.hashsalt": "eth-defi-sparkline"}):
        fig.savefig(buffer, format="svg", transparent=True, metadata={"Date": None})
    plt.close(fig)
    return buffer.getvalue()


def upload_to_r2_compressed(  # noqa: PLR0917
    payload: bytes,
    bucket_name: str,
    object_name: str,
    endpoint_url: str,
    access_key_id: str,
    secret_access_key: str,
    content_type: str,
    skip_if_current: bool = False,  # noqa: FBT001, FBT002
) -> bool:
    """Upload a gzip-compressed sparkline image to Cloudflare R2.

    The source checksum is calculated before compression so deterministic
    chart bytes can skip an unchanged remote object.

    :param payload: The bytes data to upload.
    :param bucket_name: The name of the R2 bucket.
    :param object_name: The destination object name (e.g., "my-image.png").
    :param access_key_id: Your R2 access key ID.
    :param secret_access_key: Your R2 secret access key.
    :param content_type: The MIME type of the file.
    :param skip_if_current: Skip upload if the remote object already matches the local source payload.
    :return: ``True`` if uploaded, ``False`` if skipped as unchanged.
    """
    s3_client = create_r2_client(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )

    return upload_bytes_to_r2(
        s3_client=s3_client,
        payload=gzip.compress(payload, mtime=0),
        bucket_name=bucket_name,
        object_name=object_name,
        content_type=content_type,
        content_encoding="gzip",
        skip_if_current=skip_if_current,
        source_digest=calculate_bytes_digest(payload),
    )
