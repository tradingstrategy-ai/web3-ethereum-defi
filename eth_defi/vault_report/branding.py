"""Branded frames and the social hero image for report charts.

Plotly cannot draw rounded panels, glows or brand footers, so charts are
rendered by Kaleido first and composited with Pillow afterwards:

- :py:func:`compose_chart_panel` puts a chart PNG in a rounded panel with a
  title header, a green corner glow and a brand footer carrying the data
  date and a link to the live chart, so a screenshotted chart still credits us.
- :py:func:`render_hero_image` draws the social image of the month's top
  vaults: 1200×630 for link previews and the Ghost feature image, and a square
  version for posting on X.

The look follows the website's chart panels, e.g. ``HistoricalTvlGroupChart.svelte``
in the frontend: radius 1.5rem, a faint top-left radial glow in the bullish
colour, and a 1 px highlight border.
"""

from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from eth_defi.vault_report.logos import load_protocol_logo_path
from eth_defi.vault_report.sections import format_return
from eth_defi.vault_report.theme import FONT_REGULAR, FONT_SEMIBOLD, ChartTheme

#: Social image size used by LinkedIn, Telegram and Facebook link previews, and the Ghost feature image
HERO_SIZE = (1200, 630)

#: Square social image for X, which shows link previews of the blog as square ``summary`` cards
SQUARE_HERO_SIZE = (1080, 1080)

#: Panel corner radius in pixels, 1.5rem at 2× scale
PANEL_RADIUS = 36

