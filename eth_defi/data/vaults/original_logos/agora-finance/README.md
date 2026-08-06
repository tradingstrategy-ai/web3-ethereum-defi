# Agora Finance logo assets

## Sources

- **Official homepage**: https://www.agora.finance/
- **Official generated favicon/app icon**: https://www.agora.finance/icon
- **Official homepage header mark**: https://www.agora.finance/ (inline SVG in the `header-logo` link)
- **Official public assets repository**: https://github.com/agora-finance/public-assets

## Downloaded files

| File | Description |
|------|-------------|
| `agora-finance.generic.svg` | Official icon-only Agora brandmark, extracted from the homepage inline SVG, in the brand green `#476352`. |
| `agora-finance.dark.svg` | Same official green icon-only mark for the dark-on-light variant. |
| `agora-finance.light.svg` | White icon-only mark matching the homepage header's official `brightness-0 invert` presentation, for dark backgrounds. |

The previous source was a wide wordmark. It has been replaced because vault metadata uses the icon-only brandmark and the homepage exposes the triangular mark as a standalone favicon/header symbol.

## Processing result

The three SVG brandmark variants were processed with `scripts/logos/post-process-logo.py` into transparent 256×256 PNG files:

- `formatted_logos/agora-finance/generic.png` — green mark
- `formatted_logos/agora-finance/dark.png` — green mark for light backgrounds
- `formatted_logos/agora-finance/light.png` — white mark for dark backgrounds

## Extraction date

2026-07-30
