"""Post-process logo images for vault protocol metadata.

Converts, crops, and scales logo images to standardised 256x256 PNG format.
Automatically adds padding to non-square images to make them square before
processing with background removal using rembg.

Usage:

.. code-block:: shell

    export INPUT_IMAGE=/path/to/input.svg
    export OUTPUT_IMAGE=/path/to/output.png
    python scripts/logos/post-process-logo.py

Environment variables:
    INPUT_IMAGE: Path to input image file (required)
    OUTPUT_IMAGE: Path to output image file (required)
    TARGET_SIZE: Target size in pixels (default: 256)

.. note::

    Gemini processing has been disabled. Gemini produces hallucinated crap
    when fed in logos and is useless at the moment. The script now simply
    picks the input image, adds padding if non-square, removes background,
    and scales to target size.

"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Environment variable configuration
INPUT_IMAGE = os.environ.get("INPUT_IMAGE")
OUTPUT_IMAGE = os.environ.get("OUTPUT_IMAGE")
TARGET_SIZE = int(os.environ.get("TARGET_SIZE", "256"))


def convert_svg_to_png(input_path: Path, output_path: Path, size: int = 512) -> None:
    """Convert SVG to PNG using cairosvg.

    Note: Gemini does not accept SVG input, only raster images.

    This function preprocesses the SVG to remove style attributes that use
    unsupported colour spaces (like display-p3), which cairosvg cannot render.
    The fill attribute values are preserved and used instead.

    :param input_path: Path to input SVG file
    :param output_path: Path to output PNG file
    :param size: Output size in pixels (width and height)
    """
    import re

    import cairosvg

    logger.info("Converting SVG to PNG: %s -> %s (size: %d)", input_path, output_path, size)

    # Read and preprocess SVG to remove problematic style attributes
    svg_content = input_path.read_text()

    # Remove style attributes containing color() function (e.g., display-p3 colour space)
    # which cairosvg doesn't support. The fill attribute will be used instead.
    original_content = svg_content
    svg_content = re.sub(r'\s*style="[^"]*color\([^)]+\)[^"]*"', "", svg_content)

    if svg_content != original_content:
        logger.info("Removed unsupported colour space style attributes from SVG")

    cairosvg.svg2png(
        bytestring=svg_content.encode(),
        write_to=str(output_path),
        output_width=size,
        output_height=size,
    )

    logger.info("SVG conversion complete")


def pad_to_square(input_path: Path, output_path: Path) -> None:
    """Add transparent padding to make a non-square image square.

    Centers the original image content and adds transparent padding
    on the shorter dimension to create a square output.

    :param input_path: Path to input image file
    :param output_path: Path to output image file
    """
    from PIL import Image

    logger.info("Checking if padding needed: %s", input_path)

    image = Image.open(input_path).convert("RGBA")
    width, height = image.size

    # Check if already square (within 1% tolerance)
    aspect_ratio = min(width, height) / max(width, height)
    if aspect_ratio >= 0.99:
        logger.info("Image is already square (%dx%d), no padding needed", width, height)
        image.save(output_path, "PNG", optimize=True, compress_level=9)
        return

    # Calculate new square size (use the larger dimension)
    new_size = max(width, height)

    # Create new transparent square image
    square_image = Image.new("RGBA", (new_size, new_size), (0, 0, 0, 0))

    # Calculate position to center the original image
    x_offset = (new_size - width) // 2
    y_offset = (new_size - height) // 2

    # Paste original image onto square canvas
    square_image.paste(image, (x_offset, y_offset))

    square_image.save(output_path, "PNG", optimize=True, compress_level=9)
    logger.info(
        "Added padding to make square: %dx%d -> %dx%d",
        width,
        height,
        new_size,
        new_size,
    )


# NOTE: Gemini processing has been disabled.
# Gemini produces hallucinated crap when fed in logos and is useless at the moment.
# The code below is kept for reference but is not used.
#
# def process_logo_with_gemini(
#     input_path: Path,
#     output_path: Path,
#     padding_percent: int = 10,
#     invert: str = "",
# ) -> None:
#     """Process logo using a single Gemini AI prompt.
#
#     Performs all AI operations in one pass:
#     - Analyses if logo is brandmark, wordmark, or combination
#     - Extracts brand mark if needed (removes text)
#     - Optionally inverts colours for opposite theme variant
#     - Crops to square with padding
#
#     Note: Gemini cannot produce true transparent PNGs, so background
#     removal must be done separately after this step.
#
#     :param input_path: Path to input image file
#     :param output_path: Path to output image file
#     :param padding_percent: Padding percentage for square crop
#     :param invert: 'light_to_dark', 'dark_to_light', or empty for no inversion
#     """
#     import base64
#     import io
#
#     import google.generativeai as genai
#     from PIL import Image
#
#     GOOGLE_AI_API_KEY = os.environ.get("GOOGLE_AI_API_KEY")
#     if not GOOGLE_AI_API_KEY:
#         raise ValueError("GOOGLE_AI_API_KEY environment variable is required")
#
#     genai.configure(api_key=GOOGLE_AI_API_KEY)
#     model = genai.GenerativeModel("gemini-2.5-flash-image")
#
#     logger.info("Processing logo with Gemini: %s (invert=%s)", input_path, invert or "none")
#
#     # Load and prepare image
#     image = Image.open(input_path)
#
#     # Build the prompt - all operations in one pass
#     prompt_parts = [
#         "Process this logo image to create a clean, square icon suitable for use as an app icon or avatar.",
#         "",
#         "Please perform ALL of the following steps in a single output:",
#         "",
#         "1. ANALYSE: Determine if this is a brandmark (icon only), wordmark (text only), or combination (icon + text).",
#         "",
#         "2. EXTRACT ICON: If the logo contains text (wordmark or combination), extract ONLY the icon/symbol portion and remove all text elements. If it's already just an icon, keep it as is.",
#         "",
#     ]
#
#     # Add colour inversion step if requested
#     if invert == "light_to_dark":
#         prompt_parts.extend(
#             [
#                 "3. INVERT COLOURS: This logo is designed for light backgrounds (dark-coloured logo). Invert the colours to create a version suitable for dark backgrounds. Make dark elements light/white and light elements dark. Preserve any brand colours where appropriate.",
#                 "",
#             ]
#         )
#         next_step = 4
#     elif invert == "dark_to_light":
#         prompt_parts.extend(
#             [
#                 "3. INVERT COLOURS: This logo is designed for dark backgrounds (light-coloured logo). Invert the colours to create a version suitable for light backgrounds. Make light/white elements dark and dark elements light. Preserve any brand colours where appropriate.",
#                 "",
#             ]
#         )
#         next_step = 4
#     else:
#         next_step = 3
#
#     prompt_parts.extend(
#         [
#             f"{next_step}. USE SOLID BACKGROUND: Place the logo on a solid white background (we will remove it later).",
#             "",
#             f"{next_step + 1}. CROP TO SQUARE: Crop the result to a square aspect ratio, centering the icon/logo content with approximately {padding_percent}% padding on all sides.",
#             "",
#             "Output a single square image with:",
#             "- Only the icon/symbol (no text)",
#             "- Solid white background",
#             f"- Content centered with {padding_percent}% padding",
#             "- Clean edges with no artifacts",
#         ]
#     )
#
#     prompt = "\n".join(prompt_parts)
#
#     response = model.generate_content([prompt, image])
#
#     # Extract image from response
#     if hasattr(response, "candidates") and response.candidates:
#         for part in response.candidates[0].content.parts:
#             if hasattr(part, "inline_data") and part.inline_data:
#                 # Gemini returns raw bytes, not base64 encoded
#                 image_data = part.inline_data.data
#                 # Handle both bytes and string (base64) cases
#                 if isinstance(image_data, str):
#                     image_data = base64.b64decode(image_data)
#                 result_image = Image.open(io.BytesIO(image_data))
#                 result_image.save(output_path, "PNG", optimize=True, compress_level=9)
#                 logger.info("Logo processed and saved to: %s", output_path)
#                 return
#
#     raise ValueError("No image returned from Gemini API")


