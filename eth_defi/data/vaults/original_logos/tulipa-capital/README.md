# Tulipa Capital logo sources

## Source

- **Icon-only favicon/brandmark**: Downloaded from the official Tulipa Capital homepage on 2026-07-30.
  - Homepage: https://tulipa.capital
  - Favicon URL: `https://cdn.prod.website-files.com/66ab1bc68fe996854379ab91/66ac6785bfca3d9d1da61af5_favicon.png`
  - High-resolution icon URL: `https://cdn.prod.website-files.com/66ab1bc68fe996854379ab91/66ac67b876773dea668dad97_big-favicon.png`
- **Combination mark (reference)**: Downloaded from the same official homepage.
  - Direct URL: `https://cdn.prod.website-files.com/66ab1bc68fe996854379ab91/66b1fc5fa5bb3d8ed0e5b13e_Tulipa_Capital_Logo%20export_Bi-Color%20Black.svg`

## Selection and processing

The icon-only favicon was selected instead of the wide combination mark because the vault metadata needs a compact, favicon-like brandmark that remains readable in a small screen estate. The high-resolution icon was passed through `scripts/logos/post-process-logo.py` using the Poetry environment. The pipeline removed the white background, trimmed transparent margins, and scaled the mark to 256×256 pixels.

Processed outputs are in `eth_defi/data/vaults/formatted_logos/tulipa-capital/`:

- `generic.png`
- `light.png`
- `dark.png`

All three files are 256×256 RGBA PNGs with transparent backgrounds. The orange Tulipa mark is sufficiently bright for both light and dark backgrounds, so the same official brandmark is used for each standardised variant.
