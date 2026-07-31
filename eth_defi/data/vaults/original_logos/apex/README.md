# ApeX logo sources

## Files

| File | Source | Notes |
|------|--------|-------|
| `apex-favicon.png` | https://www.apex.exchange/favicon.ico | 200×200 PNG (served as `.ico`), RGBA; black ApeX ape-mask brandmark on a solid yellow (#FFC412) circle, transparent corners |
| `defillama-apex-omni.jpg` | https://icons.llama.fi/apex-omni.jpg | 250×250 JPEG; same ape-mask brandmark on a yellow→orange gradient circle, white (opaque) corners |

## Selected for formatting

- **generic variant**: `apex-favicon.png` — cleanest source, already a circular brandmark with transparent corners; upscaled to 256×256 for `formatted_logos/apex/generic.png`.

The mark is a black brandmark on a self-contained yellow circle, so it reads on
both light and dark backgrounds and a single `generic` variant is sufficient.
No separate `light` / `dark` variants were produced.

## Sources checked

- **Website**: https://www.apex.exchange/ — `favicon.ico` is a 200×200 PNG brandmark; no `apple-touch-icon.png` (404).
- **Omni app**: https://omni.apex.exchange/ — `apple-touch-icon.png` returned an HTML page, not an image.
- **DeFiLlama**: https://icons.llama.fi/apex-omni.jpg available (250×250 JPEG); `apex-protocol.jpg` and a `.png` variant return 404.
- **CoinGecko**: APEX token image URL guess returned 404; not used.
- **Brand kit / SVG**: none found.

## Notes

- No SVG / vector source found; the 200×200 favicon was the highest-quality
  transparent-corner asset available.
- Official brand colour: yellow ≈ `#FFC412`.