def has_transparency(image_path: Path) -> bool:
    """Check if an image already has transparent pixels.

    :param image_path: Path to image file
    :return: True if the image has any transparent or semi-transparent pixels
    """
    from PIL import Image

    image = Image.open(image_path)

    # Check if image has an alpha channel
    if image.mode not in ("RGBA", "LA", "PA"):
        return False

    # Get alpha channel
    if image.mode == "RGBA":
        alpha = image.getchannel("A")
    elif image.mode == "LA":
        alpha = image.getchannel("A")
    else:  # PA mode
        alpha = image.getchannel("A")

    # Check if any pixel has alpha < 255 (not fully opaque)
    alpha_data = alpha.getdata()
    min_alpha = min(alpha_data)

    return min_alpha < 255


def remove_background(input_path: Path, output_path: Path) -> None:
    """Remove background from image using rembg.

    Gemini cannot produce true transparent PNGs, so we use rembg
    to remove the background after Gemini processing.

    If the input image already has transparency, the background removal
    step is skipped and the image is copied directly to the output.

    :param input_path: Path to input image file
    :param output_path: Path to output image file
    """
    from PIL import Image
    from rembg import remove

    # Check if image already has transparency
    if has_transparency(input_path):
        logger.info("Image already has transparency, skipping background removal: %s", input_path)
        image = Image.open(input_path)
        image.save(output_path, "PNG", optimize=True, compress_level=9)
        logger.info("Copied transparent image to: %s", output_path)
        return

    logger.info("Removing background: %s", input_path)

    image = Image.open(input_path)
    result = remove(image)
    result.save(output_path, "PNG", optimize=True, compress_level=9)

    logger.info("Background removed and saved to: %s", output_path)


