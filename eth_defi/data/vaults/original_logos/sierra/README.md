# Sierra logo sources

## Sources

- **Official homepage**: https://sierra.money/ (accessed 2026-07-30)
- **Official documentation**: https://docs.sierra.money/
- **Brandmark source**: the icon-only PNG embedded in the homepage navigation (`<img class="logo-mark">`).

The homepage's navigation publishes Sierra's circular mountain-and-sun icon separately from the “Sierra Protocol” wordmark. This compact brandmark is the appropriate artwork for small vault cards and favicon-sized displays.

## Files

- `sierra.generic.png` - 200×200 transparent PNG extracted from the official homepage; icon only.
- `sierra.wordmark.png` - Previous wide Sierra + Protocol source retained for provenance, not used for processing.

The standard outputs in `eth_defi/data/vaults/formatted_logos/sierra/` were generated from `sierra.generic.png` with `scripts/logos/post-process-logo.py` on 2026-07-30:

- `generic.png`, `dark.png`, `light.png` - 256×256 RGBA PNGs. Sierra publishes one colourful brandmark rather than separate theme variants, so all three outputs use that official transparent icon.

