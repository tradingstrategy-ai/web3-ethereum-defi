"""Build the TradingStrategy.ai logo assets of the monthly vault report.

The website's horizontal logo, ``logo-horizontal.svg`` copied from the
frontend, reads "Trading Strategy". The report charts use a version with a
``.ai`` suffix. The suffix is built from the logo's own glyphs, so it matches
the wordmark exactly: the full stop is the dot of the "i" moved to the
baseline, and the "a" and "i" are copies of the wordmark's letters.

The script writes:

- ``eth_defi/vault_report/assets/logo-horizontal-ai.svg``: the logo with the suffix
- ``logo-horizontal-ai-dark.png`` and ``logo-horizontal-ai-light.png``: PNG
  renders for dark and light chart themes, used by the Pillow-drawn panel
  footers and hero images, which cannot draw SVG

Rendering needs Chrome for Kaleido, see ``eth_defi/vault_report/README-vault-report.md``.

Example:

.. code-block:: shell

    poetry run python scripts/erc-4626/render-vault-report-logo.py
"""

import logging
import re
from pathlib import Path

import plotly.graph_objects as go
from PIL import Image
from tabulate import tabulate

from eth_defi.utils import setup_console_logging
from eth_defi.vault_report.branding import crop_to_content
from eth_defi.vault_report.charts import render_figure_png
from eth_defi.vault_report.logos import to_data_uri
from eth_defi.vault_report.theme import ASSETS_DIR, DARK_THEME

logger = logging.getLogger(__name__)

#: Rendered PNG height in pixels, 4× the logo's 60 unit view box
PNG_HEIGHT = 240

#: Gap between the suffix glyphs, in logo units, like the wordmark's own letter spacing
LETTER_SPACING = 1.2


def build_logo_svg(source: str) -> str:
    """Append a ``.ai`` suffix to the horizontal logo SVG.

    :param source:
        ``logo-horizontal.svg`` content.

    :return:
        SVG content with the suffix and a wider view box.
    """
    logotype = re.search(r'<path class="logotype" fill="(#[0-9A-Fa-f]{6})" d="([^"]+)"/>', source)
    assert logotype, "The logo has no logotype path"
    fill, path = logotype.groups()
    subpaths = ["M" + part for part in path.split("M") if part]

    def glyph(prefix: str) -> str:
        matches = [subpath for subpath in subpaths if subpath.startswith(prefix)]
        assert len(matches) == 1, f"Expected one logotype subpath starting with {prefix}, got {len(matches)}"
        return matches[0]

    # Glyphs of "Trading": the "a" outline and counter, the "i" stem and dot
    a = glyph("M108.445 41C") + glyph("M103.549 38.012C")
    i_stem, i_dot = glyph("M134.577 41V22.532"), glyph("M134.577 19.652V15.26")
    a_left, a_right, i_left, i_width, dot_bottom, baseline = 95.917, 113.161, 134.577, 4.896, 19.652, 41
    wordmark_right = float(re.search(r'viewBox="0 0 ([0-9.]+) ', source).group(1))

    period_x = wordmark_right + LETTER_SPACING
    a_x = period_x + i_width + LETTER_SPACING
    i_x = a_x + (a_right - a_left) + LETTER_SPACING
    width = i_x + i_width
    suffix = f'  <path class="logotype" fill="{fill}" transform="translate({period_x - i_left:.3f} {baseline - dot_bottom:.3f})" d="{i_dot}"/>\n  <path class="logotype" fill="{fill}" transform="translate({a_x - a_left:.3f} 0)" d="{a}"/>\n  <path class="logotype" fill="{fill}" transform="translate({i_x - i_left:.3f} 0)" d="{i_stem}{i_dot}"/>\n'
    return source.replace(f'viewBox="0 0 {wordmark_right:g} 60"', f'viewBox="0 0 {width:.1f} 60"').replace("</svg>", suffix + "</svg>")


def render_logo_png(svg: str, text_colour: str, path: Path) -> Path:
    """Render the logo SVG to a transparent PNG with the given wordmark colour.

    :param svg:
        Logo SVG content.

    :param text_colour:
        Wordmark colour, ``#rrggbb``.

    :param path:
        Output PNG.

    :return:
        ``path``.
    """
    recoloured = re.sub(r'(class="logotype" fill=")#[0-9A-Fa-f]{6}', rf"\g<1>{text_colour}", svg)
    view_width = float(re.search(r'viewBox="0 0 ([0-9.]+) 60"', svg).group(1))
    fig = go.Figure()
    fig.update_layout(width=round(PNG_HEIGHT * view_width / 60) + 8, height=PNG_HEIGHT + 8, margin={"l": 4, "r": 4, "t": 4, "b": 4}, xaxis_visible=False, yaxis_visible=False)
    fig.add_layout_image(source=to_data_uri(recoloured.encode(), "image/svg+xml"), xref="paper", yref="paper", x=0, y=1, sizex=1, sizey=1, xanchor="left", yanchor="top")
    render_figure_png(fig, path)
    crop_to_content(Image.open(path).convert("RGBA"), "#000000").save(path, optimize=True)
    return path


def main() -> None:
    """Write the logo SVG and its PNG renders."""
    setup_console_logging(default_log_level="info")
    svg = build_logo_svg((ASSETS_DIR / "logo-horizontal.svg").read_text())
    svg_path = ASSETS_DIR / "logo-horizontal-ai.svg"
    svg_path.write_text(svg)
    outputs = [
        svg_path,
        render_logo_png(svg, DARK_THEME.text, ASSETS_DIR / "logo-horizontal-ai-dark.png"),
        render_logo_png(svg, "#0B0B14", ASSETS_DIR / "logo-horizontal-ai-light.png"),
    ]
    rows = [[path.name, f"{Image.open(path).size[0]}×{Image.open(path).size[1]}" if path.suffix == ".png" else "SVG"] for path in outputs]
    print(tabulate(rows, headers=["File", "Size"], tablefmt="fancy_grid"))


if __name__ == "__main__":
    main()
