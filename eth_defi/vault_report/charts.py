"""Static charts for the monthly vault report.

Charts are Plotly figures rendered to PNG by
`Kaleido <https://github.com/plotly/Kaleido>`__ with headless Chrome, then framed
by :py:mod:`eth_defi.vault_report.branding`. Styling follows
:py:mod:`eth_defi.vault_report.theme`:

- At most eight categorical series, in the theme's fixed colour order
- Legends with protocol logos instead of plain colour boxes
- Benchmarks matching the vaults' activity: the US Treasury bill for calm
  yield vaults, BTC and ETH for trading and volatile vaults, see
  :py:mod:`eth_defi.vault_report.benchmarks`
- Performance as equity curves in percent of all compared vaults in one chart
  with a shared axis, yields as dots on a rate scale, and dollar TVL changes as
  diverging bars
- Glowing lines for charts with few series, like the website's hero charts
"""

import base64
import datetime
import io
import logging
import os
import tempfile
import textwrap
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from PIL import Image, ImageFont
from plotly.graph_objects import Figure

from eth_defi.vault_report.benchmarks import BTC, ETH, TREASURY_BILL
from eth_defi.vault_report.sections import CAPPED_ANNUALISED_RETURN
from eth_defi.vault_report.theme import ASSETS_DIR, FONT_REGULAR, ChartTheme, apply_theme

logger = logging.getLogger(__name__)

#: Rendered chart width. The blog shows images at 720 px width, so charts are rendered at roughly 2×.
IMAGE_WIDTH = 1400

#: Default rendered chart height
IMAGE_HEIGHT = 800

#: Chrome downloaded by ``plotly_get_chrome`` / ``kaleido_get_chrome`` on Linux
CHOREOGRAPHER_CHROME_PATH = Path("~/.local/share/choreographer/deps/chrome-linux64/chrome").expanduser()

#: Width of the right margin that holds a logo legend
LEGEND_MARGIN = 430


#: Font size of the curator, protocol and chain row under a vault name, in pixels
PROPERTY_FONT_SIZE = 14

#: Icon size in the property row, in pixels
PROPERTY_ICON_SIZE = 17

#: Height of one property row, in pixels
PROPERTY_ROW_HEIGHT = 21


@dataclass(slots=True, frozen=True)
class VaultProperty:
    """A vault property shown under the vault name: its curator, protocol or chain.

    Properties are drawn in the order curator, protocol, chain, each with its
    own icon, see :py:func:`add_property_rows`.
    """

    #: Label text, e.g. ``Steakhouse Financial``, ``Morpho`` or ``Base``
    text: str

    #: Logo data URI, or ``None`` to draw the text only
    logo_uri: str | None = None


@dataclass(slots=True, frozen=True)
class LegendEntry:
    """One entry of a custom chart legend."""

    #: Label text
    label: str

    #: Series colour
    colour: str

    #: Protocol or chain logo data URI, or ``None``
    logo_uri: str | None = None

    #: Plotly line dash style of the swatch
    dash: str = "solid"

    #: Muted last line, e.g. the final return
    detail: str | None = None

    #: Curator, protocol and chain drawn with their icons under the label
    properties: tuple[VaultProperty, ...] = ()


