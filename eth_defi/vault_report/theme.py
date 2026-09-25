"""Chart themes for the monthly vault report.

The dark theme follows the visual identity of the
`vault dashboard <https://tradingstrategy.ai/trading-view/vaults>`__: a near-black
surface, slate axes, light labels, the green and red brand colours and an
amber benchmark colour. The light theme is the original report style.

Categorical series colours use a fixed-order eight-colour palette whose adjacent
pairs stay distinguishable with colour vision deficiency. Each theme uses the
palette's own lightness steps for its surface. The palettes were checked on
2026-09-25 with an OKLab palette checker outside this repository: the worst
adjacent colour-vision-deficiency difference was ΔE 9.1 for the light palette
against ``#fcfcfb`` and ΔE 8.4 for the dark palette against ``#232322``
(target ≥ 8). All dark colours have at least 3:1 contrast on the dark surface.
The website's 20-colour protocol palette is not used, because several of its
adjacent pairs are not distinguishable with colour vision deficiency.

Charts use the bundled `Inter <https://rsms.me/inter/>`__ font (SIL Open Font
Licence), the closest freely licensed match to the website's Neue Haas Grotesk.
"""

from dataclasses import dataclass
from pathlib import Path

from plotly.graph_objects import Figure

#: Bundled fonts and brand images
ASSETS_DIR = Path(__file__).parent / "assets"

#: Font family name registered from :py:data:`ASSETS_DIR`
FONT_FAMILY = "Inter"

#: Font files for Pillow-drawn text
FONT_REGULAR = ASSETS_DIR / "fonts" / "Inter-Regular.ttf"
FONT_SEMIBOLD = ASSETS_DIR / "fonts" / "Inter-SemiBold.ttf"


@dataclass(slots=True, frozen=True)
class ChartTheme:
    """Colours and typography for report charts."""

    #: Theme name, ``dark`` or ``light``
    name: str

    #: Background around the chart panel
    page_background: str

    #: Chart panel and plot background
    surface: str

    #: Titles and prominent labels
    text: str

    #: Axis labels and secondary text
    muted_text: str

    #: Axis lines and panel border
    axis: str

    #: Grid lines
    grid: str

    #: Categorical series colours in fixed assignment order
    series_colours: tuple[str, ...]

    #: Positive values and brand accent
    positive: str

    #: Negative values
    negative: str

    #: Benchmark lines such as US Treasury bills
    benchmark: str

    #: Low-emphasis neutral for "Other" and unclassified groups
    neutral: str

    #: BTC benchmark line, the Bitcoin brand orange as on the website
    btc: str

    #: ETH benchmark line, the Ethereum brand blue as on the website
    eth: str

    #: Top-left panel glow colour, RGBA
    glow: tuple[int, int, int, int]

    #: Watermark logo colour
    watermark: str

    #: Watermark opacity, 0-1
    watermark_opacity: float


DARK_THEME = ChartTheme(
    name="dark",
    page_background="#1a1a19",
    surface="#232322",
    text="#f8f7f5",
    muted_text="#c9c7c3",
    axis="#64748b",
    grid="rgba(148,163,184,0.18)",
    series_colours=("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"),
    positive="#22b453",
    negative="#f97676",
    benchmark="#fbbf24",
    neutral="#5e5c5a",
    btc="#f7931a",
    eth="#8c9eff",
    glow=(34, 180, 83, 46),
    watermark="#d5deea",
    watermark_opacity=0.07,
)

LIGHT_THEME = ChartTheme(
    name="light",
    page_background="#f4f3f0",
    surface="#ffffff",
    text="#0b0b0b",
    muted_text="#52514e",
    axis="#c3c2bd",
    grid="#e6e5e1",
    series_colours=("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"),
    positive="#0f8a3c",
    negative="#d03b3b",
    benchmark="#b7791f",
    neutral="#b9b8b3",
    btc="#d97706",
    eth="#4f63d2",
    glow=(34, 180, 83, 28),
    watermark="#0b0b14",
    watermark_opacity=0.06,
)

#: Available themes by name
THEMES = {theme.name: theme for theme in (DARK_THEME, LIGHT_THEME)}


def get_theme(name: str) -> ChartTheme:
    """Look up a theme by name.

    :param name:
        ``dark`` or ``light``.

    :return:
        The theme.
    """
    assert name in THEMES, f"Unknown chart theme {name}, choose from {', '.join(THEMES)}"
    return THEMES[name]


def apply_theme(fig: Figure, theme: ChartTheme, width: int, height: int) -> Figure:
    """Apply the theme to a figure.

    Chart titles are not set here: the branded panel draws them, see
    :py:func:`eth_defi.vault_report.branding.compose_chart_panel`.

    :param fig:
        Figure to style in place.

    :param theme:
        Chart theme.

    :param width:
        Image width in pixels.

    :param height:
        Image height in pixels.

    :return:
        The same figure.
    """
    fig.update_layout(
        font={"family": FONT_FAMILY, "size": 22, "color": theme.muted_text},
        paper_bgcolor=theme.surface,
        plot_bgcolor=theme.surface,
        width=width,
        height=height,
        margin={"l": 40, "r": 110, "t": 30, "b": 70},
        hoverlabel={"font": {"family": FONT_FAMILY}},
    )
    fig.update_xaxes(showgrid=False, linecolor=theme.axis, ticks="outside", tickcolor=theme.axis, title_font={"color": theme.text, "size": 22})
    fig.update_yaxes(side="right", gridcolor=theme.grid, zerolinecolor=theme.axis, zerolinewidth=1, linecolor=theme.axis, title_font={"color": theme.text, "size": 22})
    return fig
