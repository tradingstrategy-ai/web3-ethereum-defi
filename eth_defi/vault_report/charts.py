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
- Performance as cumulative returns of all compared vaults in one chart with a
  shared axis, yields as dots on a rate scale, and dollar TVL changes as
  diverging bars
- A faint logo watermark inside the plot area, as on the website charts
- Glowing lines for charts with few series, like the website's hero charts
"""

import datetime
import logging
import os
import tempfile
import textwrap
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.graph_objects import Figure

from eth_defi.vault_report.benchmarks import BTC, ETH, TREASURY_BILL
from eth_defi.vault_report.theme import ASSETS_DIR, ChartTheme, apply_theme

logger = logging.getLogger(__name__)

#: Rendered chart width. The blog shows images at 720 px width, so charts are rendered at roughly 2×.
IMAGE_WIDTH = 1400

#: Default rendered chart height
IMAGE_HEIGHT = 800

#: Chrome downloaded by ``plotly_get_chrome`` / ``kaleido_get_chrome`` on Linux
CHOREOGRAPHER_CHROME_PATH = Path("~/.local/share/choreographer/deps/chrome-linux64/chrome").expanduser()

#: Width of the right margin that holds a logo legend
LEGEND_MARGIN = 430


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

    #: Muted second line, e.g. the final return. When set, the label is shortened to one line.
    detail: str | None = None


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


def shorten_label(text: str, max_length: int = 36) -> str:
    """Truncate a long label with an ellipsis.

    :param text:
        Label.

    :param max_length:
        Maximum length including the ellipsis.

    :return:
        Label of at most ``max_length`` characters.
    """
    return text if len(text) <= max_length else text[: max_length - 1].rstrip() + "…"


def add_watermark(fig: Figure, watermark_uri: str | None, theme: ChartTheme) -> None:
    """Add the faint brand logo in the top-left corner of the plot area.

    :param fig:
        Figure to modify.

    :param watermark_uri:
        Output of :py:func:`eth_defi.vault_report.logos.load_watermark_logo_uri`, or ``None`` to skip.

    :param theme:
        Chart theme.
    """
    if watermark_uri:
        fig.add_layout_image(source=watermark_uri, xref="paper", yref="paper", x=0.015, y=0.985, sizex=0.26, sizey=0.1, xanchor="left", yanchor="top", opacity=theme.watermark_opacity, layer="below")


def add_logo_legend(fig: Figure, entries: list[LegendEntry], theme: ChartTheme, top: float = 1.0, row_height: float = 0.088) -> None:
    """Draw a legend with logos in the right margin.

    Plotly legends cannot show images, so the legend is drawn with shapes,
    layout images and annotations. The figure needs a right margin of
    :py:data:`LEGEND_MARGIN` pixels.

    :param fig:
        Figure to modify.

    :param entries:
        Legend entries from top to bottom.

    :param theme:
        Chart theme.

    :param top:
        Paper y coordinate of the first entry.

    :param row_height:
        Paper height of one entry.
    """
    fig.update_layout(showlegend=False)
    for i, entry in enumerate(entries):
        y = top - i * row_height
        fig.add_shape(type="line", xref="paper", yref="paper", x0=1.03, x1=1.075, y0=y, y1=y, line={"color": entry.colour, "width": 6, "dash": entry.dash})
        if entry.logo_uri:
            fig.add_layout_image(source=entry.logo_uri, xref="paper", yref="paper", x=1.09, y=y, sizex=0.034, sizey=0.05, xanchor="left", yanchor="middle")
        if entry.detail:
            lines = [shorten_label(entry.label, 28), f"<span style='color:{theme.muted_text}'>{entry.detail}</span>"]
        else:
            lines = textwrap.wrap(entry.label, width=24)
            if len(lines) > 2:
                lines = [lines[0], shorten_label(" ".join(lines[1:]), 24)]
        fig.add_annotation(
            text="<br>".join(lines),
            xref="paper",
            yref="paper",
            x=1.132,
            y=y,
            xanchor="left",
            yanchor="middle",
            align="left",
            showarrow=False,
            font={"size": 17, "color": theme.text},
        )


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

    #: Protocol logo data URI, or ``None``
    logo_uri: str | None

    #: Benchmark names the vault is compared with, see :py:func:`eth_defi.vault_report.benchmarks.select_benchmarks`
    benchmarks: tuple[str, ...]


#: Benchmark line dash styles. Benchmarks share one neutral colour, so they do not compete with the vault colours.
BENCHMARK_DASHES = {TREASURY_BILL: "dash", BTC: "dot", ETH: "dashdot"}

#: Candidate y axis ticks of a log-scale performance chart, as cumulative returns in percent
LOG_SCALE_TICKS = (-90, -50, -20, 0, 20, 50, 100, 200, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000)


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


def create_performance_figure(
    series: list[PerformanceSeries],
    daily_prices: pd.DataFrame,
    benchmark_indices: dict[str, pd.Series],
    theme: ChartTheme,
    window: datetime.timedelta = datetime.timedelta(days=90),
    watermark_uri: str | None = None,
    log_threshold: float = 100.0,
    outlier_ratio: float = 5.0,
) -> Figure:
    """Draw the cumulative returns of vaults and their benchmarks in one chart.

    All vaults share the time axis and the return axis, so their equity curves
    can be compared directly. Vault colours follow the table order, and the
    legend numbers match the table rows. A vault younger than the window starts
    from 0% at its first data point.

    Benchmarks used by at least half of the vaults, see
    :py:func:`eth_defi.vault_report.benchmarks.select_benchmarks`, are drawn
    once in a neutral colour and rebased to the window start. A single volatile
    vault among calm ones therefore does not pull BTC and ETH into a T-bill chart.

    A vault whose peak return exceeds ``outlier_ratio`` times the median vault
    peak, the benchmark peaks and 10% is drawn off scale: the axis fits the other lines, and
    the outlier leaves the top edge with a marker and "off scale" in the legend.
    Otherwise one anomalous vault would flatten all others.

    When the remaining lines return more than ``log_threshold`` percent, the y
    axis switches to a log scale of growth. Its ticks are still labelled as returns.

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

    :param watermark_uri:
        Watermark logo data URI.

    :param log_threshold:
        Cumulative return in percent above which the y axis is logarithmic.

    :param outlier_ratio:
        Peak return multiple of the median vault peak above which a vault is drawn off scale.

    :return:
        Plotly figure.
    """
    assert len(series) <= len(theme.series_colours), f"At most {len(theme.series_colours)} vaults fit one chart, got {len(series)}"
    end_at = daily_prices.index.max()
    start_at = end_at - pd.Timedelta(window)

    vaults = {}
    for item in series:
        if item.vault_id not in daily_prices.columns:
            logger.warning("No price data for vault %s, left out of the performance chart", item.vault_id)
            continue
        performance = calculate_period_performance(daily_prices[item.vault_id], start_at)
        if len(performance):
            vaults[item.vault_id] = performance

    wanted = [name for name in BENCHMARK_DASHES if name in benchmark_indices and 2 * sum(name in item.benchmarks for item in series) >= len(series)]
    benchmarks = {name: calculate_period_performance(benchmark_indices[name].reindex(daily_prices.index, method="ffill"), start_at) for name in wanted}
    benchmarks = {name: performance for name, performance in benchmarks.items() if len(performance)}

    peaks = pd.Series({vault_id: performance.max() for vault_id, performance in vaults.items()}, dtype=float)
    reference = max([peaks.median(), 10.0, *(performance.max() for performance in benchmarks.values())])
    off_scale = set(peaks.index[peaks > outlier_ratio * reference]) if len(peaks) > 2 else set()
    lines = [*(performance for vault_id, performance in vaults.items() if vault_id not in off_scale), *benchmarks.values()]
    low, high = (min(line.min() for line in lines), max(line.max() for line in lines)) if lines else (0.0, 0.0)
    log_scale = high > log_threshold

    def scale(values: pd.Series) -> np.ndarray:
        return (1 + values / 100).to_numpy() if log_scale else values.to_numpy()

    fig = go.Figure()
    entries = []
    for i, item in enumerate(series):
        performance = vaults.get(item.vault_id)
        if performance is None:
            continue
        colour = theme.series_colours[i]
        fig.add_trace(go.Scatter(x=performance.index, y=scale(performance), mode="lines", name=item.name, line={"color": colour, "width": 3}))
        # End dot with a surface ring, so crossing lines stay distinguishable where they end
        fig.add_trace(go.Scatter(x=performance.index[-1:], y=scale(performance)[-1:], mode="markers", marker={"size": 11, "color": colour, "line": {"color": theme.surface, "width": 2}}, showlegend=False, hoverinfo="skip"))
        since = f" since {performance.index[0]:%b %d}" if performance.index[0] > start_at + pd.Timedelta(days=3) else ""
        note = " · off scale" if item.vault_id in off_scale else ""
        entries.append(LegendEntry(f"{i + 1}. {item.name}", colour, item.logo_uri, detail=f"{performance.iloc[-1]:+,.1f}%{since}{note}"))
        if item.vault_id in off_scale:
            # Mark where the line leaves the top of the chart
            exit_at = performance.index[performance > high][0]
            fig.add_annotation(text="▲", x=exit_at, y=1, xref="x", yref="paper", yanchor="top", showarrow=False, font={"size": 20, "color": colour})

    for name, performance in benchmarks.items():
        fig.add_trace(go.Scatter(x=performance.index, y=scale(performance), mode="lines", name=name, line={"color": theme.muted_text, "width": 2.5, "dash": BENCHMARK_DASHES[name]}, hoverinfo="skip"))
        entries.append(LegendEntry(name, theme.muted_text, dash=BENCHMARK_DASHES[name], detail=f"{performance.iloc[-1]:+,.1f}%"))

    apply_theme(fig, theme, IMAGE_WIDTH, IMAGE_HEIGHT + 100)
    fig.update_layout(margin={"l": 110, "r": LEGEND_MARGIN, "t": 30, "b": 70})
    fig.update_xaxes(range=[start_at, end_at + pd.Timedelta(days=2)], tickformat="%b %d", showgrid=False)
    padding = (high - low) * 0.06 + 0.2
    if log_scale:
        ticks = [tick for tick in LOG_SCALE_TICKS if low - 5 <= tick <= high * 1.1]
        bottom = max(1 + (low - padding) / 100, (1 + low / 100) * 0.9)
        fig.update_yaxes(type="log", range=[np.log10(bottom), np.log10(1 + (high + padding) / 100)], tickvals=[1 + tick / 100 for tick in ticks], ticktext=[f"{tick:+,}%" if tick else "0%" for tick in ticks], title="Cumulative return (log scale)")
    else:
        fig.update_yaxes(range=[low - padding, high + padding], ticksuffix="%", title="Cumulative return")
    fig.update_yaxes(side="left", zeroline=False)
    fig.add_hline(y=1 if log_scale else 0, line={"color": theme.axis, "width": 1.5}, layer="below")
    add_logo_legend(fig, entries, theme, row_height=min(0.1, 0.98 / max(len(entries), 1)))
    add_watermark(fig, watermark_uri, theme)
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
    watermark_uri: str | None = None,
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

    :param watermark_uri:
        Watermark logo data URI.

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
    fig.update_layout(xaxis_title="1M annualised return (%)", margin={"l": 280, "r": 290, "t": 50, "b": 90})
    fig.update_xaxes(showgrid=True, gridcolor=theme.grid, range=[-6, clip + 2], ticksuffix="%", zeroline=True, zerolinecolor=theme.axis, zerolinewidth=1)
    fig.update_yaxes(showgrid=False, showticklabels=False, showline=False, zeroline=False, range=[-0.7, len(df) - 0.3])

    for position, (group, row) in enumerate(df.iterrows()):
        fig.add_shape(type="line", xref="paper", yref="y", x0=0, x1=1, y0=position, y1=position, line={"color": theme.grid, "width": 1}, layer="below")
        logo = logos.get(group)
        if logo:
            fig.add_layout_image(source=logo, xref="paper", yref="y", x=-0.235, y=position, sizex=0.03, sizey=0.6, xanchor="left", yanchor="middle")
        fig.add_annotation(text=shorten_label(group, 17), xref="paper", yref="y", x=-0.19, y=position, xanchor="left", showarrow=False, font={"size": 20, "color": theme.text})
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
    add_watermark(fig, watermark_uri, theme)
    return fig


def create_tvl_change_figure(changes: pd.DataFrame, theme: ChartTheme, logos: dict[str, str | None] | None = None) -> Figure:
    """Draw the largest one-month TVL increases and decreases as diverging bars.

    Dollar amounts are quantities, so bars are the right form here: increases
    extend right in green, decreases left in red, with the amount labelled.

    :param changes:
        Output of :py:func:`eth_defi.vault_report.sections.calculate_tvl_changes`, largest increase first.

    :param theme:
        Chart theme.

    :param logos:
        Vault id -> protocol logo data URI.

    :return:
        Plotly figure.
    """
    logos = logos or {}
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
    height = max(IMAGE_HEIGHT, 140 + 40 * len(df))
    apply_theme(fig, theme, IMAGE_WIDTH, height)
    span = float(values.abs().max()) * 1.25 if len(values) else 1.0
    fig.update_layout(xaxis_title="TVL change over 30 days (USD million)", bargap=0.3, margin={"l": 470, "r": 60, "t": 30, "b": 90})
    fig.update_xaxes(showgrid=True, gridcolor=theme.grid, range=[-span, span], zeroline=True, zerolinecolor=theme.muted_text, zerolinewidth=2)
    fig.update_yaxes(showgrid=False, showticklabels=False, showline=False, zeroline=False, range=[-0.7, len(df) - 0.3])
    for position, (vault_id, vault) in enumerate(df.iterrows()):
        logo = logos.get(vault_id)
        if logo:
            fig.add_layout_image(source=logo, xref="paper", yref="y", x=-0.52, y=position, sizex=0.03, sizey=0.7, xanchor="left", yanchor="middle")
        label = f"{shorten_label(vault['name'] or vault['address'], 30)}  <span style='color:{theme.muted_text}'>{vault['chain']}</span>"
        fig.add_annotation(text=label, xref="paper", yref="y", x=-0.475, y=position, xanchor="left", showarrow=False, font={"size": 17, "color": theme.text})
    return fig


def create_risk_return_figure(
    vaults_df: pd.DataFrame,
    category_labels: dict[str, str],
    theme: ChartTheme,
    max_return: float,
    benchmark_yield: float | None = None,
    watermark_uri: str | None = None,
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

    :param watermark_uri:
        Watermark logo data URI.

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
        fig.add_annotation(x=np.log10(vault["x"]), y=vault["y"], text=shorten_label(vault["name"] or vault["address"], 26), showarrow=True, arrowcolor=theme.axis, ax=ax, ay=ay, font={"size": 15, "color": theme.text})

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
    add_watermark(fig, watermark_uri, theme)
    return fig


def create_protocol_tvl_figure(
    tvl_by_protocol: pd.DataFrame,
    theme: ChartTheme,
    logos: dict[str, str | None] | None = None,
    watermark_uri: str | None = None,
) -> Figure:
    """Draw stacked TVL by protocol with a glowing total line.

    :param tvl_by_protocol:
        Periodic TVL in USD, one column per protocol in stacking order (largest
        first), with at most seven protocols and ``Other``.

    :param theme:
        Chart theme.

    :param logos:
        Protocol name -> logo data URI.

    :param watermark_uri:
        Watermark logo data URI.

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
        entries.append(LegendEntry(f"{protocol} ${series.iloc[-1]:,.1f}B", colour, logos.get(protocol)))

    total = tvl_by_protocol.sum(axis=1) / 1e9
    add_glow_line(fig, total.index, total.to_numpy(), theme.positive, "Total")
    entries.insert(0, LegendEntry(f"Total ${total.iloc[-1]:,.1f}B", theme.positive))

    apply_theme(fig, theme, IMAGE_WIDTH, IMAGE_HEIGHT)
    fig.update_layout(margin={"l": 90, "r": LEGEND_MARGIN, "t": 30, "b": 70}, yaxis_title="TVL (USD billion)")
    fig.update_yaxes(side="left", rangemode="tozero")
    add_logo_legend(fig, entries, theme, row_height=0.1)
    add_watermark(fig, watermark_uri, theme)
    return fig


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
    try:
        fig.write_image(path, format="png")
    except RuntimeError as e:
        raise RuntimeError("Could not render chart PNG. Kaleido needs Chrome: run `poetry run plotly_get_chrome` or set BROWSER_PATH to a Chrome binary.") from e
    logger.info("Rendered chart %s", path)
    return path
