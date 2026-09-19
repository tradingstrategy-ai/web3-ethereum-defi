"""Regression tests for vault sparkline preparation and rendering."""

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import pytest

from eth_defi.research.sparkline import export_sparkline_as_png, export_sparkline_as_svg, prepare_sparkline_data, render_sparkline_gradient


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
