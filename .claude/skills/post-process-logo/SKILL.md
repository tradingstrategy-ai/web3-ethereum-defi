---
name: post-process-logo
description: Post-process original logos into standardised 256x256 PNG format
---

# Post-process logo

This skill transforms original logo images into standardised 256x256 PNG format suitable for vault protocol metadata. It uses Google Gemini 2.5 Flash Image (Nano Banana) for AI-powered logo processing in a single pass, followed by rembg for background removal (since Gemini cannot produce true transparent PNGs).

## Required inputs

Before starting, gather the following from the user:

1. **Input folder** - Folder containing original logo files (e.g., `eth_defi/data/vaults/original_logos/protocol-name/`)
2. **Output folder** - Folder where processed logos should be saved (e.g., `eth_defi/data/vaults/formatted_logos/`)
3. **Variant preference** (optional) - Which variant(s) to process: `generic`, `light`, `dark`, or `all`

If any required input is missing, ask the user before proceeding.

## Prerequisites

Ensure the following are available:

1. **GOOGLE_AI_API_KEY** environment variable set with a valid Google AI Studio API key
2. Python dependencies installed: `poetry install --with dev`

## Step 1: Inventory input logos

List all image files in the input folder and classify them:

1. Identify file formats: SVG, PNG, JPG, WEBP, etc.
2. Classify by variant based on filename:
   - `{slug}.generic.{ext}` - Generic/default theme
   - `{slug}.light.{ext}` - Light background theme (dark logo)
   - `{slug}.dark.{ext}` - Dark background theme (light logo)
3. Report findings to user

## Step 2: Variant selection

If multiple variants exist and user hasn't specified a preference:

1. Ask user which variant(s) to process
2. Options: process a specific variant or all available variants

## Step 3: Process each logo

For each selected logo, run the processing script. The pipeline:

1. **SVG to PNG** (if needed) - Gemini only accepts raster images
2. **Gemini processing** (single prompt): analyses logo type, extracts icon, optionally inverts colours, crops to square
3. **Background removal** (rembg) - Gemini cannot produce true transparency
4. **Scale to 256x256** (Pillow)

### Standard processing

```shell
export GOOGLE_AI_API_KEY=...
export INPUT_IMAGE=/path/to/original/logo.svg
export OUTPUT_IMAGE=/path/to/output/logo.generic.png
export TARGET_SIZE=256
export PADDING_PERCENT=10
python scripts/logos/post-process-logo.py
```

### Invert colours (light to dark or vice versa)

If you only have one variant (e.g., only a light logo) and need the opposite variant:

```shell
export GOOGLE_AI_API_KEY=...
export INPUT_IMAGE=/path/to/logo.light.png
export OUTPUT_IMAGE=/path/to/logo.dark.png
export INVERT=light_to_dark
python scripts/logos/post-process-logo.py
```

Set `INVERT` to:
- `light_to_dark` - Convert a dark logo (for light backgrounds) to a light logo (for dark backgrounds)
- `dark_to_light` - Convert a light logo (for dark backgrounds) to a dark logo (for light backgrounds)
- Empty or unset - No colour inversion

## Step 4: Verify output

After processing, verify each output file:

1. **Dimensions** - Should be exactly 256x256 pixels
2. **Format** - Should be PNG with RGBA colour mode
3. **Transparency** - Background should be transparent
4. **Content** - Logo should be centred with appropriate padding
5. **Quality** - No artifacts, distortion, or pixelation

Use this command to check image properties:

```shell
python -c "from PIL import Image; img = Image.open('output.png'); print(f'Size: {img.size}, Mode: {img.mode}')"
```

## Step 5: Report results

Provide the user with:

1. **Processed files** - List of all output files created
2. **File details** - Dimensions, file size for each
3. **Any issues** - Note any logos that couldn't be processed or had quality issues

## Output naming convention

Output files should follow this naming pattern:
- `{protocol-slug}.generic.png` - Generic/default theme
- `{protocol-slug}.light.png` - For light backgrounds
- `{protocol-slug}.dark.png` - For dark backgrounds

## Troubleshooting

### API key not set

If you see "GOOGLE_AI_API_KEY environment variable is required":

1. Ensure the API key is exported in your environment
2. Get a key from [Google AI Studio](https://aistudio.google.com/)

### No image returned from Gemini

If Gemini doesn't return an image:

1. The image may be too complex or unclear
2. Try with a higher resolution input
3. Check if the API key has sufficient quota

### SVG conversion issues

If SVG conversion fails:

1. Check if the SVG file is valid XML
2. Some complex SVGs may not render correctly
3. Try opening in a browser to verify the SVG displays properly

### Colour inversion produces unexpected results

If the inverted colours don't look right:

1. Gemini may struggle with complex multi-coloured logos
2. For simple inversions, consider manual editing
3. Try providing a clearer input image