def to_rgba(colour: str, alpha: float) -> str:
    """Convert ``#rrggbb`` to a CSS ``rgba()`` colour.

    :param colour:
        Hex colour.

    :param alpha:
        Opacity, 0-1.

    :return:
        ``rgba(r,g,b,a)`` string.
    """
    colour = colour.lstrip("#")
    red, green, blue = (int(colour[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({red},{green},{blue},{alpha})"


def wrap_label(text: str, width: int) -> str:
    """Word-wrap a chart label to Plotly ``<br>`` lines without truncating it.

    :param text:
        Label.

    :param width:
        Maximum characters per line.

    :return:
        Label with ``<br>`` line breaks.
    """
    return "<br>".join(textwrap.wrap(text, width=width)) or text


def _measure_text(text: str, size: int = PROPERTY_FONT_SIZE) -> float:
    """Measure text width in pixels with the bundled chart font.

    :param text:
        Text.

    :param size:
        Font size in pixels.

    :return:
        Width in pixels.
    """
    return ImageFont.truetype(str(FONT_REGULAR), size).getlength(text)


def layout_properties(properties: tuple[VaultProperty, ...], width: float) -> list[list[tuple[VaultProperty, float]]]:
    """Flow vault properties into rows that fit a width.

    :param properties:
        Properties in drawing order.

    :param width:
        Available width in pixels.

    :return:
        Rows of ``(property, x offset in pixels)``.
    """
    rows: list[list[tuple[VaultProperty, float]]] = []
    cursor = 0.0
    for prop in properties:
        item_width = (PROPERTY_ICON_SIZE + 5 if prop.logo_uri else 0) + _measure_text(prop.text)
        if not rows or (cursor > 0 and cursor + item_width > width):
            rows.append([])
            cursor = 0.0
        rows[-1].append((prop, cursor))
        cursor += item_width + 16
    return rows


def add_property_rows(
    fig: Figure,
    properties: tuple[VaultProperty, ...],
    theme: ChartTheme,
    x: float,
    y: float,
    yref: str,
    width: float,
    x_pixels: float,
    y_pixels: float,
) -> int:
    """Draw a vault's curator, protocol and chain under its name, each with its icon.

    :param fig:
        Figure to modify.

    :param properties:
        Properties in drawing order.

    :param theme:
        Chart theme.

    :param x:
        Paper x coordinate of the row start.

    :param y:
        Centre of the first row, in ``yref`` units.

    :param yref:
        ``paper`` or ``y``.

    :param width:
        Available width in pixels; properties flow to more rows when needed.

    :param x_pixels:
        Pixels per paper x unit, the plot width.

    :param y_pixels:
        Pixels per ``yref`` unit.

    :return:
        Number of rows drawn.
    """
    rows = layout_properties(properties, width)
    for i, row in enumerate(rows):
        centre = y - i * PROPERTY_ROW_HEIGHT / y_pixels
        for prop, offset in row:
            item_x = x + offset / x_pixels
            if prop.logo_uri:
                fig.add_layout_image(source=prop.logo_uri, xref="paper", yref=yref, x=item_x, y=centre, sizex=PROPERTY_ICON_SIZE / x_pixels, sizey=PROPERTY_ICON_SIZE / y_pixels, xanchor="left", yanchor="middle")
                item_x += (PROPERTY_ICON_SIZE + 5) / x_pixels
            fig.add_annotation(text=prop.text, xref="paper", yref=yref, x=item_x, y=centre, xanchor="left", yanchor="middle", showarrow=False, font={"size": PROPERTY_FONT_SIZE, "color": theme.muted_text})
    return len(rows)


def add_logo_legend(fig: Figure, entries: list[LegendEntry], theme: ChartTheme, top: float = 1.0, row_height: float = 0.088) -> None:
    """Draw a legend with logos in the right margin.

    Plotly legends cannot show images, so the legend is drawn with shapes,
    layout images and annotations. The figure needs a right margin of
    :py:data:`LEGEND_MARGIN` pixels, and its height and margins must be set
    before calling this.

    Labels are word-wrapped to full length. An entry's curator, protocol and
    chain are drawn under its label with their icons, see
    :py:func:`add_property_rows`, and its detail line last. Entries are spaced
    at least ``row_height`` apart, and further when they need more room; the
    figure grows taller rather than letting entries overlap.

    :param fig:
        Figure to modify.

    :param entries:
        Legend entries from top to bottom.

    :param theme:
        Chart theme.

    :param top:
        Paper y coordinate of the first entry.

    :param row_height:
        Minimum paper height of one entry. A legend taller than the plot makes the figure taller.
    """
    fig.update_layout(showlegend=False)
    plot_width = fig.layout.width - fig.layout.margin.l - fig.layout.margin.r
    plot_height = fig.layout.height - fig.layout.margin.t - fig.layout.margin.b
    # Lay the legend out in pixels, then grow the figure if the legend does not fit
    line_pixels, gap_pixels = 22, 18
    # Entries with a logo next to the label, e.g. protocols in the TVL chart, need room for it
    text_x = 1.132 if any(entry.logo_uri for entry in entries) else 1.09
    text_width = fig.layout.margin.r - (text_x - 1) * plot_width - 44
    layouts = []
    for entry in entries:
        lines = textwrap.wrap(entry.label, width=28 if entry.detail or entry.properties else 24) or [entry.label]
        property_rows = len(layout_properties(entry.properties, text_width)) if entry.properties else 0
        height = len(lines) * line_pixels + property_rows * PROPERTY_ROW_HEIGHT + (line_pixels if entry.detail else 0)
        layouts.append((entry, lines, max(row_height * plot_height, height + gap_pixels)))
    needed = sum(pitch for _, _, pitch in layouts) - (1 - top) * plot_height
    if needed > plot_height:
        fig.update_layout(height=fig.layout.height + needed - plot_height)
        plot_height = needed

    # Entries hang from the top of their first line, so wrapped labels grow downwards
    y = top + line_pixels / 2 / plot_height
    for entry, lines, pitch in layouts:
        # The swatch and the logo sit next to the first line
        first_line = y - line_pixels / 2 / plot_height
        fig.add_shape(type="line", xref="paper", yref="paper", x0=1.03, x1=1.075, y0=first_line, y1=first_line, line={"color": entry.colour, "width": 6, "dash": entry.dash})
        if entry.logo_uri:
            fig.add_layout_image(source=entry.logo_uri, xref="paper", yref="paper", x=1.09, y=first_line, sizex=0.034, sizey=34 / plot_height, xanchor="left", yanchor="middle")
        fig.add_annotation(text="<br>".join(lines), xref="paper", yref="paper", x=text_x, y=y, xanchor="left", yanchor="top", align="left", showarrow=False, font={"size": 17, "color": theme.text})
        cursor = y - len(lines) * line_pixels / plot_height
        if entry.properties:
            rows = add_property_rows(fig, entry.properties, theme, text_x, cursor - PROPERTY_ROW_HEIGHT / plot_height / 2, "paper", text_width, plot_width, plot_height)
            cursor -= rows * PROPERTY_ROW_HEIGHT / plot_height
        if entry.detail:
            fig.add_annotation(text=entry.detail, xref="paper", yref="paper", x=text_x, y=cursor, xanchor="left", yanchor="top", align="left", showarrow=False, font={"size": 17, "color": theme.muted_text})
        y -= pitch / plot_height


def add_glow_line(fig: Figure, x: pd.Index, y: np.ndarray, colour: str, name: str) -> None:
    """Draw a line with a soft glow underneath, like the website's hero charts.

    Use only for charts with one to three lines; glow adds clutter to busy charts.

    :param fig:
        Figure to modify.

    :param x:
        X values.

    :param y:
        Y values.

    :param colour:
        Line colour.

    :param name:
        Series name.
    """
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line={"color": to_rgba(colour, 0.18), "width": 14}, hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line={"color": colour, "width": 4}, name=name))


@dataclass(slots=True, frozen=True)
class PerformanceSeries:
    """One vault in a performance comparison chart."""

    #: Vault id, a column of the daily prices
    vault_id: str

    #: Vault name
    name: str

    #: Curator, protocol and chain shown under the name in the legend
    properties: tuple[VaultProperty, ...]

    #: Benchmark names the vault is compared with, see :py:func:`eth_defi.vault_report.benchmarks.select_benchmarks`
    benchmarks: tuple[str, ...]

    #: Rank in the table, shown in the legend and the line end badge. Defaults to the position in the chart.
    rank: int | None = None


#: Benchmark line dash styles. Benchmarks share one neutral colour, so they do not compete with the vault colours.
BENCHMARK_DASHES = {TREASURY_BILL: "dash", BTC: "dot", ETH: "dashdot"}

#: Equity index value at the window start. Lines are plotted as an index, so a log axis works, and labelled in equity %.
EQUITY_CURVE_BASE = 100

#: Candidate y axis ticks of a log-scale equity chart, as equity index values
LOG_SCALE_TICKS = (10, 20, 50, 80, 100, 120, 150, 200, 300, 400, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000)

#: Candidate tick steps of a linear equity chart, in percentage points
LINEAR_TICK_STEPS = (0.2, 0.5, 1, 2, 5, 10, 20, 25, 50, 100, 200, 500)


def calculate_period_performance(series: pd.Series, start_at: pd.Timestamp) -> pd.Series:
    """Calculate cumulative performance since a start date.

    A vault that launched after ``start_at`` starts from its first data point.

    :param series:
        Daily share prices or benchmark values.

    :param start_at:
        Start of the period.

    :return:
        Cumulative return in percent, starting at 0.
    """
    values = series.loc[series.index >= start_at].dropna()
    if values.empty:
        return values
    return (values / values.iloc[0] - 1) * 100


def calculate_rolling_sharpe(prices: pd.Series, window: datetime.timedelta = datetime.timedelta(days=90), min_periods: int = 30) -> pd.Series:
    """Calculate a rolling annualised Sharpe ratio from daily prices.

    Uses the same method as the exported three-month Sharpe ratio, see
    :py:func:`eth_defi.research.vault_metrics.calculate_sharpe_ratio_from_returns`:
    daily percentage returns, mean over standard deviation, annualised with
    365 days and a zero risk-free rate. The input must be forward filled, not
    interpolated, like the exporter's daily prices, so the values match the
    tables' "3M Sharpe" column.

    :param prices:
        Forward-filled daily share prices or benchmark values.

    :param window:
        Rolling window.

    :param min_periods:
        Minimum number of daily returns before a value is drawn.

    :return:
        Rolling Sharpe ratio, without undefined values.
    """
    returns = prices.dropna().pct_change()
    rolling = returns.rolling(window.days, min_periods=min_periods)
    sharpe = rolling.mean() / rolling.std() * np.sqrt(365)
    return sharpe.replace([np.inf, -np.inf], np.nan).dropna()


def create_performance_figure(
    series: list[PerformanceSeries],
    daily_prices: pd.DataFrame,
    benchmark_indices: dict[str, pd.Series],
    theme: ChartTheme,
    window: datetime.timedelta = datetime.timedelta(days=90),
    log_threshold: float = 100.0,
    outlier_ratio: float = 5.0,
    benchmark_logos: dict[str, str | None] | None = None,
    measure: Literal["equity", "sharpe"] = "equity",
    sharpe_window: datetime.timedelta = datetime.timedelta(days=90),
) -> Figure:
    """Draw the equity curves of vaults and their benchmarks in one chart.

    Each line shows the equity change in percent since the start of the window.
    All vaults share the time axis and the equity axis, so their equity curves
    can be compared directly. Vault colours
    follow the table order, and the legend numbers match the table rows. The
    legend and line end labels show annualised returns over each line's span. A vault
    younger than the window starts from 0% at its first data point.

    Benchmarks used by at least half of the vaults, see
    :py:func:`eth_defi.vault_report.benchmarks.select_benchmarks`, are drawn
    once in a neutral colour and rebased to the window start. A single volatile
    vault among calm ones therefore does not pull BTC and ETH into a T-bill chart.

    A vault whose peak return exceeds ``outlier_ratio`` times the median vault
    peak, the benchmark peaks and 10% is drawn off scale: the axis fits the other lines, and
    the outlier leaves the top edge with a marker, repeated as ▲ in the legend.
    Otherwise one anomalous vault would flatten all others.

    When the remaining lines return more than ``log_threshold`` percent, the y
    axis switches to a log scale.

    :param series:
        Vaults in table order, at most as many as the theme has series colours.

    :param daily_prices:
        Output of :py:func:`eth_defi.vault_report.data.calculate_daily_share_prices`.

    :param benchmark_indices:
        Output of :py:func:`eth_defi.vault_report.benchmarks.fetch_benchmark_indices`.

    :param theme:
        Chart theme.

    :param window:
        Performance period.

    :param log_threshold:
        Cumulative return in percent above which the y axis is logarithmic.

    :param outlier_ratio:
        Peak return multiple of the median vault peak above which a vault is drawn off scale.

    :param benchmark_logos:
        Benchmark name -> logo data URI, see :py:func:`eth_defi.vault_report.logos.load_benchmark_logo_uri`.
        Drawn in the legend and at the benchmark line ends.

    :param measure:
        ``equity`` draws equity curves. ``sharpe`` draws the rolling Sharpe ratio
        over ``sharpe_window`` instead, see :py:func:`calculate_rolling_sharpe`;
        ``daily_prices`` must then be forward filled and cover the window before
        the chart start. The legend shows the latest value, and the US Treasury
        bill, which has no volatility, is left out.

    :param sharpe_window:
        Rolling window of the Sharpe ratio.

    :return:
        Plotly figure.
    """
    assert len(series) <= len(theme.series_colours), f"At most {len(theme.series_colours)} vaults fit one chart, got {len(series)}"
    end_at = daily_prices.index.max()
    start_at = end_at - pd.Timedelta(window)

    sharpe = measure == "sharpe"

    def measure_line(values: pd.Series) -> pd.Series:
        # Equity change in percent, or the rolling Sharpe ratio, within the chart window
        if sharpe:
            return calculate_rolling_sharpe(values, sharpe_window).loc[start_at:end_at]
        return calculate_period_performance(values, start_at)

    vaults = {}
    for item in series:
        if item.vault_id not in daily_prices.columns:
            logger.warning("No price data for vault %s, left out of the performance chart", item.vault_id)
            continue
        line = measure_line(daily_prices[item.vault_id])
        if len(line):
            vaults[item.vault_id] = line

    wanted = [name for name in BENCHMARK_DASHES if name in benchmark_indices and 2 * sum(name in item.benchmarks for item in series) >= len(series) and not (sharpe and name == TREASURY_BILL)]
    benchmarks = {name: measure_line(benchmark_indices[name] if sharpe else benchmark_indices[name].reindex(daily_prices.index, method="ffill")) for name in wanted}
    benchmarks = {name: line for name, line in benchmarks.items() if len(line)}

    peaks = pd.Series({vault_id: line.max() for vault_id, line in vaults.items()}, dtype=float)
    reference = max([peaks.median(), 3.0 if sharpe else 10.0, *(line.max() for line in benchmarks.values())])
    off_scale = set(peaks.index[peaks > outlier_ratio * reference]) if len(peaks) > 2 else set()
    lines = [*(line for vault_id, line in vaults.items() if vault_id not in off_scale), *benchmarks.values()]
    low, high = (min(line.min() for line in lines), max(line.max() for line in lines)) if lines else (0.0, 0.0)
    log_scale = not sharpe and high > log_threshold

    def to_axis(values: pd.Series | float) -> pd.Series | float:
        # Equity change in percent -> equity index, so a log axis works; Sharpe ratios as is
        return values if sharpe else EQUITY_CURVE_BASE * (1 + values / 100)

    def scale(values: pd.Series) -> np.ndarray:
        return to_axis(values).to_numpy()

    baseline = 0.0 if sharpe else EQUITY_CURVE_BASE
    # Sharpe ratios are drawn on a 0-x scale; negative values are clipped at the bottom edge
    bottom, top = (0.0, high) if sharpe else (to_axis(low), to_axis(high))
    padding = (top - bottom) * 0.06 + 0.2
    if log_scale:
        y_range = (np.log10(max(bottom - padding, bottom * 0.9)), np.log10(top + padding))
    else:
        y_range = (0.0 if sharpe else bottom - padding, top + padding)

    def position(value: float) -> float:
        # Paper y coordinate of a line end, for the end labels
        axis_value = np.log10(to_axis(value)) if log_scale else to_axis(value)
        return (axis_value - y_range[0]) / (y_range[1] - y_range[0])

    def describe(performance: pd.Series) -> str:
        # Latest Sharpe ratio, or the annualised return over the line's own span, capped like the tables
        if sharpe:
            return f"{performance.iloc[-1]:,.2f}"
        days = (performance.index[-1] - performance.index[0]) / pd.Timedelta(days=1)
        if days < 1:
            return "---"
        annualised = ((1 + performance.iloc[-1] / 100) ** (365 / days) - 1) * 100
        return f">{CAPPED_ANNUALISED_RETURN * 100:,.0f}% ann." if annualised > CAPPED_ANNUALISED_RETURN * 100 else f"{annualised:+,.1f}% ann."

    fig = go.Figure()
    entries = []
    labels = []
    for i, item in enumerate(series):
        performance = vaults.get(item.vault_id)
        if performance is None:
            continue
        colour = theme.series_colours[i]
        rank = item.rank or i + 1
        if item.vault_id in off_scale:
            # Draw an off-scale line only until it leaves the top of the chart, where a marker continues it
            exit_at = performance.index[performance > high][0]
            performance = performance.loc[:exit_at]
        # A surface-coloured casing under each line keeps crossing lines apart
        fig.add_trace(go.Scatter(x=performance.index, y=scale(performance), mode="lines", line={"color": theme.surface, "width": 8}, showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=performance.index, y=scale(performance), mode="lines", name=item.name, line={"color": colour, "width": 3.5}))
        since = f" since {performance.index[0]:%b %d}" if performance.index[0] > start_at + pd.Timedelta(days=3) else ""
        note = " ▲" if item.vault_id in off_scale else ""
        entries.append(LegendEntry(f"{rank}. {item.name}", colour, detail=f"{describe(vaults[item.vault_id])}{since}{note}", properties=item.properties))
        badge = {"font": {"size": 15, "color": theme.surface, "weight": 700}, "bgcolor": colour, "borderpad": 3}
        if item.vault_id in off_scale:
            fig.add_annotation(text=f"▲ {rank}", x=exit_at, y=1, xref="x", yref="paper", yanchor="top", showarrow=False, **badge)
        else:
            fig.add_trace(go.Scatter(x=performance.index[-1:], y=scale(performance)[-1:], mode="markers", marker={"size": 11, "color": colour, "line": {"color": theme.surface, "width": 2}}, showlegend=False, hoverinfo="skip"))
            labels.append((position(performance.iloc[-1]), f"{rank}", badge, None))

    for name, performance in benchmarks.items():
        fig.add_trace(go.Scatter(x=performance.index, y=scale(performance), mode="lines", name=name, line={"color": to_rgba(theme.muted_text, 0.75), "width": 2, "dash": BENCHMARK_DASHES[name]}, hoverinfo="skip"))
        logo = (benchmark_logos or {}).get(name)
        entries.append(LegendEntry(name, to_rgba(theme.muted_text, 0.75), logo, dash=BENCHMARK_DASHES[name], detail=describe(performance)))
        # The line end shows the benchmark logo and value; the name is only needed without a logo
        text = describe(performance) if logo else f"{name.removeprefix('US 3M ')} {describe(performance)}"
        labels.append((position(performance.iloc[-1]), text, {"font": {"size": 15, "color": theme.muted_text}}, logo))

    # Direct labels right of the line ends, pushed apart so they do not overlap
    min_gap = 0.042
    ordered = sorted(labels, key=lambda label: -label[0])
    positions = [min(max(label[0], 0.02), 0.98) for label in ordered]
    # Push down from the top, then back up from the bottom, keeping the order
    for i in range(1, len(positions)):
        positions[i] = min(positions[i], positions[i - 1] - min_gap)
    if positions:
        positions[-1] = max(positions[-1], 0.02)
    for i in range(len(positions) - 2, -1, -1):
        positions[i] = max(positions[i], positions[i + 1] + min_gap)
    label_at = end_at + pd.Timedelta(days=1.5)
    x_end = end_at + pd.Timedelta(window) * 0.16
    for y, (_, text, style, logo) in zip(positions, ordered, strict=True):
        if logo:
            fig.add_layout_image(source=logo, xref="paper", yref="paper", x=(label_at - start_at) / (x_end - start_at), y=y, sizex=0.032, sizey=0.032, xanchor="left", yanchor="middle")
        fig.add_annotation(text=text, x=label_at, xshift=30 if logo else 0, y=y, xref="x", yref="paper", xanchor="left", yanchor="middle", showarrow=False, **style)

    apply_theme(fig, theme, IMAGE_WIDTH, IMAGE_HEIGHT + 100)
    fig.update_layout(margin={"l": 110, "r": LEGEND_MARGIN, "t": 30, "b": 70})
    # The range leaves room for the end labels; ticks stop at the data date
    ticks = pd.date_range(end=end_at, periods=7, freq="14D")
    fig.update_xaxes(range=[start_at, x_end], tickvals=ticks[ticks >= start_at], tickformat="%b %d", showgrid=False)
    if log_scale:
        ticks = [tick for tick in LOG_SCALE_TICKS if 10 ** y_range[0] <= tick <= 10 ** y_range[1]]
        fig.update_yaxes(type="log", title="Equity % (log scale)")
    else:
        step = next((step for step in LINEAR_TICK_STEPS if (y_range[1] - y_range[0]) / step <= 7), LINEAR_TICK_STEPS[-1])
        ticks = list(np.arange(np.ceil((y_range[0] - baseline) / step) * step, y_range[1] - baseline, step) + baseline)
        fig.update_yaxes(title=f"{sharpe_window.days}-day rolling Sharpe ratio" if sharpe else "Equity %")
    ticktext = [f"{tick:,.4g}" for tick in ticks] if sharpe else [f"{tick - EQUITY_CURVE_BASE:+,.4g}%" if round(tick, 6) != EQUITY_CURVE_BASE else "0%" for tick in ticks]
    fig.update_yaxes(range=list(y_range), tickvals=ticks, ticktext=ticktext)
    fig.update_yaxes(side="left", zeroline=False)
    fig.add_hline(y=baseline, line={"color": theme.axis, "width": 1.5}, layer="below")
    add_logo_legend(fig, entries, theme, row_height=min(0.1, 0.98 / max(len(entries), 1)))
    return fig


def _format_usd_short(value: float) -> str:
    """Format a dollar amount compactly for chart labels.

    :param value:
        Amount in USD, may be negative.

    :return:
        E.g. ``$23.7B``, ``$540M`` or ``-$1.2M``.
    """
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e9:
        return f"{sign}${value / 1e9:.1f}B"
    if value >= 1e6:
        return f"{sign}${value / 1e6:.1f}M"
    return f"{sign}${value / 1e3:.0f}k"


def create_average_yield_figure(
    yields: pd.DataFrame,
    vault_returns: pd.DataFrame,
    group_column: str,
    theme: ChartTheme,
    logos: dict[str, str | None] | None = None,
    benchmark_yield: float | None = None,
    max_return: float = 0.4,
) -> Figure:
    """Draw a dot plot of vault yields per chain or protocol against the Treasury bill.

    A yield is a position on a rate scale, not a quantity, so each group is a
    row of dots rather than a bar: small dots for individual vaults, showing the
    spread, and a large dot for the TVL-weighted average. The right-hand column
    gives the average, its difference to the Treasury bill in percentage points,
    and the group's TVL.

    :param yields:
        Output of :py:func:`eth_defi.vault_report.sections.calculate_chain_yields`
        or :py:func:`~eth_defi.vault_report.sections.calculate_protocol_yields`.

    :param vault_returns:
        Vaults counted in the averages, with the ``group_column`` and ``one_month_cagr_best`` columns.

    :param group_column:
        ``chain`` or ``protocol``.

    :param theme:
        Chart theme.

    :param logos:
        Group name -> logo data URI.

    :param benchmark_yield:
        Latest US Treasury bill yield as a fraction.

    :param max_return:
        Clip individual vault dots at this annualised return, and below at -5%;
        clipped vaults are drawn as triangles on the edges.

    :return:
        Plotly figure.
    """
    logos = logos or {}
    df = yields.sort_values("avg_return")
    positions = {group: position for position, group in enumerate(df.index)}
    clip = max_return * 100

    points = vault_returns.loc[vault_returns[group_column].isin(positions)].copy()
    points["x"] = (points["one_month_cagr_best"] * 100).clip(lower=-5, upper=clip)
    points["marker"] = np.select([points["one_month_cagr_best"] * 100 > clip, points["one_month_cagr_best"] * 100 < -5], ["triangle-right", "triangle-left"], "circle")
    # Deterministic vertical jitter so dots of one group do not sit on top of each other
    points["y"] = [positions[group] + ((zlib.crc32(vault_id.encode()) % 1000) / 1000 - 0.5) * 0.44 for vault_id, group in zip(points.index, points[group_column], strict=True)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=points["x"], y=points["y"], mode="markers", marker={"size": 7, "symbol": points["marker"], "color": to_rgba(theme.muted_text, 0.35)}, hoverinfo="skip", showlegend=False))

    above = [benchmark_yield is None or value >= benchmark_yield for value in df["avg_return"]]
    fig.add_trace(
        go.Scatter(
            x=df["avg_return"] * 100,
            y=list(range(len(df))),
            mode="markers",
            marker={"size": 22, "color": [theme.positive if is_above else theme.muted_text for is_above in above], "line": {"color": theme.surface, "width": 3}},
            showlegend=False,
        )
    )

    height = max(IMAGE_HEIGHT, 140 + 58 * len(df))
    apply_theme(fig, theme, IMAGE_WIDTH, height)
    # Margins sized so the labels, the plot and the right column fill the panel width
    fig.update_layout(xaxis_title="1M annualised return (%)", margin={"l": 254, "r": 264, "t": 50, "b": 90})
    fig.update_xaxes(showgrid=True, gridcolor=theme.grid, range=[-6, clip + 2], ticksuffix="%", zeroline=True, zerolinecolor=theme.axis, zerolinewidth=1)
    fig.update_yaxes(showgrid=False, showticklabels=False, showline=False, zeroline=False, range=[-0.7, len(df) - 0.3])

    for position, (group, row) in enumerate(df.iterrows()):
        fig.add_shape(type="line", xref="paper", yref="y", x0=0, x1=1, y0=position, y1=position, line={"color": theme.grid, "width": 1}, layer="below")
        logo = logos.get(group)
        if logo:
            fig.add_layout_image(source=logo, xref="paper", yref="y", x=-0.235, y=position, sizex=0.03, sizey=0.6, xanchor="left", yanchor="middle")
        fig.add_annotation(text=wrap_label(group, 16), xref="paper", yref="y", x=-0.19, y=position, xanchor="left", align="left", showarrow=False, font={"size": 20, "color": theme.text})
        # Round before formatting so a tiny negative difference does not print as -0.0
        spread = f" {round((row['avg_return'] - benchmark_yield) * 100, 1) + 0.0:+.1f} pp" if benchmark_yield is not None else ""
        fig.add_annotation(
            text=f"<b>{row['avg_return']:.1%}</b><span style='color:{theme.muted_text}'>{spread} · {_format_usd_short(row['tvl'])}</span>",
            xref="paper",
            yref="y",
            x=1.02,
            y=position,
            xanchor="left",
            showarrow=False,
            font={"size": 18, "color": theme.text},
        )

    if benchmark_yield is not None:
        fig.add_vline(x=benchmark_yield * 100, line={"color": theme.benchmark, "width": 3, "dash": "dash"})
        fig.add_annotation(text=f"US 3M T-bill {benchmark_yield:.1%}", x=benchmark_yield * 100, xref="x", y=1.0, yref="paper", yanchor="bottom", showarrow=False, font={"size": 18, "color": theme.benchmark})
    return fig


