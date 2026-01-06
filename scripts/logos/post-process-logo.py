"""Post-process logo images for vault protocol metadata.

Converts, crops, and scales logo images to standardised 256x256 PNG format.
Uses Google Gemini 2.5 Flash Image (Nano Banana) for AI-powered logo processing
in a single pass, followed by background removal using rembg.

Usage:

.. code-block:: shell

    export GOOGLE_AI_API_KEY=...
    export INPUT_IMAGE=/path/to/input.svg
    export OUTPUT_IMAGE=/path/to/output.png
    python scripts/logos/post-process-logo.py

    # To also invert colours (create opposite theme variant)
    export INVERT=light_to_dark
    python scripts/logos/post-process-logo.py

Environment variables:
    GOOGLE_AI_API_KEY: Google AI Studio API key for Gemini (required)
    INPUT_IMAGE: Path to input image file (required)
    OUTPUT_IMAGE: Path to output image file (required)
    TARGET_SIZE: Target size in pixels (default: 256)
    PADDING_PERCENT: Padding percentage for square crop (default: 10)
    INVERT: Colour inversion mode - 'light_to_dark', 'dark_to_light', or empty for no inversion

"""

import base64
import io
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Environment variable configuration
GOOGLE_AI_API_KEY = os.environ.get("GOOGLE_AI_API_KEY")
INPUT_IMAGE = os.environ.get("INPUT_IMAGE")
OUTPUT_IMAGE = os.environ.get("OUTPUT_IMAGE")
TARGET_SIZE = int(os.environ.get("TARGET_SIZE", "256"))
PADDING_PERCENT = int(os.environ.get("PADDING_PERCENT", "10"))
INVERT = os.environ.get("INVERT", "")  # 'light_to_dark', 'dark_to_light', or empty


def convert_svg_to_png(input_path: Path, output_path: Path, size: int = 512) -> None:
    """Convert SVG to PNG using cairosvg.

    Note: Gemini does not accept SVG input, only raster images.

    :param input_path: Path to input SVG file
    :param output_path: Path to output PNG file
    :param size: Output size in pixels (width and height)
    """
    import cairosvg

    logger.info("Converting SVG to PNG: %s -> %s (size: %d)", input_path, output_path, size)

    cairosvg.svg2png(
        url=str(input_path),
        write_to=str(output_path),
        output_width=size,
        output_height=size,
    )

    logger.info("SVG conversion complete")


