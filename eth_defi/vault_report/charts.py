"""Static charts for the monthly vault report.

Charts are rendered as PNG images with Plotly and
`Kaleido <https://github.com/plotly/Kaleido>`__ for upload to the blog.
Kaleido 1.x needs a Chrome binary; see :py:func:`render_figure_png`.

Chart styling:

- Fixed-order eight-colour categorical palette whose adjacent colours stay
  distinguishable with colour vision deficiency; charts draw at most eight series
- Diverging blue-red palette with a neutral grey midpoint for correlations
- Recessive grid and axes, large fonts for mobile readers
"""

import datetime
import logging
import os
import textwrap
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.graph_objects import Figure

logger = logging.getLogger(__name__)

#: Categorical series colours, assigned in this fixed order
SERIES_COLOURS = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
]

#: Diverging colour scale for correlations: red (-1) - grey (0) - blue (+1)
DIVERGING_COLOUR_SCALE = [
    [0.0, "#e34948"],
    [0.5, "#f0efec"],
    [1.0, "#2a78d6"],
]

#: Single-series bar colour
BAR_COLOUR = "#2a78d6"

SURFACE_COLOUR = "#ffffff"
TEXT_COLOUR = "#0b0b0b"
MUTED_TEXT_COLOUR = "#52514e"
GRID_COLOUR = "#e6e5e1"

#: Rendered image size. The blog shows images at 720 px width,
#: so the images are rendered at roughly 2x for sharp display.
IMAGE_WIDTH = 1400
IMAGE_HEIGHT = 800

#: Chrome downloaded by ``plotly_get_chrome`` / ``kaleido_get_chrome`` on Linux
CHOREOGRAPHER_CHROME_PATH = Path("~/.local/share/choreographer/deps/chrome-linux64/chrome").expanduser()


def _apply_layout(fig: Figure, title: str, height: int = IMAGE_HEIGHT) -> Figure:
    """Apply the shared report chart style.

    :param fig:
        Figure to style in place.

    :param title:
        Chart title.

    :param height:
        Image height in pixels.

    :return:
        The same figure.
    """
    fig.update_layout(
        title={"text": title, "x": 0.5, "xanchor": "center", "font": {"size": 34, "color": TEXT_COLOUR}},
        font={"family": "Inter, Helvetica, Arial, sans-serif", "size": 22, "color": MUTED_TEXT_COLOUR},
        paper_bgcolor=SURFACE_COLOUR,
        plot_bgcolor=SURFACE_COLOUR,
        width=IMAGE_WIDTH,
        height=height,
        margin={"l": 90, "r": 40, "t": 110, "b": 80},
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID_COLOUR, ticks="outside", tickcolor=GRID_COLOUR)
    fig.update_yaxes(gridcolor=GRID_COLOUR, zerolinecolor=MUTED_TEXT_COLOUR, zerolinewidth=1)
    return fig


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
    """Create a chart legend label for a vault.

    :param row:
        Vault metrics row.

    :return:
        E.g. ``Steakhouse USDC (Base)``.
    """
    return f"{row['name'] or row['address']} ({row['chain']})"


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
    title: str,
    history: datetime.timedelta = datetime.timedelta(days=180),
) -> Figure:
    """Draw a three-month rolling returns line chart.

    :param rolling_returns:
        Output of :py:func:`calculate_rolling_returns`, columns are vault ids.

    :param labels:
        Vault id -> legend label, in the display order. At most eight vaults.

    :param title:
        Chart title.

    :param history:
        How far back to draw.

    :return:
        Plotly figure.
    """
    assert len(labels) <= len(SERIES_COLOURS), f"Rolling returns chart supports at most {len(SERIES_COLOURS)} vaults, got {len(labels)}"
    rolling_returns = rolling_returns.loc[rolling_returns.index >= rolling_returns.index.max() - history]

    fig = go.Figure()
    for colour, (vault_id, label) in zip(SERIES_COLOURS, labels.items(), strict=False):
        if vault_id not in rolling_returns.columns:
            logger.warning("No price data for vault %s, not drawn in chart %s", vault_id, title)
            continue
        series = rolling_returns[vault_id].dropna()
        fig.add_trace(go.Scatter(x=series.index, y=series.to_numpy(), mode="lines", name="<br>".join(textwrap.wrap(label, width=28)), line={"color": colour, "width": 4}))

    _apply_layout(fig, title)
    fig.update_layout(
        xaxis_title="Date",
        yaxis_title="3M rolling returns (%)",
        legend={"title": {"text": "Vault"}, "font": {"size": 18}, "itemsizing": "constant"},
    )
    return fig


