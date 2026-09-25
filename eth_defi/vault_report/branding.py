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

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from eth_defi.vault_report.sections import format_return
from eth_defi.vault_report.theme import ASSETS_DIR, FONT_REGULAR, FONT_SEMIBOLD, ChartTheme

logger = logging.getLogger(__name__)

#: Social image size used by LinkedIn, Telegram and Facebook link previews, and the Ghost feature image
HERO_SIZE = (1200, 630)

#: Square social image for X, which shows link previews of the blog as square ``summary`` cards
SQUARE_HERO_SIZE = (1080, 1080)

#: Panel corner radius in pixels, 1.5rem at 2× scale
PANEL_RADIUS = 36

#: Padding between the panel border and its content: the header text, the chart content and the footer
PANEL_PADDING = 44

#: Gap between the subtitle and the chart content, and between the chart content and the footer rule
PANEL_CONTENT_GAP = 28

#: Width of every chart panel, so charts show at the same scale in the post
PANEL_WIDTH = 1400

#: Colour distance from the surface above which a chart pixel counts as content
CONTENT_THRESHOLD = 6


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


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    """Word-wrap text to lines that fit a width.

    :param draw:
        Drawing context.

    :param text:
        Text.

    :param font:
        Font used to measure the text.

    :param max_width:
        Maximum line width in pixels.

    :return:
        Lines, at least one.
    """
    lines: list[str] = []
    for word in text.split():
        candidate = f"{lines[-1]} {word}" if lines else word
        if lines and draw.textlength(candidate, font=font) <= max_width:
            lines[-1] = candidate
        else:
            lines.append(word)
    return lines or [""]


def draw_brand_logo(image: Image.Image, x: int, y: int, height: int, theme: ChartTheme) -> int:
    """Draw the TradingStrategy.ai logo: the candle mark and the wordmark with the ``.ai`` suffix.

    Uses the PNG renders of ``logo-horizontal-ai.svg`` made by
    ``scripts/erc-4626/render-vault-report-logo.py``, because Pillow cannot draw SVG.

    :param image:
        RGBA image to draw on in place.

    :param x:
        Left edge.

    :param y:
        Top edge.

    :param height:
        Logo height in pixels; the candle mark fills it.

    :param theme:
        Chart theme; dark themes use the light wordmark.

    :return:
        Drawn logo width in pixels.
    """
    logo = Image.open(ASSETS_DIR / f"logo-horizontal-ai-{theme.name}.png").convert("RGBA")
    logo = logo.resize((round(logo.width * height / logo.height), height), Image.Resampling.LANCZOS)
    image.alpha_composite(logo, (x, y))
    return logo.width


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


def crop_to_content(image: Image.Image, background: str, threshold: int = CONTENT_THRESHOLD) -> Image.Image:
    """Crop a chart render to its drawn pixels.

    Plotly margins leave uneven empty space around axis titles, labels and
    legends. Cropping to the drawn content lets the panel apply the same
    padding on every side of every chart.

    :param image:
        Chart render on a transparent or uniform background.

    :param background:
        Background colour, ``#rrggbb``, for renders without transparency.

    :param threshold:
        Alpha, or summed RGB distance from the background, above which a pixel is content.

    :return:
        Cropped image, or the original image if it has no content.
    """
    pixels = np.asarray(image.convert("RGBA")).astype(int)
    if pixels[:, :, 3].min() < 255:
        # Transparent render: content is whatever is drawn
        content = pixels[:, :, 3] > threshold
    else:
        content = np.abs(pixels[:, :, :3] - np.array(_hex_to_rgba(background)[:3])).sum(axis=2) > threshold
    rows, columns = np.nonzero(content)
    if not len(rows):
        return image
    return image.crop((columns.min(), rows.min(), columns.max() + 1, rows.max() + 1))