def process_logo_with_gemini(
    input_path: Path,
    output_path: Path,
    padding_percent: int = 10,
    invert: str = "",
) -> None:
    """Process logo using a single Gemini AI prompt.

    Performs all AI operations in one pass:
    - Analyses if logo is brandmark, wordmark, or combination
    - Extracts brand mark if needed (removes text)
    - Optionally inverts colours for opposite theme variant
    - Crops to square with padding

    Note: Gemini cannot produce true transparent PNGs, so background
    removal must be done separately after this step.

    :param input_path: Path to input image file
    :param output_path: Path to output image file
    :param padding_percent: Padding percentage for square crop
    :param invert: 'light_to_dark', 'dark_to_light', or empty for no inversion
    """
    import google.generativeai as genai
    from PIL import Image

    if not GOOGLE_AI_API_KEY:
        raise ValueError("GOOGLE_AI_API_KEY environment variable is required")

    genai.configure(api_key=GOOGLE_AI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash-image")

    logger.info("Processing logo with Gemini: %s (invert=%s)", input_path, invert or "none")

    # Load and prepare image
    image = Image.open(input_path)

    # Build the prompt - all operations in one pass
    prompt_parts = [
        "Process this logo image to create a clean, square icon suitable for use as an app icon or avatar.",
        "",
        "Please perform ALL of the following steps in a single output:",
        "",
        "1. ANALYSE: Determine if this is a brandmark (icon only), wordmark (text only), or combination (icon + text).",
        "",
        "2. EXTRACT ICON: If the logo contains text (wordmark or combination), extract ONLY the icon/symbol portion and remove all text elements. If it's already just an icon, keep it as is.",
        "",
    ]

    # Add colour inversion step if requested
    if invert == "light_to_dark":
        prompt_parts.extend(
            [
                "3. INVERT COLOURS: This logo is designed for light backgrounds (dark-coloured logo). Invert the colours to create a version suitable for dark backgrounds. Make dark elements light/white and light elements dark. Preserve any brand colours where appropriate.",
                "",
            ]
        )
        next_step = 4
    elif invert == "dark_to_light":
        prompt_parts.extend(
            [
                "3. INVERT COLOURS: This logo is designed for dark backgrounds (light-coloured logo). Invert the colours to create a version suitable for light backgrounds. Make light/white elements dark and dark elements light. Preserve any brand colours where appropriate.",
                "",
            ]
        )
        next_step = 4
    else:
        next_step = 3

    prompt_parts.extend(
        [
            f"{next_step}. USE SOLID BACKGROUND: Place the logo on a solid white background (we will remove it later).",
            "",
            f"{next_step + 1}. CROP TO SQUARE: Crop the result to a square aspect ratio, centering the icon/logo content with approximately {padding_percent}% padding on all sides.",
            "",
            "Output a single square image with:",
            "- Only the icon/symbol (no text)",
            "- Solid white background",
            f"- Content centered with {padding_percent}% padding",
            "- Clean edges with no artifacts",
        ]
    )

    prompt = "\n".join(prompt_parts)

    response = model.generate_content([prompt, image])

    # Extract image from response
    if hasattr(response, "candidates") and response.candidates:
        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                # Gemini returns raw bytes, not base64 encoded
                image_data = part.inline_data.data
                # Handle both bytes and string (base64) cases
                if isinstance(image_data, str):
                    image_data = base64.b64decode(image_data)
                result_image = Image.open(io.BytesIO(image_data))
                result_image.save(output_path, "PNG", optimize=True, compress_level=9)
                logger.info("Logo processed and saved to: %s", output_path)
                return

    raise ValueError("No image returned from Gemini API")


def remove_background(input_path: Path, output_path: Path) -> None:
    """Remove background from image using rembg.

    Gemini cannot produce true transparent PNGs, so we use rembg
    to remove the background after Gemini processing.

    :param input_path: Path to input image file
    :param output_path: Path to output image file
    """
    from PIL import Image
    from rembg import remove

    logger.info("Removing background: %s", input_path)

    image = Image.open(input_path)
    result = remove(image)
    result.save(output_path, "PNG", optimize=True, compress_level=9)

    logger.info("Background removed and saved to: %s", output_path)


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
    padding_percent: int = 10,
    invert: str = "",
) -> None:
    """Process a logo image through the full pipeline.

    Pipeline:
    1. SVG to PNG conversion (if needed) - Gemini only accepts raster images
    2. Gemini processing (single prompt): analyse, extract icon, optionally invert, crop to square
    3. Background removal (rembg) - Gemini cannot produce true transparency
    4. Trim transparent padding and scale to target size (Pillow)

    :param input_path: Path to input image file
    :param output_path: Path to output image file
    :param target_size: Target output size in pixels
    :param padding_percent: Padding percentage for square crop
    :param invert: 'light_to_dark', 'dark_to_light', or empty for no inversion
    """
    import tempfile

    logger.info("Starting logo processing: %s -> %s", input_path, output_path)

    # Create temp directory for intermediate files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        current_file = input_path

        # Step 1: Convert SVG to PNG if needed (Gemini only accepts raster images)
        if input_path.suffix.lower() == ".svg":
            svg_output = temp_path / "step1_svg_converted.png"
            convert_svg_to_png(current_file, svg_output, size=512)
            current_file = svg_output

        # Step 2: Process with Gemini (single prompt for all AI operations including crop)
        gemini_output = temp_path / "step2_gemini_processed.png"
        process_logo_with_gemini(current_file, gemini_output, padding_percent, invert)
        current_file = gemini_output

        # Step 3: Remove background (Gemini cannot produce true transparency)
        bg_removed_output = temp_path / "step3_bg_removed.png"
        remove_background(current_file, bg_removed_output)
        current_file = bg_removed_output

        # Step 4: Trim transparent padding and scale to target size
        trim_and_scale_image(current_file, output_path, target_size)

    logger.info("Logo processing complete: %s", output_path)


def main():
    """Main entry point."""
    from eth_defi.utils import setup_console_logging

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    # Check for required API key first
    if not GOOGLE_AI_API_KEY:
        print("\n" + "=" * 80)
        print("ERROR: GOOGLE_AI_API_KEY environment variable is not set")
        print("=" * 80)
        print("\nThis script requires a Google AI Studio API key to use Gemini 2.5 Flash Image.")
        print("\nTo get an API key:")
        print("  1. Visit https://aistudio.google.com/")
        print("  2. Sign in with your Google account")
        print("  3. Click 'Get API key' in the left sidebar")
        print("  4. Create a new API key or use an existing one")
        print("\nTo set the API key:")
        print("  export GOOGLE_AI_API_KEY='your-api-key-here'")
        print("\nThen run this script again.")
        print("=" * 80 + "\n")
        raise ValueError("GOOGLE_AI_API_KEY environment variable is required")

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
        padding_percent=PADDING_PERCENT,
        invert=INVERT,
    )


if __name__ == "__main__":
    main()