def create_tvl_change_figure(changes: pd.DataFrame, theme: ChartTheme, properties: dict[str, tuple[VaultProperty, ...]] | None = None) -> Figure:
    """Draw the largest one-month TVL increases and decreases as diverging bars.

    Dollar amounts are quantities, so bars are the right form here: increases
    extend right in green, decreases left in red, with the amount labelled.

    :param changes:
        Output of :py:func:`eth_defi.vault_report.sections.calculate_tvl_changes`, largest increase first.

    :param theme:
        Chart theme.

    :param properties:
        Vault id -> curator, protocol and chain, drawn with their icons under the vault name.

    :return:
        Plotly figure.
    """
    properties = properties or {}
    df = changes.iloc[::-1]
    values = df["tvl_change"] / 1e6
    fig = go.Figure(
        go.Bar(
            x=values,
            y=list(range(len(df))),
            orientation="h",
            marker={"color": [theme.positive if value >= 0 else theme.negative for value in values], "cornerradius": 5},
            text=[("+" if value >= 0 else "") + _format_usd_short(change) for value, change in zip(values, df["tvl_change"], strict=True)],
            textposition="outside",
            textfont={"color": theme.text, "size": 17},
            cliponaxis=False,
        )
    )
    height = max(IMAGE_HEIGHT, 120 + 72 * len(df))
    apply_theme(fig, theme, IMAGE_WIDTH, height)
    span = float(values.abs().max()) * 1.25 if len(values) else 1.0
    fig.update_layout(xaxis_title="TVL change over 30 days (USD million)", bargap=0.3, margin={"l": 470, "r": 60, "t": 30, "b": 90})
    fig.update_xaxes(showgrid=True, gridcolor=theme.grid, range=[-span, span], zeroline=True, zerolinecolor=theme.muted_text, zerolinewidth=2)
    fig.update_yaxes(showgrid=False, showticklabels=False, showline=False, zeroline=False, range=[-0.7, len(df) - 0.3])
    # Labels in the left margin: the vault name, then its curator, protocol and chain with their icons
    plot_width = IMAGE_WIDTH - fig.layout.margin.l - fig.layout.margin.r
    unit_pixels = (height - fig.layout.margin.t - fig.layout.margin.b) / max(len(df), 1)
    label_x, label_width = -0.515, 0.515 * plot_width - 20
    for position, (vault_id, vault) in enumerate(df.iterrows()):
        lines = textwrap.wrap(vault["name"] or vault["address"], width=40) or [""]
        vault_properties = properties.get(vault_id, ())
        property_rows = len(layout_properties(vault_properties, label_width)) if vault_properties else 0
        top = position + (len(lines) * 21 + property_rows * PROPERTY_ROW_HEIGHT) / 2 / unit_pixels
        fig.add_annotation(text="<br>".join(lines), xref="paper", yref="y", x=label_x, y=top, xanchor="left", yanchor="top", align="left", showarrow=False, font={"size": 17, "color": theme.text})
        if vault_properties:
            add_property_rows(fig, vault_properties, theme, label_x, top - (len(lines) * 21 + PROPERTY_ROW_HEIGHT / 2) / unit_pixels, "y", label_width, plot_width, unit_pixels)
    return fig


