# Partners Group logo sources

## Source

- **Homepage**: https://www.partnersgroup.com/en
- **Official SVG wordmark**: https://www.partnersgroup.com/~/media/Images/P/Partnersgroup/Universal/logo/partners-group.svg
- **Official favicon/brandmark source**: https://www.partnersgroup.com/~/media/4CC79F7AEDFC4CA98E9B89CC221E0158.jpg
- **Retrieved**: 2026-07-30

## Files

- `partners-group.brandmark.jpg` — the official 220×220 site favicon. It uses
  the compact PG monogram and red bars, without the Partners Group wordmark.
- `partners-group.brandmark.light.png` — transparent light-background variant
  derived from the official favicon for use on dark surfaces.
- `partners-group.generic.svg` — the original official wordmark, retained as a
  source reference only.

## Processing

The favicon was selected because it is the organisation's official compact
brandmark, while the former source was a wide wordmark. The brandmark was
processed with `scripts/logos/post-process-logo.py` to produce 256×256
transparent `generic.png`, `dark.png`, and `light.png` files under
`eth_defi/data/vaults/formatted_logos/partners-group/`. The dark variant keeps
the black PG monogram and red bars; the light variant changes the monogram to
white while preserving the red bars.
