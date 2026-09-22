"""Regression tests for vault sparkline preparation and rendering."""

from io import BytesIO
from xml.etree import ElementTree as ET  # noqa: S405

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import pytest
from PIL import Image

from eth_defi.research import sparkline
from eth_defi.research.sparkline import (
    SPARKLINE_PNG_HEIGHT,
    SPARKLINE_PNG_WIDTH,
    SPARKLINE_SVG_HEIGHT,
    SPARKLINE_SVG_WIDTH,
    export_sparkline_as_png,
    export_sparkline_as_svg,
    prepare_sparkline_data,
    render_sparkline_gradient,
    render_sparkline_png,
    render_sparkline_svg,
)


def test_png_gradient_pixels_are_cached() -> None:
    """Repeated renders reuse the immutable gradient pixel cache."""
    sparkline._cached_sparkline_gradient.cache_clear()
    try:
        first = sparkline._cached_sparkline_gradient(600, 600, (34, 180, 82), (40, 40, 39))
        second = sparkline._cached_sparkline_gradient(600, 600, (34, 180, 82), (40, 40, 39))

        assert first == second
        assert sparkline._cached_sparkline_gradient.cache_info().hits == 1
    finally:
        sparkline._cached_sparkline_gradient.cache_clear()


def test_gradient_sparkline_ignores_nullable_share_prices() -> None:
    """Nullable share prices do not reach Matplotlib's y-axis bounds."""
    index = pd.date_range("2026-07-01", periods=3, freq="D")
    vault_prices_df = pd.DataFrame(
        {
            "share_price": pd.Series([pd.NA, 1.0, 1.01], index=index, dtype="object"),
            "total_assets": [np.nan, 10_000.0, 10_100.0],
        },
        index=index,
    )

    fig = render_sparkline_gradient(vault_prices_df, ffill=False)
    png = export_sparkline_as_png(fig)

    assert png.startswith(b"\x89PNG")


def test_gradient_sparkline_rejects_vault_without_finite_share_prices() -> None:
    """A completely missing price series has no meaningful sparkline."""
    index = pd.date_range("2026-07-01", periods=2, freq="D")
    vault_prices_df = pd.DataFrame(
        {
            "share_price": pd.Series([pd.NA, pd.NA], index=index, dtype="object"),
            "total_assets": [10_000.0, 10_000.0],
        },
        index=index,
    )

    with pytest.raises(ValueError, match="without finite share prices"):
        render_sparkline_gradient(vault_prices_df, ffill=False)


def test_gradient_sparkline_svg_is_deterministic() -> None:
    """Identical chart inputs produce identical bytes for upload deduplication."""
    index = pd.date_range("2026-07-01", periods=3, freq="D")
    vault_prices_df = pd.DataFrame(
        {
            "share_price": [1.0, 1.01, 1.02],
            "total_assets": [10_000.0, 10_100.0, 10_200.0],
        },
        index=index,
    )

    first_svg = export_sparkline_as_svg(render_sparkline_gradient(vault_prices_df, ffill=False))
    second_svg = export_sparkline_as_svg(render_sparkline_gradient(vault_prices_df, ffill=False))

    assert first_svg == second_svg


def test_sparkline_requires_two_weeks_of_finite_price_history() -> None:
    """Publish at exactly two weeks, but not before the threshold."""
    start_at = pd.Timestamp("2026-08-01 12:00:00")
    index = pd.DatetimeIndex(
        [start_at, start_at + pd.Timedelta(days=13), start_at + pd.Timedelta(days=14)],
        name="timestamp",
    )
    prices_df = pd.DataFrame(
        {"share_price": [1.0, float("inf"), 1.02], "total_assets": [10_000.0, 10_100.0, 10_200.0]},
        index=index,
    )

    eligible = prepare_sparkline_data(prices_df)

    assert eligible is not None
    assert eligible.end_at - eligible.prices_df.index[0] == pd.Timedelta(days=14)
    assert prepare_sparkline_data(prices_df.iloc[:2]) is None


def test_short_sparkline_uses_full_90_day_axis_with_blank_left_side() -> None:
    """A new vault's two-week line occupies only the chart's right edge."""
    end_at = pd.Timestamp("2026-08-15 12:00:00")
    index = pd.date_range(end=end_at, periods=15, freq="D", name="timestamp")
    prices_df = pd.DataFrame(
        {"share_price": [1 + day / 1_000 for day in range(len(index))], "total_assets": [10_000.0] * len(index)},
        index=index,
    )
    sparkline_data = prepare_sparkline_data(prices_df)
    assert sparkline_data is not None

    fig = render_sparkline_gradient(
        sparkline_data.prices_df,
        x_axis_range=(sparkline_data.start_at, sparkline_data.end_at),
    )
    axis_start, axis_end = (pd.Timestamp(mdates.num2date(value)).tz_localize(None) for value in fig.axes[0].get_xlim())
    plotted_start = pd.Timestamp(fig.axes[0].lines[0].get_xdata()[0])
    plotted_end = pd.Timestamp(fig.axes[0].lines[0].get_xdata()[-1])

    assert sparkline_data.start_at == end_at.normalize() - pd.Timedelta(days=90)
    assert sparkline_data.prices_df.index[0] == (end_at - pd.Timedelta(days=14)).normalize()
    assert axis_start.floor("s") == sparkline_data.start_at
    assert axis_end.floor("s") == sparkline_data.end_at
    assert plotted_start == sparkline_data.prices_df.index[0]
    assert plotted_end == sparkline_data.end_at
    export_sparkline_as_png(fig)