def create_risk_return_figure(
    vaults_df: pd.DataFrame,
    category_labels: dict[str, str],
    theme: ChartTheme,
    max_return: float,
    benchmark_yield: float | None = None,
    label_count: int = 6,
) -> Figure:
    """Draw a risk/return bubble scatter of vaults.

    Uses annualised three-month volatility rather than Sharpe, because
    near-zero-volatility lending vaults have Sharpe ratios in the millions.
    Vaults above ``max_return`` are drawn as triangles on the top edge, so the
    bulk of the market is not squashed into a flat band. Unclassified vaults
    are drawn first in a neutral colour, so classified strategies stand out.

    :param vaults_df:
        Vaults with ``three_months_volatility``, ``three_months_cagr_best``,
        ``current_nav`` and ``strategy_tags`` columns.

    :param category_labels:
        Strategy tag -> human-readable category label.

    :param theme:
        Chart theme.

    :param max_return:
        Clip annualised returns above this, as a fraction.

    :param benchmark_yield:
        Latest US Treasury bill yield as a fraction, drawn as a reference line.

    :param label_count:
        Label the vaults with the highest returns within the clip directly.

    :return:
        Plotly figure.
    """
    df = vaults_df.dropna(subset=["three_months_volatility", "three_months_cagr_best", "current_nav"]).copy()
    min_volatility = 1e-4
    df["x"] = df["three_months_volatility"].clip(lower=min_volatility) * 100
    df["clipped"] = df["three_months_cagr_best"] > max_return
    df["y"] = df["three_months_cagr_best"].clip(lower=-0.5, upper=max_return) * 100
    # Area ∝ TVL, capped so the few multi-billion vaults do not cover the chart
    df["size"] = np.sqrt(df["current_nav"].clip(upper=2e9))
    df["category"] = df["strategy_tags"].apply(lambda tags: category_labels.get(tags[0], tags[0]) if isinstance(tags, list) and tags else "Unclassified")
    classified = df.loc[df["category"] != "Unclassified", "category"].value_counts().index[: len(theme.series_colours) - 1].tolist()
    df["category"] = df["category"].where(df["category"].isin(classified) | (df["category"] == "Unclassified"), "Other")

    fig = go.Figure()
    size_ref = 2.0 * df["size"].max() / (40**2)
    colours = dict(zip(classified, theme.series_colours, strict=False)) | {"Other": theme.neutral, "Unclassified": theme.neutral}
    for category in ["Unclassified", *classified, "Other"]:
        group = df.loc[(df["category"] == category) & ~df["clipped"]]
        if group.empty:
            continue
        opacity = 0.45 if category == "Unclassified" else 0.8
        fig.add_trace(
            go.Scatter(
                x=group["x"],
                y=group["y"],
                mode="markers",
                name=f"{category} ({len(group)})",
                marker={
                    "size": group["size"],
                    "sizemode": "area",
                    "sizeref": size_ref,
                    "sizemin": 4,
                    "color": to_rgba(colours[category], opacity),
                    "line": {"color": theme.surface, "width": 1.5},
                },
            )
        )

    clipped = df.loc[df["clipped"]]
    if len(clipped):
        fig.add_trace(go.Scatter(x=clipped["x"], y=clipped["y"], mode="markers", name=f"Above {max_return:.0%} ({len(clipped)})", marker={"size": 14, "symbol": "triangle-up", "color": theme.text}))

    # Alternate label offsets so neighbouring labels do not overlap
    offsets = [(34, -28), (34, 30), (-34, -48), (-34, 44)]
    for i, (_, vault) in enumerate(df.loc[~df["clipped"]].nlargest(label_count, "y").iterrows()):
        ax, ay = offsets[i % len(offsets)]
        fig.add_annotation(x=np.log10(vault["x"]), y=vault["y"], text=wrap_label(vault["name"] or vault["address"], 24), align="left", showarrow=True, arrowcolor=theme.axis, ax=ax, ay=ay, font={"size": 15, "color": theme.text})

    if benchmark_yield is not None:
        fig.add_hline(y=benchmark_yield * 100, line={"color": theme.benchmark, "width": 3, "dash": "dash"})
        fig.add_annotation(text=f"US 3M T-bill {benchmark_yield:.1%}", xref="paper", x=1.0, y=benchmark_yield * 100, yanchor="bottom", xanchor="right", yshift=4, showarrow=False, font={"size": 17, "color": theme.benchmark})

    apply_theme(fig, theme, IMAGE_WIDTH, 900)
    fig.update_layout(
        xaxis_title="3M volatility, annualised (%, log scale)",
        yaxis_title="3M return, annualised (%)",
        margin={"l": 90, "r": 330, "t": 40, "b": 80},
        legend={"font": {"size": 17, "color": theme.text}, "x": 1.02, "y": 1, "xanchor": "left", "itemsizing": "constant", "title": {"text": "Strategy", "font": {"color": theme.text}}},
    )
    fig.update_xaxes(type="log", showgrid=True, gridcolor=theme.grid, range=[np.log10(min_volatility * 100) - 0.1, np.log10(df["x"].max()) + 0.1])
    fig.update_yaxes(side="left", range=[min(df["y"].min(), 0) - 5, max_return * 100 + 8])
    return fig


