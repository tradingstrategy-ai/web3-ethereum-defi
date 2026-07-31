# Reservoir logo sources

## Source

- **Brandmark PNG**: Official Reservoir Webflow webclip/icon asset, downloaded
  2026-07-30 from the Reservoir website
  - URL: `https://cdn.prod.website-files.com/64127710d8bb7f3867dd0a72/65f325e53d4665b4cad32836_Webclip.png`
  - Website: `https://reservoir.xyz`
  - Saved as `reservoir.brandmark.png` (256×256 RGBA, transparent background)

The asset is the square geometric Reservoir mark without the Reservoir wordmark,
so it remains legible in the small vault-card layout.

## Processing

`reservoir.brandmark.png` was passed through
`scripts/logos/post-process-logo.py` to produce transparent 256×256 PNGs at
`eth_defi/data/vaults/formatted_logos/reservoir/`:

- `generic.png`
- `dark.png`
- `light.png`

The existing `reservoir.dark.svg` and `reservoir.light.svg` files are retained
as the original wordmark references, but are no longer used for the formatted
vault logo.
