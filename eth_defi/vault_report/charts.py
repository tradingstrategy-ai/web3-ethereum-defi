"""Static charts for the monthly vault report.

Charts are Plotly figures rendered to PNG by
`Kaleido <https://github.com/plotly/Kaleido>`__ with headless Chrome, then framed
by :py:mod:`eth_defi.vault_report.branding`. Styling follows
:py:mod:`eth_defi.vault_report.theme`:

- At most eight categorical series, in the theme's fixed colour order
- Legends with protocol logos instead of plain colour boxes
- Benchmarks matching each vault's activity: the US Treasury bill (amber,
  dashed) for calm yield vaults, BTC and ETH (dotted) for trading and volatile
  vaults, see :py:mod:`eth_defi.vault_report.benchmarks`
- Performance as cumulative returns over a common period in small multiples,
  and yields as dots on a rate scale, not bars
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
from plotly.subplots import make_subplots

from eth_defi.vault_report.benchmarks import BTC, ETH, TREASURY_BILL
from eth_defi.vault_report.movers import RankChange
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
class PerformancePanel:
    """One vault in a performance chart grid."""

    #: Vault id, a column of the daily prices
    vault_id: str

    #: Vault name
    name: str

    #: Second header line, e.g. ``Ethereum · Lagoon Finance``
    subtitle: str

    #: Protocol logo data URI, or ``None``
    logo_uri: str | None

    #: Benchmark names the vault is compared with, see :py:func:`eth_defi.vault_report.benchmarks.select_benchmarks`
    benchmarks: tuple[str, ...]


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


def _benchmark_style(benchmark: str, theme: ChartTheme) -> dict:
    """Line style of a benchmark.

    :param benchmark:
        Benchmark name.

    :param theme:
        Chart theme.

    :return:
        Plotly line dictionary.
    """
    colours = {TREASURY_BILL: theme.benchmark, BTC: theme.btc, ETH: theme.eth}
    return {"color": colours[benchmark], "width": 2.5, "dash": "dash" if benchmark == TREASURY_BILL else "dot"}


def create_performance_grid_figure(
    panels: list[PerformancePanel],
    daily_prices: pd.DataFrame,
    benchmark_indices: dict[str, pd.Series],
    theme: ChartTheme,
    window: datetime.timedelta = datetime.timedelta(days=90),
    columns: int = 4,
) -> Figure:
    """Draw small multiples of vault performance against benchmarks.

    Each panel shows one vault's cumulative return over the same window, with
    its benchmarks rebased to the same start: the US Treasury bill for calm
    yield vaults, BTC and ETH for trading and volatile vaults. Every panel has
    its own y axis, so calm lending vaults are readable next to volatile ones.

    :param panels:
        Vaults in display order.

    :param daily_prices:
        Output of :py:func:`eth_defi.vault_report.data.calculate_daily_share_prices`.

    :param benchmark_indices:
        Output of :py:func:`eth_defi.vault_report.benchmarks.fetch_benchmark_indices`.

    :param theme:
        Chart theme.

    :param window:
        Performance period.

    :param columns:
        Panels per row.

    :return:
        Plotly figure.
    """
    rows = max(1, -(-len(panels) // columns))
    fig = make_subplots(rows=rows, cols=columns, shared_xaxes=True, horizontal_spacing=0.05, vertical_spacing=0.26 if rows > 1 else 0.1)
    end_at = daily_prices.index.max()
    start_at = end_at - pd.Timedelta(window)
    used_benchmarks: set[str] = set()

    for i, panel in enumerate(panels):
        row, col = divmod(i, columns)
        if panel.vault_id not in daily_prices.columns:
            logger.warning("No price data for vault %s, left out of the performance chart", panel.vault_id)
            continue
        vault = calculate_period_performance(daily_prices[panel.vault_id], start_at)
        if vault.empty:
            continue
        panel_start = vault.index[0]

        benchmark_labels = []
        for benchmark in panel.benchmarks:
            if benchmark not in benchmark_indices:
                continue
            values = benchmark_indices[benchmark].reindex(vault.index, method="ffill")
            performance = calculate_period_performance(values, panel_start)
            if performance.empty:
                continue
            used_benchmarks.add(benchmark)
            fig.add_trace(go.Scatter(x=performance.index, y=performance.to_numpy(), mode="lines", line=_benchmark_style(benchmark, theme), hoverinfo="skip", showlegend=False), row=row + 1, col=col + 1)
            benchmark_labels.append(f"<span style='color:{_benchmark_style(benchmark, theme)['color']}'>{benchmark} {performance.iloc[-1]:+.1f}%</span>")

        final = vault.iloc[-1]
        colour = theme.positive if final >= 0 else theme.negative
        fig.add_trace(go.Scatter(x=vault.index, y=vault.to_numpy(), mode="lines", line={"color": to_rgba(colour, 0.2), "width": 10}, hoverinfo="skip", showlegend=False), row=row + 1, col=col + 1)
        fig.add_trace(go.Scatter(x=vault.index, y=vault.to_numpy(), mode="lines", line={"color": colour, "width": 3.5}, showlegend=False), row=row + 1, col=col + 1)

        # Three header lines above the panel: name and return, chain and protocol, benchmark returns
        axis_suffix = "" if i == 0 else str(i + 1)
        x0, x1 = fig.layout[f"xaxis{axis_suffix}"].domain
        y1 = fig.layout[f"yaxis{axis_suffix}"].domain[1]
        return_text = f"{final:+.1f}%"
        panel_pixels = (x1 - x0) * (IMAGE_WIDTH - 60)
        logo_pixels = 34 if panel.logo_uri else 0
        name_chars = max(8, int((panel_pixels - logo_pixels - 13 * len(return_text) - 16) / 10))
        header_y = (y1 + 0.13, y1 + 0.082, y1 + 0.037)
        if panel.logo_uri:
            fig.add_layout_image(source=panel.logo_uri, xref="paper", yref="paper", x=x0, y=header_y[0], sizex=0.026, sizey=0.045, xanchor="left", yanchor="middle")
        fig.add_annotation(text=f"<b>{shorten_label(panel.name, name_chars)}</b>", xref="paper", yref="paper", x=x0, xshift=logo_pixels, y=header_y[0], xanchor="left", yanchor="middle", showarrow=False, font={"size": 17, "color": theme.text})
        fig.add_annotation(text=f"<b>{return_text}</b>", xref="paper", yref="paper", x=x1, y=header_y[0], xanchor="right", yanchor="middle", showarrow=False, font={"size": 20, "color": colour})
        young = panel_start > start_at + pd.Timedelta(days=3)
        since = f"since {panel_start:%b %d}" if young else ""
        fig.add_annotation(text=shorten_label(panel.subtitle, int(panel_pixels / 7.5) - len(since) - 2), xref="paper", yref="paper", x=x0, y=header_y[1], xanchor="left", yanchor="middle", showarrow=False, font={"size": 13, "color": theme.muted_text})
        if young:
            fig.add_annotation(text=since, xref="paper", yref="paper", x=x1, y=header_y[1], xanchor="right", yanchor="middle", showarrow=False, font={"size": 13, "color": theme.benchmark})
        if benchmark_labels:
            fig.add_annotation(text="vs " + " · ".join(benchmark_labels), xref="paper", yref="paper", x=x0, y=header_y[2], xanchor="left", yanchor="middle", showarrow=False, font={"size": 13, "color": theme.muted_text})

    apply_theme(fig, theme, IMAGE_WIDTH, 190 + 370 * rows)
    fig.update_layout(margin={"l": 30, "r": 30, "t": 120, "b": 110})
    fig.update_xaxes(showgrid=False, tickformat="%b %d", nticks=4, tickfont={"size": 14}, linecolor=theme.axis)
    fig.update_yaxes(side="left", ticksuffix="%", tickfont={"size": 14}, gridcolor=theme.grid, zeroline=True, zerolinecolor=theme.muted_text, zerolinewidth=1, nticks=5)

    legend = [f"<span style='color:{theme.positive}'>━━</span> Vault"]
    legend += [f"<span style='color:{_benchmark_style(b, theme)['color']}'>{'╌╌' if b == TREASURY_BILL else '┈┈'}</span> {b}" for b in (TREASURY_BILL, BTC, ETH) if b in used_benchmarks]
    fig.add_annotation(text="     ".join(legend), xref="paper", yref="paper", x=0.0, y=-0.1 if rows > 1 else -0.2, xanchor="left", yanchor="top", showarrow=False, font={"size": 17, "color": theme.text})
    return fig


def create_correlation_figure(
    daily_prices: pd.DataFrame,
    labels: dict[str, str],
    theme: ChartTheme,
    period: datetime.timedelta = datetime.timedelta(days=90),
) -> Figure:
    """Draw a daily returns correlation heatmap.

    The heatmap fills the plot area, so it has no watermark.

    :param daily_prices:
        Output of :py:func:`eth_defi.vault_report.data.calculate_daily_share_prices`.

    :param labels:
        Vault id -> axis label, in display order.

    :param theme:
        Chart theme.

    :param period:
        Correlation lookback period.

    :return:
        Plotly figure.
    """
    ids = [vault_id for vault_id in labels if vault_id in daily_prices.columns]
    prices = daily_prices[ids]
    prices = prices.loc[prices.index > prices.index.max() - period]
    correlation = prices.pct_change(fill_method=None).corr()
    names = [shorten_label(labels[vault_id]) for vault_id in ids]
    negative, neutral, positive = theme.diverging

    fig = go.Figure(
        data=go.Heatmap(
            z=correlation.to_numpy(),
            x=names,
            y=names,
            zmin=-1,
            zmax=1,
            colorscale=[[0.0, negative], [0.5, neutral], [1.0, positive]],
            text=correlation.map(lambda value: f"{value:.2f}" if pd.notna(value) else "").to_numpy(),
            texttemplate="%{text}",
            textfont={"size": 13, "color": theme.text},
            xgap=2,
            ygap=2,
            hoverongaps=False,
            colorbar={"tickfont": {"color": theme.muted_text}, "outlinewidth": 0},
        )
    )
    apply_theme(fig, theme, IMAGE_WIDTH, 1100)
    fig.update_layout(margin={"l": 320, "r": 40, "t": 20, "b": 240})
    fig.update_xaxes(tickangle=45, tickfont={"size": 14}, showline=False, ticks="")
    fig.update_yaxes(side="left", tickfont={"size": 14}, autorange="reversed", showgrid=False, showline=False)
    return fig


def create_chain_yield_figure(
    chain_yields: pd.DataFrame,
    vault_returns: pd.DataFrame,
    theme: ChartTheme,
    chain_logos: dict[str, str | None] | None = None,
    benchmark_yield: float | None = None,
    watermark_uri: str | None = None,
    max_return: float = 0.4,
) -> Figure:
    """Draw a dot plot of vault yields per chain against the Treasury bill.

    A yield is a position on a rate scale, not a quantity, so each chain is a
    row of dots rather than a bar: small dots for individual vaults, showing the
    spread, and a large dot for the TVL-weighted average. The right-hand column
    gives the average and its difference to the Treasury bill in percentage points.

    :param chain_yields:
        Output of :py:func:`eth_defi.vault_report.sections.calculate_chain_yields`.

    :param vault_returns:
        Vaults counted in the averages, with ``chain`` and ``one_month_cagr_best`` columns.

    :param theme:
        Chart theme.

    :param chain_logos:
        Chain name -> logo data URI.

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
    chain_logos = chain_logos or {}
    df = chain_yields.sort_values("avg_return")
    positions = {chain: position for position, chain in enumerate(df.index)}
    clip = max_return * 100

    points = vault_returns.loc[vault_returns["chain"].isin(positions)].copy()
    points["x"] = (points["one_month_cagr_best"] * 100).clip(lower=-5, upper=clip)
    points["marker"] = np.select([points["one_month_cagr_best"] * 100 > clip, points["one_month_cagr_best"] * 100 < -5], ["triangle-right", "triangle-left"], "circle")
    # Deterministic vertical jitter so dots of one chain do not sit on top of each other
    points["y"] = [positions[chain] + ((zlib.crc32(vault_id.encode()) % 1000) / 1000 - 0.5) * 0.44 for vault_id, chain in zip(points.index, points["chain"], strict=True)]

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

    height = max(IMAGE_HEIGHT, 140 + 46 * len(df))
    apply_theme(fig, theme, IMAGE_WIDTH, height)
    fig.update_layout(xaxis_title="1M annualised return (%)", margin={"l": 280, "r": 230, "t": 50, "b": 90})
    fig.update_xaxes(showgrid=True, gridcolor=theme.grid, range=[-6, clip + 2], ticksuffix="%", zeroline=True, zerolinecolor=theme.axis, zerolinewidth=1)
    fig.update_yaxes(showgrid=False, showticklabels=False, showline=False, zeroline=False, range=[-0.7, len(df) - 0.3])

    for position, (chain, row) in enumerate(df.iterrows()):
        fig.add_shape(type="line", xref="paper", yref="y", x0=0, x1=1, y0=position, y1=position, line={"color": theme.grid, "width": 1}, layer="below")
        logo = chain_logos.get(chain)
        if logo:
            fig.add_layout_image(source=logo, xref="paper", yref="y", x=-0.235, y=position, sizex=0.028, sizey=0.75, xanchor="left", yanchor="middle")
        fig.add_annotation(text=chain, xref="paper", yref="y", x=-0.2, y=position, xanchor="left", showarrow=False, font={"size": 20, "color": theme.text})
        spread = f"  {(row['avg_return'] - benchmark_yield) * 100:+.1f} pp" if benchmark_yield is not None else ""
        fig.add_annotation(
            text=f"<b>{row['avg_return']:.1%}</b><span style='color:{theme.muted_text}'>{spread}</span>",
            xref="paper",
            yref="y",
            x=1.02,
            y=position,
            xanchor="left",
            showarrow=False,
            font={"size": 19, "color": theme.text},
        )

    if benchmark_yield is not None:
        fig.add_vline(x=benchmark_yield * 100, line={"color": theme.benchmark, "width": 3, "dash": "dash"})
        fig.add_annotation(text=f"US 3M T-bill {benchmark_yield:.1%}", x=benchmark_yield * 100, xref="x", y=1.0, yref="paper", yanchor="bottom", showarrow=False, font={"size": 18, "color": theme.benchmark})
    add_watermark(fig, watermark_uri, theme)
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


