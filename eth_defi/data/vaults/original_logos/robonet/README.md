# Robonet logo sources

## Sources

- **Homepage**: https://robonet.finance
- **Official logo SVG**: https://robonet.finance/images/logo/logo.svg (light purple `#D5C5FF`, transparent, 170x37 viewBox)
- **Official favicon**: https://robonet.finance/favicon.ico (32x26 ICO, confirms the compact mark used by the site)

## Files

- `robonet.generic.svg` — Original official combination logo (wordmark plus mark), retained as the source record.
- `robonet.brandmark.svg` — Official compact Robonet mark extracted from the leftmost 44.44×37 region of the published SVG; no text is included.
- `robonet.brandmark.ico` — Favicon downloaded from the official site for provenance (the vector mark is used for processing because it has higher quality).

## Processing

The compact vector mark was passed through `scripts/logos/post-process-logo.py` to create transparent 256×256 `generic.png`, `light.png`, and `dark.png` files. Robonet publishes one light-purple mark and no separate dark-text variant, so all three outputs preserve the official colour and shape.

## Notes

- The previous source was a wide wordmark (~4.6:1 aspect ratio), which is unsuitable for small screens. The new outputs use the official favicon/mark instead.
- No public brand kit or separate colour variants were found; the single published mark is the authoritative source.
