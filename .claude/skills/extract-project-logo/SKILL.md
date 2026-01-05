---
name: extract-project-logo
description: Extract a project's logo from its website, brand kit, or other sources
---

# Extract project logo

This skill extracts a project's official logo from its website or related sources, prioritising high-quality vector formats and official brand assets.

## Required inputs

Before starting, gather the following from the user:

1. **Website URL** - The project's main website (e.g., `https://example.com`)
2. **Save path** - Local filesystem path where the logo should be saved (e.g., `./logos/example-logo.svg`)
3. **Format preference** (optional) - Preferred format: SVG (recommended), PNG, or any

If any required input is missing, ask the user before proceeding.

Always prefer an image file with brand mark (logo without text) over wordmark (logo with text),
because we want to use logos in squared icon context.

## Step 1: Search for brand kit or media resources

The highest quality logos are typically found in official brand/media kits. Search for these pages:

1. Navigate to the website and look for links in the footer or header:
   - "Brand", "Brand Kit", "Brand Assets", "Brand Guidelines"
   - "Media", "Media Kit", "Press", "Press Kit"
   - "Resources", "Downloads", "Assets"

2. Try common URL patterns:
   - `{base_url}/brand`
   - `{base_url}/brand-kit`
   - `{base_url}/media`
   - `{base_url}/press`
   - `{base_url}/assets`
   - `{base_url}/resources`

3. If a brand kit is found:
   - Look for downloadable logo packages (often ZIP files)
   - Prefer SVG or vector formats over raster images
   - Choose the most square logo variant if multiple options exist
   - If both light and dark themes are present, get both
   
If a brand kit is found with suitable logos, proceed to Step 4.

## Step 2: Check GitHub repository

Many projects host their logos in their GitHub repositories:

1. Find the project's GitHub repository (often linked in website footer/header)

2. Search these common locations:
   - `/assets/` or `/images/` directories
   - `/branding/` or `/brand/` directories
   - `/public/` directory (for web apps)
   - `/docs/` or `/documentation/` directories
   - Root directory (README badges, logo files)

3. Check the README.md for embedded logos:
   - Look for `<img>` tags or markdown image syntax
   - These often point to high-quality logo files

4. Search for files named:
   - `logo.svg`, `logo.png`, `logo-*.svg`
   - `{project-name}.svg`, `{project-name}-logo.*`
   - `brand.*`, `icon.*`

If a suitable logo is found, proceed to Step 4.

## Step 3: Extract from website directly

If no brand kit or GitHub assets are available, extract the logo from the website:

### Option A: Using MCP Playwright (recommended for dynamic sites)

Use the MCP Playwright tool to:

1. Navigate to the homepage
2. Look for logo elements in the header (typically top-left):
   - Search for `<img>` tags with class/id containing "logo"
   - Check for `<svg>` elements in the header
   - Look for elements with `role="img"` and logo-related aria labels

3. Extract the image source URL or SVG content

### Option B: Check meta tags and favicon

1. **Open Graph image** - Check for `<meta property="og:image">` tag
   - Often a high-quality image suitable for social sharing
   - May include branding elements

2. **Favicon** - Check for high-resolution favicon:
   - `<link rel="icon" type="image/svg+xml">` (best - vector)
   - `<link rel="apple-touch-icon">` (usually 180x180 PNG)
   - `/favicon.ico` (low resolution, last resort)

3. **Twitter Card image** - Check `<meta name="twitter:image">`

## Step 4: Verify logo quality

Before saving, verify the extracted logo meets quality standards:

### Quality checklist

- [ ] **Format**: SVG preferred, PNG acceptable (minimum 256x256), avoid JPG for logos
- [ ] **Transparency**: Logo should have transparent background (not white/coloured)
- [ ] **Clarity**: No compression artifacts, pixelation, or distortion
- [ ] **Completeness**: Full logo visible, not cropped or cut off
- [ ] **Correct variant**: Primary logo, not a favicon or simplified icon (unless that's what's needed)

### Size guidelines

| Format | Minimum size | Recommended |
|--------|--------------|-------------|
| SVG    | N/A (vector) | Always prefer |
| PNG    | 256x256      | 512x512 or larger |
| ICO    | Avoid        | Only for favicons |

If the logo quality is insufficient, return to earlier steps or inform the user that only low-quality versions are available.

## Step 5: Download and save

1. **For SVG files**:
   - Download the raw SVG content
   - Ensure the file is valid XML
   - Save with `.svg` extension

2. **For raster images (PNG, etc.)**:
   - Download the highest resolution available
   - Verify file integrity after download
   - Save with appropriate extension

3. **Verify the saved file**:
   - Open the file to confirm it displays correctly
   - Check file size is reasonable (not 0 bytes or corrupted)

### Download methods

Using curl:
```shell
curl -o "{save_path}" "{logo_url}"
```

Using Python:
```python
import requests

response = requests.get("{logo_url}")
with open("{save_path}", "wb") as f:
    f.write(response.content)
```

For SVG content extracted directly:
```python
with open("{save_path}", "w") as f:
    f.write(svg_content)
```

## Step 6: Report results to user

Provide the user with:

1. **Source location** - Where the logo was found (brand kit URL, GitHub path, etc.)
2. **File details** - Format, dimensions (for raster), file size
3. **Saved path** - Confirm where the file was saved
4. **Alternative versions** - Note if other variants are available (dark mode, icon-only, etc.)

## Troubleshooting

### No logo found

If no suitable logo can be found:

1. Check if the project uses a different primary domain
2. Look for the project on social media (Twitter/X, LinkedIn) - profile images are often logos
3. Search "[project name] logo" on image search engines
4. Check DeFiLlama, CoinGecko, or similar aggregators for crypto projects
5. Ask the user if they have alternative sources or contacts

### Logo is low quality

If only low-quality versions are available:

1. Inform the user of the limitation
2. Suggest contacting the project directly for brand assets
3. Offer to save the best available version with a note about quality

### Dynamic/JavaScript-rendered logos

If the logo is rendered via JavaScript:

1. Use MCP Playwright to load the page fully
2. Wait for dynamic content to render
3. Extract from the rendered DOM

### Logo requires authentication

If brand assets are behind a login:

1. Inform the user
2. Provide the URL to the brand kit page
3. Ask the user to download manually or provide credentials (if appropriate)