def compose_chart_panel(chart_png: Path, theme: ChartTheme, title: str, subtitle: str, footer_note: str, link: str, output_path: Path) -> Path:
    """Frame a chart image in a branded panel.

    The chart is cropped to its content, resized to the inner width of a
    :py:data:`PANEL_WIDTH` panel and padded by :py:data:`PANEL_PADDING` on the
    left and right, and by :py:data:`PANEL_CONTENT_GAP` above and below, so
    every panel has the same size, scale and margins regardless of its Plotly layout.

    :param chart_png:
        Chart image rendered with the same theme surface colour.

    :param theme:
        Chart theme.

    :param title:
        Panel title, heading case.

    :param subtitle:
        Description of what the chart shows. Long titles and subtitles are word-wrapped.

    :param footer_note:
        Short footer text, e.g. the data date.

    :param link:
        Live chart URL shown in the footer, without ``https://``.

    :param output_path:
        Where to write the PNG. May be the same as ``chart_png``.

    :return:
        ``output_path``.
    """
    # The chart content gets the same padding on every side. Content is resized to the panel's inner width,
    # a few percent at most, because the Plotly layouts are tuned to fill it.
    content = crop_to_content(Image.open(chart_png).convert("RGBA"), theme.surface)
    pad, gap = PANEL_PADDING, PANEL_CONTENT_GAP
    width = PANEL_WIDTH
    inner_width = width - 2 * pad
    if content.width != inner_width:
        scale = inner_width / content.width
        if abs(scale - 1) > 0.05:
            logger.warning("Chart %s content is %d px wide, resized by %.0f%% to fit the panel", chart_png, content.width, (scale - 1) * 100)
        content = content.resize((inner_width, round(content.height * scale)), Image.Resampling.LANCZOS)
    # The footer text sits 44 px above the bottom edge, like the other panel margins
    footer_height = 107
    title_font, subtitle_font = _font(40, bold=True), _font(24)
    # Long titles and subtitles wrap to more lines, and the header grows to fit them
    measure = ImageDraw.Draw(content)
    title_lines = _wrap_text(measure, title, title_font, width - 2 * pad)
    subtitle_lines = _wrap_text(measure, subtitle, subtitle_font, width - 2 * pad)
    subtitle_top = 36 + 50 * len(title_lines) + 2
    header_height = subtitle_top + 32 * len(subtitle_lines)
    chart_height = gap + content.height + gap
    panel = Image.new("RGBA", (width, header_height + chart_height + footer_height), _hex_to_rgba(theme.surface))
    _draw_glow(panel, theme, radius=int(width * 0.35))
    panel.alpha_composite(content, (pad, header_height + gap))

    draw = ImageDraw.Draw(panel)
    for i, line in enumerate(title_lines):
        draw.text((pad, 36 + 50 * i), line, font=title_font, fill=theme.text)
    for i, line in enumerate(subtitle_lines):
        draw.text((pad, subtitle_top + 32 * i), line, font=subtitle_font, fill=theme.muted_text)

    footer_top = header_height + chart_height
    draw.line((pad, footer_top + 4, width - pad, footer_top + 4), fill=_hex_to_rgba(theme.axis, 90), width=1)
    text_y = footer_top + 30
    logo_width = draw_brand_logo(panel, pad, footer_top + 22, 41, theme)
    draw.text((pad + logo_width + 22, text_y + 1), footer_note, font=_font(22), fill=theme.muted_text)
    link_font = _font(22)
    draw.text((width - pad - draw.textlength(link, font=link_font), text_y + 1), link, font=link_font, fill=theme.muted_text)

    _round_corners(panel, theme).save(output_path, format="PNG", optimize=True)
    return output_path


def _draw_sparkline(image: Image.Image, values: pd.Series, box: tuple[int, int, int, int], colour: str) -> None:
    """Draw a small price line with a faint fill underneath.

    :param image:
        RGBA image to draw on.

    :param values:
        Prices in time order.

    :param box:
        (left, top, right, bottom) pixel box.

    :param colour:
        Line colour.
    """
    values = values.dropna()
    if len(values) < 2:
        return
    left, top, right, bottom = box
    low, high = float(values.min()), float(values.max())
    span = high - low or 1.0
    points = [(left + (right - left) * i / (len(values) - 1), bottom - (bottom - top) * (float(v) - low) / span) for i, v in enumerate(values)]
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.polygon([*points, (right, bottom), (left, bottom)], fill=_hex_to_rgba(colour, 40))
    draw.line(points, fill=_hex_to_rgba(colour), width=3, joint="curve")
    image.alpha_composite(layer)