def create_movers_figure(
    changes: list[RankChange],
    labels: dict[str, str],
    theme: ChartTheme,
    previous_label: str,
    current_label: str,
    logos: dict[str, str | None] | None = None,
) -> Figure:
    """Draw a slope chart of rank changes between two reports.

    Status is shown both by colour and by a text marker (▲, ▼, NEW), so colour
    is never the only signal.

    :param changes:
        Output of :py:func:`eth_defi.vault_report.movers.calculate_rank_changes`.

    :param labels:
        Vault id -> display name.

    :param theme:
        Chart theme.

    :param previous_label:
        Column heading of the previous ranking, e.g. ``February 2026``.

    :param current_label:
        Column heading of the current ranking.

    :param logos:
        Vault id -> protocol logo data URI.

    :return:
        Plotly figure.
    """
    logos = logos or {}
    top_n = len(changes)
    shown_previous = [c.previous_rank for c in changes if c.previous_rank is not None]
    bottom = max([top_n, *shown_previous]) + 1
    status_colour = {"new": theme.positive, "up": theme.positive, "down": theme.negative, "same": theme.muted_text}

    fig = go.Figure()
    for change in changes:
        colour = status_colour[change.status]
        if change.previous_rank is not None:
            fig.add_trace(go.Scatter(x=[0, 1], y=[change.previous_rank, change.current_rank], mode="lines+markers", line={"color": to_rgba(colour, 0.85), "width": 3}, marker={"size": 11, "color": colour}, hoverinfo="skip"))
            fig.add_annotation(text=f"#{change.previous_rank}", x=0, y=change.previous_rank, xanchor="right", xshift=-14, showarrow=False, font={"size": 15, "color": theme.muted_text})
        else:
            fig.add_trace(go.Scatter(x=[1], y=[change.current_rank], mode="markers", marker={"size": 13, "color": colour, "symbol": "star"}, hoverinfo="skip"))

        marker = {"new": "NEW", "up": f"▲{change.previous_rank - change.current_rank if change.previous_rank else ''}", "down": f"▼{change.current_rank - change.previous_rank if change.previous_rank else ''}", "same": "="}[change.status]
        label = shorten_label(labels.get(change.vault_id, change.vault_id), 32)
        text_x_shift = 72
        if logos.get(change.vault_id):
            fig.add_layout_image(source=logos[change.vault_id], xref="paper", yref="y", x=1.025, y=change.current_rank, sizex=0.045, sizey=0.8, xanchor="left", yanchor="middle")
        fig.add_annotation(
            text=f"<b>#{change.current_rank}</b> {label}  <span style='color:{colour}'>{marker}</span>",
            x=1,
            y=change.current_rank,
            xanchor="left",
            xshift=text_x_shift,
            showarrow=False,
            font={"size": 17, "color": theme.text},
        )

    apply_theme(fig, theme, IMAGE_WIDTH, max(IMAGE_HEIGHT, 90 + 44 * top_n))
    fig.update_layout(showlegend=False, margin={"l": 360, "r": 560, "t": 70, "b": 20})
    fig.update_xaxes(range=[-0.02, 1.02], showline=False, showticklabels=False, ticks="")
    fig.update_yaxes(range=[bottom, 0.3], showgrid=False, showticklabels=False, showline=False, zeroline=False)
    for x, text in ((0, previous_label), (1, current_label)):
        fig.add_annotation(text=f"<b>{text}</b>", x=x, y=1.0, yref="paper", yanchor="bottom", showarrow=False, font={"size": 20, "color": theme.text})
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
