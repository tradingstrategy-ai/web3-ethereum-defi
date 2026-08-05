# GaoTeng Global Asset Management logo sources

## Sources

- **Official homepage**: https://www.gaotengasset.com/en-US/
- **Official header logo**: https://www.gaotengasset.com/en-US/ (the page embeds the white GaoTeng SVG logo and its icon-only group)
- **Colour logo source**: https://static.asseto.finance/asseto/2026-07-12/djwbvvmlksd4aahpoe.svg (the same GaoTeng mark is published as the Asseto fund-manager partner logo at https://asseto.finance/product)
- **Retrieved**: 2026-07-30

## Files

- `gaoteng-global-asset-management.generic.svg` — original 377×80 transparent GaoTeng wordmark from Asseto, retained for provenance.
- `gaoteng-global-asset-management.brandmark.svg` — the blue/orange icon-only mark extracted from the original vector; no wordmark text.
- `gaoteng-global-asset-management.brandmark.light.svg` — the white icon-only mark extracted from the official website header SVG for dark backgrounds.

## Processing

The existing full wordmark was flagged as unsuitable for small-screen curator cards. The icon-only paths were extracted from the high-resolution vectors above and processed with `scripts/logos/post-process-logo.py` into transparent 256×256 `generic.png`, `dark.png`, and `light.png` files. The generic and dark variants retain GaoTeng's blue/orange colours; the light variant uses the official white icon for dark backgrounds.