def create_protocol_tvl_figure(
    tvl_by_protocol: pd.DataFrame,
    theme: ChartTheme,
    logos: dict[str, str | None] | None = None,
    value_label: str = "TVL",
) -> Figure:
    """Draw stacked TVL by protocol or fund with a glowing total line.

    :param tvl_by_protocol:
        Periodic TVL in USD, one column per protocol or fund in stacking order
        (largest first), with at most seven groups and ``Other``.

    :param theme:
        Chart theme.

    :param logos:
        Protocol or fund name -> logo data URI.

    :param value_label:
        Name of the value on the y axis, e.g. ``TVL`` or ``NAV``.

    :return:
        Plotly figure.
    """
    logos = logos or {}
    fig = go.Figure()
    entries = []
    colours = [*theme.series_colours[: len(tvl_by_protocol.columns) - 1], theme.neutral] if "Other" in tvl_by_protocol.columns else list(theme.series_colours)
    for colour, protocol in zip(colours, tvl_by_protocol.columns, strict=False):
        series = tvl_by_protocol[protocol] / 1e9
        fig.add_trace(go.Scatter(x=series.index, y=series.to_numpy(), mode="lines", stackgroup="tvl", name=protocol, line={"width": 0, "color": colour}, fillcolor=to_rgba(colour, 0.56)))
        entries.append(LegendEntry(f"{protocol} {_format_usd_short(series.iloc[-1] * 1e9)}", colour, logos.get(protocol)))

    total = tvl_by_protocol.sum(axis=1) / 1e9
    add_glow_line(fig, total.index, total.to_numpy(), theme.positive, "Total")
    entries.insert(0, LegendEntry(f"Total {_format_usd_short(total.iloc[-1] * 1e9)}", theme.positive))

    apply_theme(fig, theme, IMAGE_WIDTH, IMAGE_HEIGHT)
    # The protocol legend is short, so it needs less room than LEGEND_MARGIN and the plot fills the panel width
    fig.update_layout(margin={"l": 90, "r": 360, "t": 30, "b": 70}, yaxis_title=f"{value_label} (USD billion)")
    fig.update_yaxes(side="left", rangemode="tozero")
    add_logo_legend(fig, entries, theme, row_height=0.1)
    return fig