def create_correlation_figure(
    daily_prices: pd.DataFrame,
    labels: dict[str, str],
    period: datetime.timedelta = datetime.timedelta(days=90),
) -> Figure:
    """Draw a daily returns correlation heatmap.

    :param daily_prices:
        Output of :py:func:`eth_defi.vault_report.data.calculate_daily_share_prices`.

    :param labels:
        Vault id -> axis label, in display order.

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

    fig = go.Figure(
        data=go.Heatmap(
            z=correlation.to_numpy(),
            x=names,
            y=names,
            zmin=-1,
            zmax=1,
            colorscale=DIVERGING_COLOUR_SCALE,
            text=correlation.map(lambda value: f"{value:.2f}" if pd.notna(value) else "").to_numpy(),
            texttemplate="%{text}",
            textfont={"size": 13},
            xgap=2,
            ygap=2,
            hoverongaps=False,
        )
    )
    _apply_layout(fig, f"Vault daily returns correlation, last {period.days} days", height=1100)
    fig.update_layout(margin={"l": 320, "r": 40, "t": 110, "b": 240})
    fig.update_xaxes(tickangle=45, tickfont={"size": 14}, showline=False, ticks="")
    fig.update_yaxes(tickfont={"size": 14}, autorange="reversed", showgrid=False)
    return fig


def create_chain_yield_figure(chain_yields: pd.DataFrame) -> Figure:
    """Draw a horizontal bar chart of average vault yield per chain.

    :param chain_yields:
        Output of :py:func:`eth_defi.vault_report.sections.calculate_chain_yields`.

    :return:
        Plotly figure.
    """
    df = chain_yields.sort_values("avg_return")
    fig = go.Figure(
        go.Bar(
            x=df["avg_return"] * 100,
            y=df.index,
            orientation="h",
            marker={"color": BAR_COLOUR, "cornerradius": 4},
            text=[f"{value:.1%}" for value in df["avg_return"]],
            textposition="outside",
            textfont={"color": TEXT_COLOUR, "size": 18},
            cliponaxis=False,
        )
    )
    _apply_layout(fig, "Average stablecoin vault yield by blockchain", height=max(IMAGE_HEIGHT, 160 + 42 * len(df)))
    fig.update_layout(
        xaxis_title="TVL-weighted 1M annualised return (%)",
        bargap=0.25,
        margin={"l": 200, "r": 110, "t": 110, "b": 90},
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID_COLOUR, rangemode="tozero")
    fig.update_yaxes(showgrid=False, tickfont={"size": 20})
    return fig


def render_figure_png(fig: Figure, path: Path) -> Path:
    """Render a figure as a PNG image.

    Kaleido 1.x renders images with a headless Chrome. If ``BROWSER_PATH``
    is not set and Chrome has been downloaded with ``plotly_get_chrome``
    to the default choreographer location, that Chrome is used.

    :param fig:
        Plotly figure with width and height set in its layout.

    :param path:
        Output file.

    :return:
        The output path.
    """
    if not os.environ.get("BROWSER_PATH") and CHOREOGRAPHER_CHROME_PATH.exists():
        os.environ["BROWSER_PATH"] = str(CHOREOGRAPHER_CHROME_PATH)

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
