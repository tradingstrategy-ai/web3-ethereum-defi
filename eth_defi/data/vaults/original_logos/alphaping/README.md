# AlphaPing logo sources

## Sources

- **Homepage**: <https://alphaping.ch>
- **Official favicon / brandmark**: <https://alphaping.ch/favicon.ico?2fbe0cc6bb8fabe4> (served as a 2084×2084 RGBA PNG, white AP symbol on the brand's dark background)
- **Dark SVG wordmark**: <https://alphaping.ch/images/alphaping_logo_black.svg> (black full wordmark)
- **Light SVG wordmark**: <https://alphaping.ch/images/alphaping_logo_white.svg> (white full wordmark)

## Files

- `alphaping.brandmark.png` — Official square favicon containing the icon-only AP brandmark (2084×2084 RGBA PNG).
- `alphaping.dark.svg` — Black full wordmark SVG retained as an alternate source.
- `alphaping.light.svg` — White full wordmark SVG retained as an alternate source.

The favicon brandmark is preferred for vault metadata because it is icon-only and square; the SVGs are wide wordmarks and are retained for reference.

## Processed output

`eth_defi/data/vaults/formatted_logos/alphaping/{generic,dark,light}.png` were generated from `alphaping.brandmark.png` with `scripts/logos/post-process-logo.py`. Each is a 256×256 RGBA PNG containing the icon-only mark centred on a transparent canvas. The source site publishes only the white-on-dark brandmark; therefore the three standard outputs intentionally use that official variant rather than retaining the previous wordmark outputs.