def recolour_for_dark_background(input_path: Path, output_path: Path) -> None:
    """Detect if logo is dark-on-dark and invert colours for dark background visibility.

    Analyses the visible (non-transparent) pixels of the image. If there are no
    bright pixels at all (none exceeding a brightness threshold), the logo would
    not be visible on a dark background. In this case, the colours are inverted.

    :param input_path: Path to input image file
    :param output_path: Path to output image file
    """
    from PIL import Image, ImageOps

    logger.info("Checking if recolouring needed for dark background: %s", input_path)

    image = Image.open(input_path).convert("RGBA")

    # Extract only visible pixels (alpha > 0)
    pixels = list(image.getdata())
    visible_pixels = [(r, g, b) for r, g, b, a in pixels if a > 0]

    if not visible_pixels:
        logger.warning("No visible pixels found, skipping recolouring")
        image.save(output_path, "PNG", optimize=True, compress_level=9)
        return

    # Check if there are ANY bright pixels in the image
    # Using perceived luminance formula: 0.299*R + 0.587*G + 0.114*B
    # A pixel is considered "bright" if its luminance exceeds the threshold
    brightness_threshold = 128

    has_bright_pixels = any((0.299 * r + 0.587 * g + 0.114 * b) >= brightness_threshold for r, g, b in visible_pixels)

    if not has_bright_pixels:
        # Calculate max brightness for logging
        max_brightness = max(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in visible_pixels)
        logger.info(
            "Logo has no bright pixels (max brightness: %.1f < %d), inverting colours for dark background",
            max_brightness,
            brightness_threshold,
        )

        # Split into RGB and Alpha channels
        r, g, b, a = image.split()

        # Invert only the RGB channels, preserve alpha
        rgb_image = Image.merge("RGB", (r, g, b))
        inverted_rgb = ImageOps.invert(rgb_image)

        # Recombine with original alpha channel
        r_inv, g_inv, b_inv = inverted_rgb.split()
        result = Image.merge("RGBA", (r_inv, g_inv, b_inv, a))

        result.save(output_path, "PNG", optimize=True, compress_level=9)
        logger.info("Colours inverted and saved to: %s", output_path)
    else:
        # Calculate max brightness for logging
        max_brightness = max(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in visible_pixels)
        logger.info(
            "Logo has bright pixels (max brightness: %.1f >= %d), no recolouring needed",
            max_brightness,
            brightness_threshold,
        )
        image.save(output_path, "PNG", optimize=True, compress_level=9)


