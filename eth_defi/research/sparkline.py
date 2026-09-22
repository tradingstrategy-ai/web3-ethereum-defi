"""Prepare, render and upload share-price sparklines for vault data."""

from __future__ import annotations

import gzip
import math
import warnings
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageColor, ImageDraw

from eth_defi.cloudflare_r2 import calculate_bytes_digest, create_r2_client, upload_bytes_to_r2
from eth_defi.vault.base import VaultSpec

#: Width of the time axis used by published vault sparklines.
DEFAULT_SPARKLINE_WINDOW = pd.Timedelta(days=90)

#: Minimum elapsed finite share-price history required for publication.
MIN_SPARKLINE_HISTORY = pd.Timedelta(days=14)

#: Versioned visual contract used by canonical input digests and state.
SPARKLINE_RENDERER_VERSION = 1

#: Public sparkline dimensions.
SPARKLINE_SVG_WIDTH = 100
SPARKLINE_SVG_HEIGHT = 25
SPARKLINE_PNG_WIDTH = 300
SPARKLINE_PNG_HEIGHT = 300

#: Stable visual style constants shared by direct renderers.
SPARKLINE_LINE_COLOR = "#00ff88"
SPARKLINE_GRADIENT_TOP_COLOR = "#22B452"
SPARKLINE_BACKGROUND_COLOR = "#282827"
SPARKLINE_GRADIENT_ALPHA = 0.4
SPARKLINE_LINE_WIDTH = 2
SPARKLINE_SVG_LINE_WIDTH = 1
SPARKLINE_SVG_MARGIN_RATIO = 4
SPARKLINE_PNG_MARGIN_RATIO = 50

#: Tolerance used to canonicalise SVG negative zero values.
SPARKLINE_SVG_NEGATIVE_ZERO_EPSILON = 0.0005


@dataclass(frozen=True, slots=True)
class SparklineCoordinates:
    """Pixel coordinates shared by deterministic SVG and PNG renderers."""

    #: Ordered observation points as ``(x, y)`` pixels.
    points: tuple[tuple[float, float], ...]

    #: Baseline used by the non-constant gradient area.
    baseline_y: float

    #: Whether every finite share price has the same value.
    is_constant: bool


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