def rasterise_logos(logo_uris: set[str], size: int = 96) -> dict[str, Image.Image]:
    """Convert logo data URIs to Pillow images for the Pillow-drawn hero image.

    PNG logos are decoded directly. SVG logos, such as the website's chain
    logos, are rendered by Kaleido in one pass: Pillow cannot draw SVG.

    :param logo_uris:
        PNG or SVG data URIs.

    :param size:
        Rendered size of an SVG logo in pixels.

    :return:
        Data URI -> RGBA image.
    """
    images = {}
    svgs = sorted(uri for uri in logo_uris if uri.startswith("data:image/svg+xml"))
    for uri in logo_uris - set(svgs):
        images[uri] = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("RGBA")
    if svgs:
        fig = go.Figure()
        fig.update_layout(width=size * len(svgs), height=size, margin={"l": 0, "r": 0, "t": 0, "b": 0}, xaxis_visible=False, yaxis_visible=False)
        for i, uri in enumerate(svgs):
            fig.add_layout_image(source=uri, xref="paper", yref="paper", x=i / len(svgs), y=1, sizex=1 / len(svgs), sizey=1, xanchor="left", yanchor="top")
        with tempfile.TemporaryDirectory() as tmp:
            sheet = Image.open(render_figure_png(fig, Path(tmp) / "logos.png")).convert("RGBA")
            sheet.load()
        for i, uri in enumerate(svgs):
            images[uri] = sheet.crop((i * size, 0, (i + 1) * size, size))
    return images