def render_hero_image(
    vaults_df: pd.DataFrame,
    sparklines: dict[str, pd.Series],
    month_label: str,
    subtitle: str,
    theme: ChartTheme,
    output_path: Path,
    size: tuple[int, int] = HERO_SIZE,
    properties: dict[str, list[tuple[str, Image.Image | None]]] | None = None,
) -> Path:
    """Draw the social image of the month's top vaults.

    Each vault is a row with its name, its curator, protocol and chain with
    their icons under the name, a 90-day price sparkline and its return as a
    large number. Returns are shown as numbers rather than bars, because a
    yield is a rate, not a quantity.

    :param vaults_df:
        Top vaults in rank order, at most five are drawn.

    :param sparklines:
        Vault id -> daily share prices for the sparkline.

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

    :param properties:
        Vault id -> ``(text, icon)`` pairs in the order curator, protocol, chain,
        see :py:func:`eth_defi.vault_report.report.make_vault_properties`. Vaults
        without an entry show their chain and protocol as text.

    :return:
        ``output_path``.
    """
    properties = properties or {}
    width, height = size
    square = height > width * 0.8
    image = Image.new("RGBA", size, _hex_to_rgba(theme.page_background))
    _draw_glow(image, theme, radius=int(width * 0.6))
    draw = ImageDraw.Draw(image)
    pad = 56

    draw_brand_logo(image, pad, 42, 44, theme)
    badge = "Monthly vault report"
    badge_font = _font(20)
    badge_width = draw.textlength(badge, font=badge_font) + 36
    draw.rounded_rectangle((width - pad - badge_width, 44, width - pad, 84), radius=20, outline=_hex_to_rgba(theme.axis), width=2)
    draw.text((width - pad - badge_width + 18, 53), badge, font=badge_font, fill=theme.muted_text)

    title_top = 150 if square else 116
    draw.text((pad, title_top), "Best-performing stablecoin vaults", font=_font(50 if square else 52, bold=True), fill=theme.text)
    draw.text((pad, title_top + 64), month_label, font=_font(32), fill=theme.positive)

    rows = vaults_df.head(5)
    rows_top, row_height = (330, 118) if square else (250, 66)
    spark_left = int(width * (0.52 if square else 0.56))
    spark_right = spark_left + (200 if square else 240)
    header_font = _font(15)
    draw.text((spark_left, rows_top - 30), "90-day price", font=header_font, fill=theme.muted_text)
    header = "1M return, annualised"
    draw.text((width - pad - draw.textlength(header, font=header_font), rows_top - 30), header, font=header_font, fill=theme.muted_text)
    for rank, (vault_id, vault) in enumerate(rows.iterrows(), start=1):
        top = rows_top + (rank - 1) * row_height
        draw.text((pad, top + 12), str(rank), font=_font(28, bold=True), fill=theme.muted_text)
        name_x = pad + 44
        text_right = spark_left - 24
        draw.text((name_x, top + 2), _fit_text(draw, vault["name"] or vault["address"], _font(26, bold=True), text_right - name_x), font=_font(26, bold=True), fill=theme.text)
        # Curator, protocol and chain under the name, each with its own icon
        property_font = _font(18)
        x = name_x
        for text, icon in properties.get(vault_id, [(vault["protocol_label"], None), (vault["chain"], None)]):
            if icon is not None:
                if x + 22 > text_right:
                    break
                icon = icon.copy()
                icon.thumbnail((20, 20))
                image.alpha_composite(icon, (x, top + 36 + (20 - icon.height) // 2))
                x += 26
            if text_right - x < 40:
                break
            fitted = _fit_text(draw, text, property_font, text_right - x)
            draw.text((x, top + 34), fitted, font=property_font, fill=theme.muted_text)
            x += int(draw.textlength(fitted, font=property_font)) + 18
        if vault_id in sparklines:
            _draw_sparkline(image, sparklines[vault_id], (spark_left, top + 6, spark_right, top + 50), theme.positive)
            draw = ImageDraw.Draw(image)
        value = format_return(vault["one_month_cagr_net"], vault["one_month_cagr"]).replace(" (n)", "").replace(" (g)", "")
        value_font = _font(34, bold=True)
        draw.text((width - pad - draw.textlength(value, font=value_font), top + 6), value, font=value_font, fill=theme.positive)

    footer_font = _font(18)
    footer_text = _fit_text(draw, subtitle, footer_font, width - 2 * pad - 220)
    draw.text((pad, height - 50), footer_text, font=footer_font, fill=theme.muted_text)
    draw.text((width - pad - draw.textlength("tradingstrategy.ai", font=_font(20, bold=True)), height - 52), "tradingstrategy.ai", font=_font(20, bold=True), fill=theme.text)
    image.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path