def trim_and_scale_image(input_path: Path, output_path: Path, size: int = 256) -> None:
    """Remove padding/margin and scale image to target size.

    Removes both transparent padding and solid colour padding/margins by:
    1. First trimming transparent pixels using alpha channel
    2. Then detecting and removing any solid colour border/padding

    Finally scales to the target size using high-quality resampling.

    :param input_path: Path to input image file
    :param output_path: Path to output image file
    :param size: Target size in pixels (width and height)
    """
    from PIL import Image, ImageChops

    logger.info("Trimming and scaling image to %dx%d: %s", size, size, input_path)

    image = Image.open(input_path).convert("RGBA")
    original_size = (image.width, image.height)

    # Step 1: Trim transparent pixels using alpha channel
    alpha = image.getchannel("A")
    alpha_bbox = alpha.getbbox()

    if alpha_bbox:
        image = image.crop(alpha_bbox)
        logger.info(
            "Trimmed transparent padding from %dx%d to %dx%d",
            original_size[0],
            original_size[1],
            image.width,
            image.height,
        )
    else:
        # No non-transparent pixels found
        logger.warning("No non-transparent pixels found, keeping original size")

    # Step 2: Detect and remove solid colour padding/margin
    # Sample corner pixels to detect background colour
    corners = [
        image.getpixel((0, 0)),
        image.getpixel((image.width - 1, 0)),
        image.getpixel((0, image.height - 1)),
        image.getpixel((image.width - 1, image.height - 1)),
    ]

    # Check if all corners have the same colour (indicating solid padding)
    if len(set(corners)) == 1:
        bg_colour = corners[0]
        # Only process if the background is not fully transparent
        if bg_colour[3] > 0:  # Alpha > 0
            logger.info("Detected solid colour padding: RGBA%s", bg_colour)

            # Create a solid background image of the same colour
            bg = Image.new("RGBA", image.size, bg_colour)

            # Find difference between image and solid background
            diff = ImageChops.difference(image, bg)

            # Get bounding box of non-background pixels
            # We need to check RGB channels, not just alpha
            diff_rgb = diff.convert("RGB")
            content_bbox = diff_rgb.getbbox()

            if content_bbox:
                pre_trim_size = (image.width, image.height)
                image = image.crop(content_bbox)
                logger.info(
                    "Trimmed solid colour padding from %dx%d to %dx%d",
                    pre_trim_size[0],
                    pre_trim_size[1],
                    image.width,
                    image.height,
                )

    # Scale to target size
    resized = image.resize((size, size), Image.Resampling.LANCZOS)
    resized.save(output_path, "PNG", optimize=True, compress_level=9)

    logger.info("Trimmed and scaled to: %s", output_path)


def process_logo(
    input_path: Path,
    output_path: Path,
    target_size: int = 256,
) -> None:
    """Process a logo image through the full pipeline.

    Pipeline:
    1. SVG to PNG conversion (if needed)
    2. Add padding to make square (if non-square)
    3. Background removal (rembg)
    4. Recolour for dark background (invert if logo is too dark)
    5. Trim transparent padding and scale to target size (Pillow)

    :param input_path: Path to input image file
    :param output_path: Path to output image file
    :param target_size: Target output size in pixels
    """
    import tempfile

    logger.info(
        "Starting logo processing: %s -> %s",
        input_path,
        output_path,
    )

    # Create temp directory for intermediate files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        current_file = input_path

        # Step 1: Convert SVG to PNG if needed
        if input_path.suffix.lower() == ".svg":
            svg_output = temp_path / "step1_svg_converted.png"
            convert_svg_to_png(current_file, svg_output, size=512)
            current_file = svg_output

        # Step 2: Add padding to make square if needed
        padded_output = temp_path / "step2_padded.png"
        pad_to_square(current_file, padded_output)
        current_file = padded_output

        # Step 3: Remove background
        bg_removed_output = temp_path / "step3_bg_removed.png"
        remove_background(current_file, bg_removed_output)
        current_file = bg_removed_output

        # Step 4: Recolour for dark background (invert if logo is too dark)
        recoloured_output = temp_path / "step4_recoloured.png"
        recolour_for_dark_background(current_file, recoloured_output)
        current_file = recoloured_output

        # Step 5: Trim transparent padding and scale to target size
        trim_and_scale_image(current_file, output_path, target_size)

    logger.info("Logo processing complete: %s", output_path)


def main():
    """Main entry point."""
    from eth_defi.utils import setup_console_logging

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    if not INPUT_IMAGE:
        raise ValueError("INPUT_IMAGE environment variable is required")
    if not OUTPUT_IMAGE:
        raise ValueError("OUTPUT_IMAGE environment variable is required")

    input_path = Path(INPUT_IMAGE)
    output_path = Path(OUTPUT_IMAGE)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    process_logo(
        input_path=input_path,
        output_path=output_path,
        target_size=TARGET_SIZE,
    )


if __name__ == "__main__":
    main()