def _configure_fonts() -> None:
    """Make the bundled Inter font available to Kaleido's Chrome.

    Chrome on Linux finds fonts through fontconfig. A private configuration
    that includes the system configuration and the bundled font directory is
    written to a temporary directory and selected with ``FONTCONFIG_FILE``,
    so nothing is installed system-wide. An existing ``FONTCONFIG_FILE`` is respected.
    """
    if os.environ.get("FONTCONFIG_FILE"):
        return
    config_dir = Path(tempfile.mkdtemp(prefix="eth-defi-vault-report-fonts-"))
    (config_dir / "fonts.conf").write_text(
        f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>{ASSETS_DIR / "fonts"}</dir>
  <cachedir>{config_dir / "cache"}</cachedir>
</fontconfig>
"""
    )
    os.environ["FONTCONFIG_FILE"] = str(config_dir / "fonts.conf")


def render_figure_png(fig: Figure, path: Path) -> Path:
    """Render a figure as a PNG image.

    Kaleido 1.x renders images with a headless Chrome. If ``BROWSER_PATH``
    is not set and Chrome has been downloaded with ``plotly_get_chrome``
    to the default choreographer location, that Chrome is used. The bundled
    :py:data:`~eth_defi.vault_report.theme.FONT_FAMILY` font is made available
    to Chrome, see :py:func:`_configure_fonts`.

    The chart is rendered on a transparent background, so the panel surface
    and its corner glow show through evenly when
    :py:func:`eth_defi.vault_report.branding.compose_chart_panel` frames it.

    :param fig:
        Plotly figure with width and height set in its layout.

    :param path:
        Output file.

    :return:
        The output path.
    """
    if not os.environ.get("BROWSER_PATH") and CHOREOGRAPHER_CHROME_PATH.exists():
        os.environ["BROWSER_PATH"] = str(CHOREOGRAPHER_CHROME_PATH)
    _configure_fonts()

    # Kaleido and its browser driver log every tab and temporary directory operation at INFO level
    for noisy_logger in ("kaleido", "choreographer"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    path.parent.mkdir(parents=True, exist_ok=True)
    transparent = go.Figure(fig).update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    try:
        transparent.write_image(path, format="png")
    except RuntimeError as e:
        raise RuntimeError("Could not render chart PNG. Kaleido needs Chrome: run `poetry run plotly_get_chrome` or set BROWSER_PATH to a Chrome binary.") from e
    logger.info("Rendered chart %s", path)
    return path
