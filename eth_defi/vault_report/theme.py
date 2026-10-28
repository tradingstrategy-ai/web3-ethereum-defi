"""Chart themes for the monthly vault report.

The dark theme follows the visual identity of the
`vault dashboard <https://tradingstrategy.ai/vaults>`__: a near-black
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
The same font files serve two renderers: Kaleido's headless Chrome finds them
through a private fontconfig file, see
:py:func:`eth_defi.vault_report.charts.render_figure_png`, and Pillow loads
them directly for the panel frames, the hero images and the legend text
measurements, see :py:mod:`eth_defi.vault_report.branding`. Using one font in
both keeps Pillow's measured text widths equal to what Chrome draws.

The dark theme is the default, because the blog renders posts in dark mode;
``CHART_THEME=light`` selects the light theme, e.g. for newsletters, see
``README-vault-report.md``.
"""

from dataclasses import dataclass
from pathlib import Path

from plotly.graph_objects import Figure

#: Bundled report assets: the Inter fonts, the TradingStrategy.ai logo and its PNG renders,
#: the benchmark logos and the podcast service icons, see ``README-vault-report.md``
ASSETS_DIR = Path(__file__).parent / "assets"

#: Font family name Plotly asks Chrome for; Chrome resolves it to the bundled files in
#: :py:data:`ASSETS_DIR` through the private fontconfig of
#: :py:func:`eth_defi.vault_report.charts.render_figure_png`
FONT_FAMILY = "Inter"

#: Regular weight for Pillow-drawn text and for measuring Plotly text widths
FONT_REGULAR = ASSETS_DIR / "fonts" / "Inter-Regular.ttf"

#: Semibold weight for Pillow-drawn titles. It is the only heavier weight bundled, so Chrome
#: also draws Plotly ``<b>`` text with it, and legend layouts measure bold labels with it.
FONT_SEMIBOLD = ASSETS_DIR / "fonts" / "Inter-SemiBold.ttf"


@dataclass(slots=True, frozen=True)
class ChartTheme:
    """Colours for report charts, panel frames and hero images.

    Colours are CSS strings, ``#rrggbb`` unless noted, because Plotly takes
    them as is. Pillow code converts them, see
    :py:func:`eth_defi.vault_report.charts.to_rgba` and the branding helpers,
    so members that Pillow draws with must stay ``#rrggbb``.
    """

    #: Theme name, ``dark`` or ``light``. Also selects the logo variants: the brand logo PNG,
    #: ``logo-horizontal-ai-{name}.png``, and the protocol logo for this background, see
    #: :py:func:`eth_defi.vault_report.logos.load_protocol_logo_path`
    name: str

    #: Page colour behind a panel: the background of the hero images, on which the ranking card sits
    page_background: str

    #: Chart panel colour. Plotly figures are styled with it but rendered transparent, so the
    #: panel's own surface and glow show through, see :py:func:`eth_defi.vault_report.charts.render_figure_png`
    surface: str

    #: Titles and prominent labels
    text: str

    #: Axis labels and secondary text
    muted_text: str

    #: Axis lines, zero lines and the panel footer rule
    axis: str

    #: Grid lines; a translucent ``rgba()`` on the dark theme, so it is used by Plotly only
    grid: str

    #: Categorical series colours in fixed assignment order. Charts assign them by rank, so the
    #: length caps how many vaults or groups one chart can show; the order is the one checked
    #: for colour vision deficiency, so do not reorder or subset it except from the start
    series_colours: tuple[str, ...]

    #: Positive values and the brand accent: gains, above-T-bill averages and the hero returns
    positive: str

    #: Negative values: losses and outflows
    negative: str

    #: US Treasury bill reference lines; amber, so the risk-free rate stands apart from the series colours
    benchmark: str

    #: Low-emphasis neutral for the summed "Other" group of the TVL and risk and return charts
    neutral: str

    #: Top-left panel glow colour, RGBA 0-255. The low alpha keeps it a hint of the website's
    #: radial gradient after the Gaussian blur, see :py:mod:`eth_defi.vault_report.branding`
    glow: tuple[int, int, int, int]


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
    glow=(34, 180, 83, 46),
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
    glow=(34, 180, 83, 28),
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

    Sets the font, colours, size and default axes shared by every report
    chart. Chart titles are not set here: the branded panel draws them, see
    :py:func:`eth_defi.vault_report.branding.compose_chart_panel`. The
    margins and right-hand y axis are defaults only; each ``create_*_figure``
    function in :py:mod:`eth_defi.vault_report.charts` overrides them for its
    labels and legend, so call this first and adjust the layout afterwards.

    Sizes are design pixels. The 22 px base font is shown at about 11 px in
    the blog's content column, because charts are designed about twice as
    wide as they are displayed.

    :param fig:
        Figure to style in place.

    :param theme:
        Chart theme.

    :param width:
        Figure width in design pixels.

    :param height:
        Figure height in design pixels.

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
