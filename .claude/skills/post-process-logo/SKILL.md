---
name: post-process-logo
description: Post-process original logos into standardised 256x256 PNG format
---

# Post-process logo

This skill transforms original logo images into standardised 256x256 PNG format suitable for vault protocol metadata. It automatically selects the most square variant from available logos and applies padding if needed to create a perfect square output.

## Required inputs

Before starting, gather the following from the user:

1. **Input folder** - Folder containing original logo files (e.g., `eth_defi/data/vaults/original_logos/protocol-name/`)
2. **Output folder** - Folder where processed logos should be saved (e.g., `eth_defi/data/vaults/formatted_logos/`)
3. **Variant preference** (optional) - Which variant(s) to process: `generic`, `light`, `dark`, or `all`

If any required input is missing, ask the user before proceeding.

## Prerequisites

Ensure the following are available:

1. Python dependencies installed: `poetry install --with dev`

Python Pillow is installed for detecting image format, dimensions, and transparency.

## Logo vocabulary

There is no universal standard how artist name their logo files for dark and light variants. In our case, we always say
- `light`: light (white) text on dark or transparent background
- `dark`: dark (black) text on white or transparent  background

Following vocabulary is used:
- Brand mark: same as logo mark, the logo without the brand name text
- Word mark: the logo with the brand name text

## Step 1: Inventory input logos

List all image files in the input folder and classify them:

1. Identify file formats: SVG, PNG, JPG, WEBP, etc.
2. Classify by variant based on filename:
   - `{slug}.generic.{ext}` - Generic/default theme
   - `{slug}.light.{ext}` - Light background theme (dark logo)
   - `{slug}.dark.{ext}` - Dark background theme (light logo)
3. Always prefer brand marks over word marks as source logo for post processing
4. **Check aspect ratio** of each logo:
   - **Square aspect ratio**: Width equals height (1:1 ratio). Allow 90% tolerance in the detection as there might be one-off pixel errors in the source material.
   - **Non-square**: Width differs from height - will need padding to become square
5. **Check if logos have transparent backgrounds**:
   - **Transparent background**: PNG files with alpha channel transparency
   - **Assume SVGs are always transparent**: SVG files the originals should never contain a solid background
6. Report findings to user, noting aspect ratios for each variant

## Step 2: Variant selection

### Automatic selection priority

1. **Pick the most square variant**: Calculate aspect ratio (min dimension / max dimension) for each variant and select the one closest to 1.0
2. **If multiple variants have the same squareness**:
   - Prefer transparent variants (PNG with alpha, SVG)
   - If still tied, prefer generic > light > dark
3. **If user specified a variant preference**: Use that variant regardless of squareness

## Step 3: Process logo

Process the selected logo variant. The script will automatically add padding to non-square logos to make them square before scaling.

```shell
export INPUT_IMAGE=/path/to/original/logo.png
export OUTPUT_IMAGE=/path/to/output/logo.generic.png
poetry run python scripts/logos/post-process-logo.py
```

The script will:
1. Convert SVG to PNG if needed
2. Add transparent padding to make the logo square (if non-square)
3. Remove background if not already transparent
4. Recolour for dark background (invert colours if logo is too dark to be visible on dark backgrounds)
5. Trim any excess padding and scale to 256x256

## Step 4: Report results

Provide the user with:

1. **Processed files** - List of all output files created
2. **File details** - Dimensions, file size for each
3. **Any issues** - Note any logos that couldn't be processed or had quality issues

## Output naming convention

Output files should follow this naming pattern:
- `{protocol-slug}/light.png` - For light backgrounds
- `{protocol-slug}/dark.png` - For dark backgrounds

## Troubleshooting

### SVG conversion issues

If SVG conversion fails:

1. Check if the SVG file is valid XML
2. Some complex SVGs may not render correctly
3. Try opening in a browser to verify the SVG displays properly

### Background removal issues

If the background isn't removed properly:

1. The logo may have complex edges or gradients
2. Try providing a higher resolution input
3. For logos that already have transparency, the script will skip background removal
