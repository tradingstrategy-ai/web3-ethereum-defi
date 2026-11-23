"""Regression tests for vault sparkline preparation and rendering."""

import math
from io import BytesIO
from xml.etree import ElementTree as ET  # noqa: S405

import numpy as np
import pandas as pd
import pytest
from PIL import Image, ImageColor

from eth_defi.research import sparkline
from eth_defi.research.sparkline import (
    SPARKLINE_PNG_HEIGHT,
    SPARKLINE_PNG_WIDTH,
    SPARKLINE_SVG_HEIGHT,
    SPARKLINE_SVG_WIDTH,
    prepare_sparkline_data,
    render_sparkline_png,
    render_sparkline_svg,
)


def test_established_sparse_sparkline_carries_price_to_window_start() -> None:
    """Retain a pre-window value when an old vault has sparse scans."""
    index = pd.DatetimeIndex([pd.Timestamp("2025-01-01"), pd.Timestamp("2026-01-01")], name="timestamp")
    prices_df = pd.DataFrame({"share_price": [1.0, 1.1], "total_assets": [10_000.0, 11_000.0]}, index=index)

    sparkline_data = prepare_sparkline_data(prices_df)

    assert sparkline_data is not None
    assert sparkline_data.start_at == pd.Timestamp("2025-10-03")
    assert sparkline_data.prices_df.index.tolist() == [sparkline_data.start_at, sparkline_data.end_at]
    assert sparkline_data.prices_df["share_price"].tolist() == [1.0, 1.1]


def test_square_png_preserves_pixels_and_gradient(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render a square PNG with lossless pixels and a seamless gradient."""
    index = pd.date_range("2026-07-01", periods=3, freq="D", name="timestamp")
    prices_df = pd.DataFrame({"share_price": [1.0, 1.01, 1.02]}, index=index)
    encode = sparkline.fpng.from_ndarray
    source_pixels: list[np.ndarray] = []

    def capture_pixels(pixels: np.ndarray) -> bytes:
        source_pixels.append(pixels.copy())
        return encode(pixels)

    monkeypatch.setattr(sparkline.fpng, "from_ndarray", capture_pixels)

    payload = render_sparkline_png(prices_df)
    coordinates = sparkline._calculate_sparkline_coordinates(prices_df, width=SPARKLINE_PNG_WIDTH, height=SPARKLINE_PNG_HEIGHT, margin_ratio=sparkline.SPARKLINE_PNG_MARGIN_RATIO)
    background = ImageColor.getrgb(sparkline.SPARKLINE_BACKGROUND_COLOR)
    with Image.open(BytesIO(payload)) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (300, 300)
        assert len(source_pixels) == 1
        np.testing.assert_array_equal(np.asarray(image), source_pixels[0])
        sample_x = 225
        assert image.getpixel((sample_x, math.floor(coordinates.baseline_y) - 1)) == background
        assert image.getpixel((sample_x, math.ceil(coordinates.baseline_y) + 1)) == background

    assert payload == render_sparkline_png(prices_df)


@pytest.mark.parametrize(
    "days, prices",
    [
        pytest.param([0, 5, 10], [1.0, 1.4, 1.2], id="short"),
        pytest.param([0, 45, 90], [1.0, 1.0, 1.0], id="constant"),
    ],
)
def test_svg_and_table_png_preserve_geometry(days: list[int], prices: list[float]) -> None:
    """Native table PNGs retain time coverage, steps, padding and stroke weight.

    Decode actual raster pixels, including blank history, gradient and the
    centre-line stroke. These checks catch a square PNG squeezed into a 4:1
    canvas even when its final dimensions are correct.

    :param days:
        Elapsed observation days from launch.
    :param prices:
        Share prices at the observations.
    """
    index = pd.DatetimeIndex([pd.Timestamp("2026-01-01") + pd.Timedelta(days=day) for day in days], dtype="datetime64[us]")
    data = prepare_sparkline_data(pd.DataFrame({"share_price": prices}, index=index))
    assert data is not None
    window_days = 90
    green_threshold = 150
    svg = render_sparkline_svg(data)
    assert svg == render_sparkline_svg(data)
    root = ET.fromstring(svg)  # noqa: S314
    assert root.attrib["width"] == str(SPARKLINE_SVG_WIDTH)
    assert root.attrib["height"] == str(SPARKLINE_SVG_HEIGHT)
    payload = sparkline.render_sparkline_table_png(svg)
    assert payload == sparkline.render_sparkline_table_png(data)
    background = ImageColor.getrgb(sparkline.SPARKLINE_BACKGROUND_COLOR)
    with Image.open(BytesIO(payload)) as image:
        assert image.format == "PNG"
        assert image.size == (300, 75)
        image = image.convert("RGB")
        if days[-1] < window_days:
            assert image.crop((0, 0, 250, 75)).getextrema() == tuple((channel, channel) for channel in background)
        if len(set(prices)) == 1:
            assert not any(element.get("fill") == "url(#sparkline-gradient)" for element in root)
            green_rows = [y for y in range(75) if image.getpixel((150, y))[1] > green_threshold]
            assert green_rows == [36, 37, 38]
            assert image.getpixel((150, 20)) == background
            assert image.getpixel((150, 50)) == background
        else:
            # A 4/25 margin gives approximately 9 px of padding on a 75 px canvas.
            first_x = round(300 * (1 - days[-1] / 90))
            step_x = round(300 * (1 - (days[-1] - days[1]) / 90))
            low_x = (first_x + step_x) // 2
            high_x = (step_x + 300) // 2
            assert image.getpixel((low_x, 65))[1] > green_threshold
            assert image.getpixel((high_x, 9))[1] > green_threshold
            assert image.getpixel((low_x, 40)) == background
            assert image.getpixel((high_x, 30))[1] > background[1]
            assert image.getpixel((high_x, 70)) == background
