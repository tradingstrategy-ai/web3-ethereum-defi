"""Static charts for the monthly vault report.

Charts are Plotly figures rendered to PNG by
`Kaleido <https://github.com/plotly/Kaleido>`__ with headless Chrome, then framed
by :py:mod:`eth_defi.vault_report.branding`. Styling follows
:py:mod:`eth_defi.vault_report.theme`:

- At most eight categorical series, in the theme's fixed colour order
- Legends with protocol logos instead of plain colour boxes
- The US Treasury bill as an amber dashed benchmark
- A faint logo watermark inside the plot area, as on the website charts
- Glowing lines for charts with few series, like the website's hero charts
"""

import datetime
import logging
import os
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.graph_objects import Figure

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


def make_vault_label(row: pd.Series) -> str:
    """Create a chart label for a vault.

    :param row:
        Vault metrics row.

    :return:
        E.g. ``Steakhouse USDC (Base)``.
    """
    return f"{row['name'] or row['address']} ({row['chain']})"


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
        text_x = 1.09
        if entry.logo_uri:
            fig.add_layout_image(source=entry.logo_uri, xref="paper", yref="paper", x=1.09, y=y, sizex=0.034, sizey=0.05, xanchor="left", yanchor="middle")
            text_x = 1.132
        fig.add_annotation(
            text="<br>".join(textwrap.wrap(entry.label, width=24)[:2]),
            xref="paper",
            yref="paper",
            x=text_x,
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


def calculate_rolling_returns(
    daily_prices: pd.DataFrame,
    window: datetime.timedelta = datetime.timedelta(days=90),
) -> pd.DataFrame:
    """Calculate rolling returns from daily share prices.

    For vaults younger than the window, returns are calculated since the
    vault inception, so new vaults are visible in the chart.

    :param daily_prices:
        Output of :py:func:`eth_defi.vault_report.data.calculate_daily_share_prices`.

    :param window:
        Rolling return window.

    :return:
        Same shape as ``daily_prices``, rolling returns in percent.
    """
    if daily_prices.empty:
        return daily_prices
    first_prices = daily_prices.bfill().iloc[0]
    base = daily_prices.shift(window.days).fillna(first_prices)
    return (daily_prices / base - 1) * 100


def create_rolling_returns_figure(
    rolling_returns: pd.DataFrame,
    labels: dict[str, str],
    theme: ChartTheme,
    logos: dict[str, str | None] | None = None,
    benchmark: pd.Series | None = None,
    watermark_uri: str | None = None,
    history: datetime.timedelta = datetime.timedelta(days=180),
) -> Figure:
    """Draw a three-month rolling returns line chart.

    :param rolling_returns:
        Output of :py:func:`calculate_rolling_returns`, columns are vault ids.

    :param labels:
        Vault id -> legend label, in the display order. At most eight vaults.

    :param theme:
        Chart theme.

    :param logos:
        Vault id -> protocol logo data URI.

    :param benchmark:
        US Treasury bill rolling returns on the same index, see
        :py:func:`eth_defi.vault_report.benchmarks.calculate_treasury_bill_rolling_returns`.

    :param watermark_uri:
        Watermark logo data URI.

    :param history:
        How far back to draw.

    :return:
        Plotly figure.
    """
    assert len(labels) <= len(theme.series_colours), f"Rolling returns chart supports at most {len(theme.series_colours)} vaults, got {len(labels)}"
    logos = logos or {}
    rolling_returns = rolling_returns.loc[rolling_returns.index >= rolling_returns.index.max() - history]

    fig = go.Figure()
    entries = []
    few_series = len(labels) <= 3
    for colour, (vault_id, label) in zip(theme.series_colours, labels.items(), strict=False):
        if vault_id not in rolling_returns.columns:
            logger.warning("No price data for vault %s, not drawn in the rolling returns chart", vault_id)
            continue
        series = rolling_returns[vault_id].dropna()
        if few_series:
            add_glow_line(fig, series.index, series.to_numpy(), colour, label)
        else:
            fig.add_trace(go.Scatter(x=series.index, y=series.to_numpy(), mode="lines", name=label, line={"color": colour, "width": 3.5}))
        entries.append(LegendEntry(label, colour, logos.get(vault_id)))

    if benchmark is not None and benchmark.notna().any():
        benchmark = benchmark.loc[benchmark.index >= rolling_returns.index.min()].dropna()
        fig.add_trace(go.Scatter(x=benchmark.index, y=benchmark.to_numpy(), mode="lines", name="US 3M T-bill", line={"color": theme.benchmark, "width": 3, "dash": "dash"}))
        entries.append(LegendEntry("US 3M T-bill", theme.benchmark, dash="dash"))

    apply_theme(fig, theme, IMAGE_WIDTH, IMAGE_HEIGHT)
    fig.update_layout(margin={"l": 90, "r": LEGEND_MARGIN, "t": 30, "b": 70}, yaxis_title="3M rolling return (%)")
    fig.update_yaxes(side="left")
    add_logo_legend(fig, entries, theme)
    add_watermark(fig, watermark_uri, theme)
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
    theme: ChartTheme,
    chain_logos: dict[str, str | None] | None = None,
    benchmark_yield: float | None = None,
    watermark_uri: str | None = None,
) -> Figure:
    """Draw a horizontal bar chart of average vault yield per chain.

    :param chain_yields:
        Output of :py:func:`eth_defi.vault_report.sections.calculate_chain_yields`.

    :param theme:
        Chart theme.

    :param chain_logos:
        Chain name -> logo data URI.

    :param benchmark_yield:
        Latest US Treasury bill yield as a fraction, drawn as a reference line.

    :param watermark_uri:
        Watermark logo data URI.

    :return:
        Plotly figure.
    """
    chain_logos = chain_logos or {}
    df = chain_yields.sort_values("avg_return")
    fig = go.Figure(
        go.Bar(
            x=df["avg_return"] * 100,
            y=list(range(len(df))),
            orientation="h",
            marker={"color": [theme.positive if value >= 0 else theme.negative for value in df["avg_return"]], "cornerradius": 6},
            text=[f"{value:.1%}" for value in df["avg_return"]],
            textposition="outside",
            textfont={"color": theme.text, "size": 19},
            cliponaxis=False,
        )
    )
    height = max(IMAGE_HEIGHT, 120 + 44 * len(df))
    apply_theme(fig, theme, IMAGE_WIDTH, height)
    fig.update_layout(xaxis_title="TVL-weighted 1M annualised return (%)", bargap=0.28, margin={"l": 280, "r": 120, "t": 50, "b": 90})
    fig.update_xaxes(showgrid=True, gridcolor=theme.grid, rangemode="tozero")
    fig.update_yaxes(showgrid=False, showticklabels=False, showline=False, range=[-0.7, len(df) - 0.3])

    for position, chain in enumerate(df.index):
        logo = chain_logos.get(chain)
        if logo:
            fig.add_layout_image(source=logo, xref="paper", yref="y", x=-0.235, y=position, sizex=0.028, sizey=0.75, xanchor="left", yanchor="middle")
        fig.add_annotation(text=chain, xref="paper", yref="y", x=-0.2, y=position, xanchor="left", showarrow=False, font={"size": 20, "color": theme.text})

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
        Label the vaults with the highest returns directly.

    :return:
        Plotly figure.
    """
    df = vaults_df.dropna(subset=["three_months_volatility", "three_months_cagr_best", "current_nav"]).copy()
    df["x"] = (df["three_months_volatility"].clip(lower=1e-4)) * 100
    df["y"] = df["three_months_cagr_best"].clip(lower=-0.5, upper=max_return) * 100
    df["size"] = np.sqrt(df["current_nav"])
    df["category"] = df["strategy_tags"].apply(lambda tags: category_labels.get(tags[0], tags[0]) if isinstance(tags, list) and tags else "Unknown")
    top_categories = df["category"].value_counts().index[: len(theme.series_colours) - 1].tolist()
    df["category"] = df["category"].where(df["category"].isin(top_categories), "Other")

    fig = go.Figure()
    size_ref = 2.0 * df["size"].max() / (46**2)
    colours = dict(zip(top_categories, theme.series_colours, strict=False)) | {"Other": theme.muted_text}
    for category in [*top_categories, "Other"]:
        group = df.loc[df["category"] == category]
        if group.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=group["x"],
                y=group["y"],
                mode="markers",
                name=f"{category} ({len(group)})",
                marker={"size": group["size"], "sizemode": "area", "sizeref": size_ref, "sizemin": 4, "color": to_rgba(colours[category], 0.7), "line": {"color": theme.surface, "width": 1.5}},
            )
        )

    for _, vault in df.nlargest(label_count, "y").iterrows():
        fig.add_annotation(x=np.log10(vault["x"]), y=vault["y"], text=shorten_label(vault["name"] or vault["address"], 26), showarrow=True, arrowcolor=theme.axis, ax=30, ay=-24, font={"size": 15, "color": theme.text})

    if benchmark_yield is not None:
        fig.add_hline(y=benchmark_yield * 100, line={"color": theme.benchmark, "width": 3, "dash": "dash"})
        fig.add_annotation(text=f"US 3M T-bill {benchmark_yield:.1%}", xref="paper", x=0.0, y=benchmark_yield * 100, yanchor="bottom", xanchor="left", showarrow=False, font={"size": 17, "color": theme.benchmark})

    apply_theme(fig, theme, IMAGE_WIDTH, 900)
    fig.update_layout(
        xaxis_title="3M volatility, annualised (%, log scale)",
        yaxis_title="3M return, annualised (%)",
        margin={"l": 90, "r": 330, "t": 30, "b": 80},
        legend={"font": {"size": 17, "color": theme.text}, "x": 1.02, "y": 1, "xanchor": "left", "itemsizing": "constant", "title": {"text": "Strategy", "font": {"color": theme.text}}},
    )
    fig.update_xaxes(type="log", showgrid=True, gridcolor=theme.grid)
    fig.update_yaxes(side="left")
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
        text_x_shift = 58 if logos.get(change.vault_id) else 18
        if logos.get(change.vault_id):
            fig.add_layout_image(source=logos[change.vault_id], xref="paper", yref="y", x=1.0, y=change.current_rank, sizex=0.045, sizey=0.8, xanchor="left", yanchor="middle")
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
    colours = [*theme.series_colours[: len(tvl_by_protocol.columns) - 1], theme.muted_text] if "Other" in tvl_by_protocol.columns else list(theme.series_colours)
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