def _render_input(
    vault_prices_df: pd.DataFrame | SparklineData,
    x_axis_range: tuple[pd.Timestamp, pd.Timestamp] | None,
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """Resolve render rows and stable chart bounds from one input object.

    :param vault_prices_df:
        Prepared SparklineData or a single-vault price frame.
    :param x_axis_range:
        Optional explicit chart bounds for legacy callers.
    :return:
        Finite sorted rows and inclusive naive UTC chart bounds.
    :raises ValueError:
        If the input has no finite prices or invalid bounds.
    """
    if isinstance(vault_prices_df, SparklineData):
        prices_df = vault_prices_df.prices_df
        start_at = vault_prices_df.start_at
        end_at = vault_prices_df.end_at
    else:
        prices_df = vault_prices_df
        if x_axis_range is None:
            finite = filter_finite_share_prices(prices_df).sort_index()
            start_at = finite.index[0]
            end_at = finite.index[-1]
        else:
            start_at, end_at = x_axis_range

    if x_axis_range is not None:
        start_at, end_at = x_axis_range
    start_at = pd.Timestamp(start_at)
    end_at = pd.Timestamp(end_at)
    if start_at.tzinfo is not None or end_at.tzinfo is not None:
        message = "Sparkline bounds must be naive UTC timestamps"
        raise ValueError(message)
    if end_at < start_at:
        raise ValueError(f"Sparkline bounds must be increasing: {start_at!s} >= {end_at!s}")
    if end_at == start_at:
        end_at = start_at + pd.Timedelta(days=1)
    return filter_finite_share_prices(prices_df).sort_index(), start_at, end_at


def _calculate_sparkline_coordinates(  # noqa: PLR0914
    vault_prices_df: pd.DataFrame | SparklineData,
    *,
    width: int,
    height: int,
    margin_ratio: int,
    x_axis_range: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> SparklineCoordinates:
    """Map sparse daily observations to renderer-independent pixel points.

    The mapping uses the supplied 90-day bounds rather than the observation
    extent. Exact constant ranges are placed on the centre line and never
    produce a degenerate gradient extent.

    :param vault_prices_df:
        Prepared sparkline data or a finite single-vault price frame.
    :param width:
        Output width in pixels.
    :param height:
        Output height in pixels.
    :param margin_ratio:
        Vertical margin in pixels relative to the output height.
    :param x_axis_range:
        Optional explicit bounds for a raw price frame.
    :return:
        Pixel coordinates and gradient baseline.
    """
    assert width > 0 and height > 0, f"Sparkline dimensions must be positive: {width}x{height}"
    prices_df, start_at, end_at = _render_input(vault_prices_df, x_axis_range)
    values = prices_df["share_price"].to_numpy(dtype=float)
    if values.size == 0:
        message = "Cannot render sparkline without finite share prices"
        raise ValueError(message)
    start_ns = start_at.value
    end_ns = end_at.value
    span_ns = end_ns - start_ns
    x_values = np.clip((prices_df.index.view("int64") - start_ns) / span_ns * width, 0.0, float(width))
    y_min = float(np.min(values))
    y_max = float(np.max(values))
    y_range = y_max - y_min
    if y_range == 0.0:
        y_values = np.full(values.shape, height / 2.0, dtype=float)
        baseline_y = height / 2.0
        is_constant = True
    else:
        margin = y_range * (margin_ratio / height)
        lower = y_min - margin
        upper = y_max + margin
        y_values = (upper - values) / (upper - lower) * height
        baseline_y = (upper - y_min) / (upper - lower) * height
        is_constant = False

    points = tuple((float(x), float(y)) for x, y in zip(x_values, y_values, strict=True))
    return SparklineCoordinates(points=points, baseline_y=float(baseline_y), is_constant=is_constant)


def _step_line_points(coordinates: SparklineCoordinates, *, width: float | None = None) -> tuple[tuple[float, float], ...]:
    """Expand observation points into horizontal-then-vertical step points."""
    points = coordinates.points
    if not points:
        return ()
    expanded: list[tuple[float, float]] = [points[0]]
    for previous, current in zip(points, points[1:], strict=False):
        expanded.append((current[0], previous[1]))
        expanded.append(current)
    if len(expanded) == 1:
        _, y = expanded[0]
        max_x = float(width) if width is not None else float(SPARKLINE_PNG_WIDTH)
        expanded = [(0.0, y), (max_x, y)]
    return tuple(expanded)


def _format_svg_number(value: float) -> str:
    """Format one SVG coordinate without locale or negative-zero drift."""
    if not math.isfinite(value):
        raise ValueError(f"Non-finite SVG coordinate: {value!r}")
    if abs(value) < SPARKLINE_SVG_NEGATIVE_ZERO_EPSILON:
        value = 0.0
    formatted = f"{value:.3f}".rstrip("0").rstrip(".")
    return formatted or "0"


def _svg_path(points: tuple[tuple[float, float], ...]) -> str:
    """Encode a step path with stable absolute SVG commands."""
    if not points:
        message = "Cannot encode an empty sparkline path"
        raise ValueError(message)
    commands = [f"M {_format_svg_number(points[0][0])} {_format_svg_number(points[0][1])}"]
    for x, y in points[1:]:
        commands.append(f"H {_format_svg_number(x)}")
        commands.append(f"V {_format_svg_number(y)}")
    return " ".join(commands)


def _svg_area_path(coordinates: SparklineCoordinates, line_points: tuple[tuple[float, float], ...]) -> str:
    """Encode the step path closed against the data-space baseline."""
    first_x = line_points[0][0]
    last_x = line_points[-1][0]
    line_path = _svg_path(line_points)
    return f"M {_format_svg_number(first_x)} {_format_svg_number(coordinates.baseline_y)} L {_format_svg_number(first_x)} {_format_svg_number(line_points[0][1])} {line_path[2:]} L {_format_svg_number(last_x)} {_format_svg_number(coordinates.baseline_y)} Z"


def render_sparkline_svg(
    vault_prices_df: pd.DataFrame | SparklineData,
    *,
    width: int = SPARKLINE_SVG_WIDTH,
    height: int = SPARKLINE_SVG_HEIGHT,
    line_width: int = SPARKLINE_SVG_LINE_WIDTH,
    margin_ratio: int = SPARKLINE_SVG_MARGIN_RATIO,
    line_color: str = SPARKLINE_GRADIENT_TOP_COLOR,
    bg_color: str = SPARKLINE_BACKGROUND_COLOR,
    x_axis_range: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> bytes:
    """Render a deterministic sparse step sparkline as native SVG bytes.

    :param vault_prices_df:
        Prepared sparkline data or a finite single-vault price frame.
    :param width:
        SVG width in pixels.
    :param height:
        SVG height in pixels.
    :param line_width:
        Price-line width in pixels.
    :param margin_ratio:
        Vertical margin in pixels relative to the output height.
    :param line_color:
        Top colour of the translucent area gradient.
    :param bg_color:
        Background colour.
    :param x_axis_range:
        Optional explicit chart bounds for raw price frames.
    :return:
        Stable UTF-8 SVG bytes.
    """
    coordinates = _calculate_sparkline_coordinates(
        vault_prices_df,
        width=width,
        height=height,
        margin_ratio=margin_ratio,
        x_axis_range=x_axis_range,
    )
    line_points = _step_line_points(coordinates, width=width)
    line_path = _svg_path(line_points)
    area_path = _svg_area_path(coordinates, line_points) if not coordinates.is_constant else ""
    area_element = "" if coordinates.is_constant else f'<path d="{area_path}" fill="url(#sparkline-gradient)" />'
    svg = f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><defs><linearGradient id="sparkline-gradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="{line_color}" stop-opacity="{SPARKLINE_GRADIENT_ALPHA:.3f}" /><stop offset="100%" stop-color="{bg_color}" stop-opacity="{SPARKLINE_GRADIENT_ALPHA:.3f}" /></linearGradient></defs><rect x="0" y="0" width="{width}" height="{height}" fill="{bg_color}" />{area_element}<path d="{line_path}" fill="none" stroke="{SPARKLINE_LINE_COLOR}" stroke-width="{line_width}" stroke-linecap="round" stroke-linejoin="round" /></svg>\n'
    return svg.encode("utf-8")


@lru_cache(maxsize=4)
def _cached_sparkline_gradient(
    scaled_width: int,
    scaled_height: int,
    top_colour: tuple[int, int, int],
    background: tuple[int, int, int],
) -> bytes:
    """Build one immutable supersampled gradient and cache its raw pixels.

    The gradient is independent of vault prices; only the supersampled output
    dimensions and the two colours affect its pixels. Returning raw bytes keeps
    the cached value immutable and gives each renderer its own Pillow image,
    which is safe when several sparkline workers render concurrently.

    :param scaled_width:
        Supersampled image width in pixels.
    :param scaled_height:
        Supersampled image height in pixels.
    :param top_colour:
        RGB colour at the top of the gradient.
    :param background:
        RGB colour at the bottom of the gradient.
    :return:
        Raw RGB pixel bytes for a ``scaled_width`` by ``scaled_height`` image.
    """
    denominator = max(1, scaled_height - 1)
    gradient = Image.new("RGB", (1, scaled_height))
    gradient.putdata(tuple(tuple(round(top_colour[channel] * (1.0 - row / denominator) + background[channel] * (row / denominator)) for channel in range(3)) for row in range(scaled_height)))
    return gradient.resize((scaled_width, scaled_height), Image.Resampling.NEAREST).tobytes()


def render_sparkline_png(  # noqa: PLR0914
    vault_prices_df: pd.DataFrame | SparklineData,
    *,
    width: int = SPARKLINE_PNG_WIDTH,
    height: int = SPARKLINE_PNG_HEIGHT,
    line_width: int = SPARKLINE_LINE_WIDTH,
    margin_ratio: int = SPARKLINE_PNG_MARGIN_RATIO,
    line_color: str = SPARKLINE_GRADIENT_TOP_COLOR,
    bg_color: str = SPARKLINE_BACKGROUND_COLOR,
    x_axis_range: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> bytes:
    """Render a deterministic two-times-supersampled Pillow PNG.

    :param vault_prices_df:
        Prepared sparkline data or a finite single-vault price frame.
    :param width:
        Final PNG width in pixels.
    :param height:
        Final PNG height in pixels.
    :param line_width:
        Final price-line width in pixels.
    :param margin_ratio:
        Vertical margin in pixels relative to the output height.
    :param line_color:
        Top colour of the translucent area gradient.
    :param bg_color:
        Background colour.
    :param x_axis_range:
        Optional explicit chart bounds for raw price frames.
    :return:
        Stable PNG bytes.
    """
    scale = 2
    coordinates = _calculate_sparkline_coordinates(
        vault_prices_df,
        width=width,
        height=height,
        margin_ratio=margin_ratio,
        x_axis_range=x_axis_range,
    )
    line_points = _step_line_points(coordinates, width=width)
    scaled_width = width * scale
    scaled_height = height * scale
    background = ImageColor.getrgb(bg_color)
    top_colour = ImageColor.getrgb(line_color)
    line_colour = ImageColor.getrgb(SPARKLINE_LINE_COLOR)
    image = Image.new("RGB", (scaled_width, scaled_height), background)

    if not coordinates.is_constant:
        gradient = Image.frombytes(
            "RGB",
            (scaled_width, scaled_height),
            _cached_sparkline_gradient(scaled_width, scaled_height, top_colour, background),
        )
        mask = Image.new("L", (scaled_width, scaled_height), 0)
        mask_points = [(round(x * scale), round(y * scale)) for x, y in line_points]
        first_x, _ = mask_points[0]
        last_x, _ = mask_points[-1]
        mask_points.extend(
            [
                (last_x, round(coordinates.baseline_y * scale)),
                (first_x, round(coordinates.baseline_y * scale)),
            ]
        )
        ImageDraw.Draw(mask).polygon(mask_points, fill=round(255 * SPARKLINE_GRADIENT_ALPHA))
        image.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(image)
    draw_points = [(round(x * scale), round(y * scale)) for x, y in line_points]
    draw.line(draw_points, fill=line_colour, width=max(1, round(line_width * scale)), joint="curve")
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


def render_sparkline_simple(
    vault_prices_df: pd.DataFrame,
    width: int = 256,
    height: int = 64,
    ffill: bool = True,  # noqa: FBT001, FBT002
) -> Any:
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
    import matplotlib.pyplot as plt  # noqa: PLC0415

    from eth_defi.research.wrangle_vault_prices import forward_fill_vault  # noqa: PLC0415

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
) -> Any:
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

    import matplotlib.pyplot as plt  # noqa: PLC0415

    from eth_defi.research.wrangle_vault_prices import forward_fill_vault  # noqa: PLC0415

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
    if y_range == 0:
        # Matplotlib cannot resample a zero-height image extent. Keep the
        # visible line centred on a small synthetic range and omit the area.
        y_margin = max(abs(float(y_min)) * margin_ratio, 1e-9)

    # Apply equal vertical margins.
    y_min_with_margin = y_min - y_margin
    y_max_with_margin = y_max + y_margin

    # Constant-price series produce identical y limits. Matplotlib expands them
    # automatically; the warning is expected and does not affect the image.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ax1.set_ylim(y_min_with_margin, y_max_with_margin)

    if y_range != 0:
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
    fig: Any,
) -> bytes:
    """Render a sparkline chart and return as PNG bytes."""

    import matplotlib.pyplot as plt  # noqa: PLC0415

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=100, transparent=False)
    plt.close(fig)
    return buffer.getvalue()


def export_sparkline_as_svg(
    fig: Any,
) -> bytes:
    """Render a sparkline chart and return as SVG bytes."""

    import matplotlib as mpl  # noqa: PLC0415
    import matplotlib.pyplot as plt  # noqa: PLC0415

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