def test_established_sparse_sparkline_carries_price_to_window_start() -> None:
    """Retain a pre-window value when an old vault has sparse scans."""
    index = pd.DatetimeIndex([pd.Timestamp("2025-01-01"), pd.Timestamp("2026-01-01")], name="timestamp")
    prices_df = pd.DataFrame({"share_price": [1.0, 1.1], "total_assets": [10_000.0, 11_000.0]}, index=index)

    sparkline_data = prepare_sparkline_data(prices_df)

    assert sparkline_data is not None
    assert sparkline_data.start_at == pd.Timestamp("2025-10-03")
    assert sparkline_data.prices_df.index.tolist() == [sparkline_data.start_at, sparkline_data.end_at]
    assert sparkline_data.prices_df["share_price"].tolist() == [1.0, 1.1]


def test_sparkline_preparation_sorts_input_and_rejects_missing_prices() -> None:
    """Handle unordered source rows and nullable data without rendering errors."""
    index = pd.DatetimeIndex([pd.Timestamp("2026-01-15 12:00:00"), pd.Timestamp("2026-01-01 12:00:00")], name="timestamp")
    unordered_prices_df = pd.DataFrame(
        {"share_price": [1.1, 1.0], "total_assets": [11_000.0, 10_000.0]},
        index=index,
    )
    missing_prices_df = unordered_prices_df.assign(share_price=pd.Series([pd.NA, pd.NA], index=index, dtype="Float64"))

    prepared = prepare_sparkline_data(unordered_prices_df)

    assert prepared is not None
    assert prepared.prices_df.index.is_monotonic_increasing
    assert prepare_sparkline_data(missing_prices_df) is None


def test_direct_renderers_handle_constant_series_and_keep_dimensions() -> None:
    """Constant-price charts omit the degenerate area and remain deterministic."""
    index = pd.date_range("2026-07-01", periods=3, freq="D", name="timestamp")
    prices_df = pd.DataFrame({"share_price": [1.0, 1.0, 1.0]}, index=index)

    first_svg = render_sparkline_svg(prices_df)
    second_svg = render_sparkline_svg(prices_df)
    first_png = render_sparkline_png(prices_df)
    second_png = render_sparkline_png(prices_df)

    assert first_svg == second_svg
    assert first_png == second_png
    root = ET.fromstring(first_svg)  # noqa: S314
    assert root.attrib["width"] == str(SPARKLINE_SVG_WIDTH)
    assert root.attrib["height"] == str(SPARKLINE_SVG_HEIGHT)
    assert not any(element.tag.endswith("path") and element.attrib.get("fill") == "url(#sparkline-gradient)" for element in root)
    with Image.open(BytesIO(first_png)) as image:
        assert image.size == (SPARKLINE_PNG_WIDTH, SPARKLINE_PNG_HEIGHT)


def test_svg_gradient_uses_full_canvas_coordinates() -> None:
    """Keep SVG gradient interpolation aligned with the PNG renderer."""
    index = pd.date_range("2026-07-01", periods=3, freq="D", name="timestamp")
    prices_df = pd.DataFrame({"share_price": [1.0, 1.01, 1.02]}, index=index)

    root = ET.fromstring(render_sparkline_svg(prices_df))  # noqa: S314
    gradient = next(element for element in root.iter() if element.tag.endswith("linearGradient"))

    assert gradient.attrib["gradientUnits"] == "userSpaceOnUse"
    assert gradient.attrib["x1"] == "0"
    assert gradient.attrib["y1"] == "0"
    assert gradient.attrib["x2"] == "0"
    assert gradient.attrib["y2"] == str(SPARKLINE_SVG_HEIGHT)


def test_direct_renderers_use_sparse_step_paths_and_support_one_point() -> None:
    """Direct output preserves step semantics without hourly materialisation."""
    index = pd.DatetimeIndex([pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-03")], name="timestamp")
    prices_df = pd.DataFrame({"share_price": [1.0, 1.2]}, index=index)
    svg = render_sparkline_svg(prices_df)
    assert b" H " in svg
    assert b" V " in svg
    one_point = render_sparkline_svg(pd.DataFrame({"share_price": [1.0]}, index=pd.DatetimeIndex([index[0]], name="timestamp")))
    assert b"M 0" in one_point


def test_direct_renderers_normalise_parquet_datetime_resolution() -> None:
    """Microsecond Parquet timestamps still span the full chart width."""
    index = pd.DatetimeIndex(pd.date_range("2026-07-01", periods=16, freq="D"), dtype="datetime64[us]", name="timestamp")
    prepared = prepare_sparkline_data(pd.DataFrame({"share_price": [1.0 + i / 100 for i in range(16)]}, index=index))

    assert prepared is not None
    coordinates = sparkline._calculate_sparkline_coordinates(prepared, width=SPARKLINE_SVG_WIDTH, height=SPARKLINE_SVG_HEIGHT, margin_ratio=4)
    assert coordinates.points[0][0] > 0.0
    assert coordinates.points[-1][0] == pytest.approx(SPARKLINE_SVG_WIDTH)
