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

#: Resolution multiplier of the chart panels.
#:
#: Layouts, fonts and margins are designed in :py:data:`PANEL_WIDTH` pixels;
#: Kaleido and the panel frame render them at this scale for sharper images
#: on high-density screens, e.g. 1400 px designs export 1867 px wide.
CHART_SCALE = 4 / 3

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


def _round_corners(image: Image.Image, theme: ChartTheme, scale: float = 1.0) -> Image.Image:
    """Clip an image to a rounded panel with a highlight border.

    :param image:
        Panel image.

    :param theme:
        Chart theme.

    :param scale:
        Resolution multiplier for the corner radius and border width.

    :return:
        RGBA image with transparent corners.
    """
    mask = Image.new("L", image.size, 0)
    radius = round(PANEL_RADIUS * scale)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
    border_colour = (255, 255, 255, 30) if theme.name == "dark" else (0, 0, 0, 26)
    ImageDraw.Draw(image).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, outline=border_colour, width=round(2 * scale))
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


def compose_chart_panel(chart_png: Path, theme: ChartTheme, title: str, subtitle: str, footer_note: str, link: str, output_path: Path, scale: float = CHART_SCALE) -> Path:
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

    :param scale:
        Resolution multiplier: the chart must be rendered at the same scale,
        see :py:func:`eth_defi.vault_report.charts.render_figure_png`.

    :return:
        ``output_path``.
    """

    def px(value: float) -> int:
        """Scale a design pixel measure to the output resolution."""
        return round(value * scale)

    # The chart content gets the same padding on every side. Content is resized to the panel's inner width,
    # a few percent at most, because the Plotly layouts are tuned to fill it.
    content = crop_to_content(Image.open(chart_png).convert("RGBA"), theme.surface)
    pad, gap = px(PANEL_PADDING), px(PANEL_CONTENT_GAP)
    width = px(PANEL_WIDTH)
    inner_width = width - 2 * pad
    if content.width != inner_width:
        resize_ratio = inner_width / content.width
        if abs(resize_ratio - 1) > 0.05:
            logger.warning("Chart %s content is %d px wide, resized by %.0f%% to fit the panel", chart_png, content.width, (resize_ratio - 1) * 100)
        content = content.resize((inner_width, round(content.height * resize_ratio)), Image.Resampling.LANCZOS)
    # The footer text sits 44 px above the bottom edge, like the other panel margins
    footer_height = px(107)
    title_font, subtitle_font = _font(px(40), bold=True), _font(px(24))
    # Long titles and subtitles wrap to more lines, and the header grows to fit them
    measure = ImageDraw.Draw(content)
    title_lines = _wrap_text(measure, title, title_font, width - 2 * pad)
    subtitle_lines = _wrap_text(measure, subtitle, subtitle_font, width - 2 * pad)
    subtitle_top = px(36 + 50 * len(title_lines) + 2)
    header_height = subtitle_top + px(32) * len(subtitle_lines)
    chart_height = gap + content.height + gap
    panel = Image.new("RGBA", (width, header_height + chart_height + footer_height), _hex_to_rgba(theme.surface))
    _draw_glow(panel, theme, radius=int(width * 0.35))
    panel.alpha_composite(content, (pad, header_height + gap))

    draw = ImageDraw.Draw(panel)
    for i, line in enumerate(title_lines):
        draw.text((pad, px(36 + 50 * i)), line, font=title_font, fill=theme.text)
    for i, line in enumerate(subtitle_lines):
        draw.text((pad, subtitle_top + px(32) * i), line, font=subtitle_font, fill=theme.muted_text)

    footer_top = header_height + chart_height
    draw.line((pad, footer_top + px(4), width - pad, footer_top + px(4)), fill=_hex_to_rgba(theme.axis, 90), width=px(1))
    text_y = footer_top + px(31)
    logo_width = draw_brand_logo(panel, pad, footer_top + px(22), px(41), theme)
    footer_font = _font(px(22))
    draw.text((pad + logo_width + px(22), text_y), footer_note, font=footer_font, fill=theme.muted_text)
    draw.text((width - pad - draw.textlength(link, font=footer_font), text_y), link, font=footer_font, fill=theme.muted_text)

    _round_corners(panel, theme, scale).save(output_path, format="PNG", optimize=True)
    return output_path


def render_logo_tile(logo_path: Path, theme: ChartTheme, output_path: Path, size: int = 96) -> Path:
    """Draw a logo centred on a rounded tile of the panel surface colour.

    Protocol logos are made for either dark or light backgrounds, and many are
    white. On a tile of the chart panel colour a logo reads the same on the
    dark blog page and in light newsletter emails.

    :param logo_path:
        Logo PNG for the theme's surface, see :py:func:`eth_defi.vault_report.logos.load_protocol_logo_path`.

    :param theme:
        Chart theme with the surface colour.

    :param output_path:
        Where to write the PNG.

    :param size:
        Tile width and height in pixels; shown at half size for high-density screens.

    :return:
        ``output_path``.
    """
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=_hex_to_rgba(theme.surface))
    logo = Image.open(logo_path).convert("RGBA")
    logo.thumbnail((round(size * 0.7), round(size * 0.7)), Image.Resampling.LANCZOS)
    tile.alpha_composite(logo, ((size - logo.width) // 2, (size - logo.height) // 2))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tile.save(output_path, format="PNG", optimize=True)
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

    # Header: logo left, edition and selection criteria right
    draw_brand_logo(image, pad, 40, 40, theme)
    edition_font, criteria_font = _font(22, bold=True), _font(16)
    draw.text((width - pad - draw.textlength(month_label, font=edition_font), 40), month_label, font=edition_font, fill=theme.positive)
    draw.text((width - pad - draw.textlength(subtitle, font=criteria_font), 70), subtitle, font=criteria_font, fill=theme.muted_text)

    title_top = 150 if square else 104
    title_font = _font(56 if square else 46, bold=True)
    draw.text((pad, title_top), "Best-performing stablecoin vaults", font=title_font, fill=theme.text)

    # The ranking sits on a rounded card with column headers and hairline row separators
    card_top = title_top + (110 if square else 78)
    card_bottom = height - (56 if square else 30)
    card = (pad - 20, card_top, width - pad + 20, card_bottom)
    card_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(card_layer).rounded_rectangle(card, radius=22, fill=_hex_to_rgba(theme.surface, 235), outline=(255, 255, 255, 22) if theme.name == "dark" else (0, 0, 0, 20), width=1)
    image.alpha_composite(card_layer)
    draw = ImageDraw.Draw(image)

    header_height = 44 if square else 38
    rows = vaults_df.head(5)
    row_height = (card_bottom - card_top - header_height - 8) / max(len(rows), 1)
    spark_left = int(width * (0.54 if square else 0.575))
    spark_right = spark_left + (190 if square else 220)
    header_font = _font(14)
    header_y = card_top + header_height / 2
    for text, x, anchor in (("VAULT", pad + 52, "lm"), ("90-DAY PRICE", spark_left, "lm"), ("3M RETURN, ANNUALISED", width - pad, "rm")):
        draw.text((x, header_y), text, font=header_font, fill=theme.muted_text, anchor=anchor)
    # Neutral hairlines: white on the dark theme, black on the light one
    separator = (255, 255, 255, 28) if theme.name == "dark" else (0, 0, 0, 24)
    # Separators go on a layer: ImageDraw does not blend translucent colours into an RGBA image
    lines_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    lines_draw = ImageDraw.Draw(lines_layer)
    lines_draw.line((pad, card_top + header_height, width - pad, card_top + header_height), fill=separator, width=1)

    # Top three ranks get gold, silver and bronze rings
    medals = {1: "#d4af37", 2: "#c0c4cc", 3: "#c8804a"}
    name_font, property_font = _font(28 if square else 25, bold=True), _font(18 if square else 17)
    value_font = _font(40 if square else 34, bold=True)
    for rank, (vault_id, vault) in enumerate(rows.iterrows(), start=1):
        top = card_top + header_height + (rank - 1) * row_height
        middle = top + row_height / 2
        if rank > 1:
            lines_draw.line((pad, top, width - pad, top), fill=separator, width=1)

        # Rank badge: a translucent tinted disc with a ring, drawn on its own layer so the tint blends
        radius = 17 if square else 15
        ring = medals.get(rank, theme.muted_text)
        badge = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(badge).ellipse((pad, middle - radius, pad + 2 * radius, middle + radius), outline=_hex_to_rgba(ring, 255 if rank in medals else 110), width=2, fill=_hex_to_rgba(ring, 34 if rank in medals else 0))
        image.alpha_composite(badge)
        draw = ImageDraw.Draw(image)
        draw.text((pad + radius, middle), str(rank), font=_font(17 if square else 16, bold=True), fill=ring, anchor="mm")

        name_x = pad + 2 * radius + 20
        text_right = spark_left - 28
        name_y = middle - (21 if square else 18)
        draw.text((name_x, name_y), _fit_text(draw, vault["name"] or vault["address"], name_font, text_right - name_x), font=name_font, fill=theme.text, anchor="lm")
        # Curator, protocol and chain under the name, each with its own icon
        property_y = middle + (17 if square else 15)
        x = name_x
        for text, icon in properties.get(vault_id, [(vault["protocol_label"], None), (vault["chain"], None)]):
            if icon is not None:
                if x + 22 > text_right:
                    break
                icon = icon.copy()
                icon.thumbnail((38, 19))
                image.alpha_composite(icon, (x, int(property_y - icon.height / 2)))
                x += icon.width + 6
            if text_right - x < 40:
                break
            fitted = _fit_text(draw, text, property_font, text_right - x)
            draw.text((x, property_y), fitted, font=property_font, fill=theme.muted_text, anchor="lm")
            x += int(draw.textlength(fitted, font=property_font)) + 16
        if vault_id in sparklines:
            spark_half = min(24 if square else 20, row_height * 0.32)
            _draw_sparkline(image, sparklines[vault_id], (spark_left, int(middle - spark_half), spark_right, int(middle + spark_half)), theme.positive)
            draw = ImageDraw.Draw(image)
        value = format_return(vault["three_months_cagr_net"], vault["three_months_cagr"])
        draw.text((width - pad, middle), value, font=value_font, fill=theme.positive, anchor="rm")
    image.alpha_composite(lines_layer)

    image.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path