#: Brand mark candles as (x0, y0, x1, y1, colour) in the 60×60 ``brand-mark.svg`` view box
BRAND_MARK_CANDLES = (
    (5, 12, 15, 60, "#22B554"),
    (18, 12, 28, 36, "#F62F2F"),
    (31, 24, 41, 48, "#F62F2F"),
    (44, 0, 54, 48, "#22B554"),
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load the bundled Inter font.

    :param size:
        Font size in pixels.

    :param bold:
        Use the semibold weight.

    :return:
        Pillow font.
    """
    return ImageFont.truetype(str(FONT_SEMIBOLD if bold else FONT_REGULAR), size)


def _hex_to_rgba(colour: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """Convert ``#rrggbb`` to an RGBA tuple.

    :param colour:
        Hex colour.

    :param alpha:
        Alpha, 0-255.

    :return:
        RGBA tuple.
    """
    colour = colour.lstrip("#")
    return int(colour[0:2], 16), int(colour[2:4], 16), int(colour[4:6], 16), alpha


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    """Truncate text with an ellipsis to fit a width.

    :param draw:
        Drawing context.

    :param text:
        Text.

    :param font:
        Font used to measure the text.

    :param max_width:
        Maximum width in pixels.

    :return:
        Text that fits.
    """
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


def draw_brand_mark(image: Image.Image, x: int, y: int, size: int) -> None:
    """Draw the Trading Strategy candle brand mark.

    :param image:
        RGBA image to draw on.

    :param x:
        Left edge.

    :param y:
        Top edge.

    :param size:
        Width and height in pixels.
    """
    draw = ImageDraw.Draw(image)
    scale = size / 60
    for x0, y0, x1, y1, colour in BRAND_MARK_CANDLES:
        draw.rounded_rectangle((x + x0 * scale, y + y0 * scale, x + x1 * scale, y + y1 * scale), radius=5 * scale, fill=colour)


def _draw_glow(image: Image.Image, theme: ChartTheme, radius: int) -> None:
    """Draw the soft top-left corner glow.

    :param image:
        RGBA image to draw on in place.

    :param theme:
        Chart theme with the glow colour.

    :param radius:
        Glow radius in pixels.
    """
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((-radius, -radius, radius, radius), fill=theme.glow)
    glow = glow.filter(ImageFilter.GaussianBlur(radius // 2))
    image.alpha_composite(glow)


def _round_corners(image: Image.Image, theme: ChartTheme) -> Image.Image:
    """Clip an image to a rounded panel with a highlight border.

    :param image:
        Panel image.

    :param theme:
        Chart theme.

    :return:
        RGBA image with transparent corners.
    """
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=PANEL_RADIUS, fill=255)
    border_colour = (255, 255, 255, 30) if theme.name == "dark" else (0, 0, 0, 26)
    ImageDraw.Draw(image).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=PANEL_RADIUS, outline=border_colour, width=2)
    result = Image.new("RGBA", image.size, (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)
    return result


def compose_chart_panel(chart_png: Path, theme: ChartTheme, title: str, subtitle: str, footer_note: str, link: str, output_path: Path) -> Path:
    """Frame a chart image in a branded panel.

    :param chart_png:
        Chart image rendered with the same theme surface colour.

    :param theme:
        Chart theme.

    :param title:
        Panel title, heading case.

    :param subtitle:
        One-line description of what the chart shows.

    :param footer_note:
        Short footer text, e.g. the data date.

    :param link:
        Live chart URL shown in the footer, without ``https://``.

    :param output_path:
        Where to write the PNG. May be the same as ``chart_png``.

    :return:
        ``output_path``.
    """
    chart = Image.open(chart_png).convert("RGBA")
    header_height, footer_height, pad = 128, 84, 44
    panel = Image.new("RGBA", (chart.width, chart.height + header_height + footer_height), _hex_to_rgba(theme.surface))
    _draw_glow(panel, theme, radius=int(chart.width * 0.35))
    panel.alpha_composite(chart, (0, header_height))

    draw = ImageDraw.Draw(panel)
    width = panel.width
    draw.text((pad, 34), _fit_text(draw, title, _font(40, bold=True), width - 2 * pad), font=_font(40, bold=True), fill=theme.text)
    draw.text((pad, 86), _fit_text(draw, subtitle, _font(24), width - 2 * pad), font=_font(24), fill=theme.muted_text)

    footer_top = header_height + chart.height
    draw.line((pad, footer_top + 4, width - pad, footer_top + 4), fill=_hex_to_rgba(theme.axis, 90), width=1)
    text_y = footer_top + 30
    draw_brand_mark(panel, pad, text_y - 3, 30)
    brand_font = _font(24, bold=True)
    draw.text((pad + 42, text_y), "Trading Strategy", font=brand_font, fill=theme.text)
    note_x = pad + 42 + draw.textlength("Trading Strategy", font=brand_font) + 18
    draw.text((note_x, text_y + 1), footer_note, font=_font(22), fill=theme.muted_text)
    link_font = _font(22)
    draw.text((width - pad - draw.textlength(link, font=link_font), text_y + 1), link, font=link_font, fill=theme.muted_text)

    _round_corners(panel, theme).save(output_path, format="PNG", optimize=True)
    return output_path


def _draw_monogram(image: Image.Image, text: str, x: int, y: int, size: int, theme: ChartTheme) -> None:
    """Draw a round letter badge in place of a missing protocol logo.

    :param image:
        RGBA image to draw on.

    :param text:
        Name whose first letter is drawn.

    :param x:
        Left edge.

    :param y:
        Top edge.

    :param size:
        Diameter in pixels.

    :param theme:
        Chart theme.
    """
    draw = ImageDraw.Draw(image)
    draw.ellipse((x, y, x + size, y + size), fill=theme.neutral)
    letter = (text.strip()[:1] or "?").upper()
    font = _font(int(size * 0.55), bold=True)
    draw.text((x + size / 2, y + size / 2), letter, font=font, fill=theme.text, anchor="mm")


def render_hero_image(vaults_df: pd.DataFrame, month_label: str, subtitle: str, theme: ChartTheme, output_path: Path, size: tuple[int, int] = HERO_SIZE) -> Path:
    """Draw the social image of the month's top vaults.

    :param vaults_df:
        Top vaults in rank order, at most five are drawn. Must not contain
        capped outlier returns, which would dwarf the other bars.

    :param month_label:
        E.g. ``September 2026``.

    :param subtitle:
        Selection criteria shown in the footer.

    :param theme:
        Chart theme.

    :param output_path:
        Where to write the PNG.

    :param size:
        :py:data:`HERO_SIZE` or :py:data:`SQUARE_HERO_SIZE`.

    :return:
        ``output_path``.
    """
    width, height = size
    square = height > width * 0.8
    image = Image.new("RGBA", size, _hex_to_rgba(theme.page_background))
    _draw_glow(image, theme, radius=int(width * 0.6))
    draw = ImageDraw.Draw(image)
    pad = 56

    draw_brand_mark(image, pad, 44, 40)
    draw.text((pad + 54, 48), "Trading Strategy", font=_font(28, bold=True), fill=theme.text)
    badge = "Monthly vault report"
    badge_font = _font(20)
    badge_width = draw.textlength(badge, font=badge_font) + 36
    draw.rounded_rectangle((width - pad - badge_width, 44, width - pad, 84), radius=20, outline=_hex_to_rgba(theme.axis), width=2)
    draw.text((width - pad - badge_width + 18, 53), badge, font=badge_font, fill=theme.muted_text)

    title_top = 150 if square else 116
    draw.text((pad, title_top), "Best-performing stablecoin vaults", font=_font(50 if square else 52, bold=True), fill=theme.text)
    draw.text((pad, title_top + 64), month_label, font=_font(32), fill=theme.positive)

    rows = vaults_df.head(5)
    max_return = max(rows["one_month_cagr_best"].max(), 1e-9) if len(rows) else 1.0
    rows_top, row_height = (330, 118) if square else (250, 66)
    bar_left = int(width * (0.56 if square else 0.575))
    bar_max = width - bar_left - pad - 150
    for rank, (_, vault) in enumerate(rows.iterrows(), start=1):
        top = rows_top + (rank - 1) * row_height
        draw.text((pad, top + 12), str(rank), font=_font(28, bold=True), fill=theme.muted_text)
        logo_path = load_protocol_logo_path(vault["protocol_slug"], theme)
        protocol = vault["protocol"] if vault["protocol_slug"] != "protocol-not-yet-identified" else "Unknown protocol"
        if logo_path:
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((40, 40))
            image.alpha_composite(logo, (pad + 40, top + 8 + (40 - logo.height) // 2))
        else:
            _draw_monogram(image, protocol, pad + 40, top + 8, 40, theme)
        name_x = pad + 96
        draw.text((name_x, top + 2), _fit_text(draw, vault["name"] or vault["address"], _font(26, bold=True), bar_left - name_x - 24), font=_font(26, bold=True), fill=theme.text)
        draw.text((name_x, top + 34), _fit_text(draw, f"{vault['chain']} · {protocol}", _font(18), bar_left - name_x - 24), font=_font(18), fill=theme.muted_text)
        bar_width = max(8, int(bar_max * vault["one_month_cagr_best"] / max_return))
        draw.rounded_rectangle((bar_left, top + 14, bar_left + bar_width, top + 42), radius=14, fill=theme.positive)
        value = format_return(vault["one_month_cagr_net"], vault["one_month_cagr"])
        draw.text((bar_left + bar_width + 14, top + 11), value.replace(" (n)", "").replace(" (g)", ""), font=_font(26, bold=True), fill=theme.text)

    footer_font = _font(18)
    footer_text = _fit_text(draw, subtitle, footer_font, width - 2 * pad - 220)
    draw.text((pad, height - 50), footer_text, font=footer_font, fill=theme.muted_text)
    draw.text((width - pad - draw.textlength("tradingstrategy.ai", font=_font(20, bold=True)), height - 52), "tradingstrategy.ai", font=_font(20, bold=True), fill=theme.text)
    image.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path
