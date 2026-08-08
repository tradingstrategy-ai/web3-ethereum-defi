# Apostro logo sources

## Sources

- Homepage: <https://www.apostro.xyz/>
- Official brand-assets page: <https://www.apostro.xyz/brand-assets>
- Official icon source: <https://cdn.prod.website-files.com/62fe4a146ead545f426e50c8/6304ba7961f2e8d5c4e6e40b_logo-white.svg>
- Downloaded: 2026-07-30

## Files

| File | Description | Source URL |
|------|-------------|------------|
| `apostro.generic.svg` | Black Apostro “A” brandmark (icon only), converted from the official white icon for the light-background variant | https://cdn.prod.website-files.com/62fe4a146ead545f426e50c8/6304ba7961f2e8d5c4e6e40b_logo-white.svg |
| `apostro.light.svg` | White Apostro “A” brandmark (icon only) | https://cdn.prod.website-files.com/62fe4a146ead545f426e50c8/6304ba7961f2e8d5c4e6e40b_logo-white.svg |
| `apostro.wordmark.svg` | Original full Apostro wordmark retained for provenance; not used for vault logos | https://cdn.prod.website-files.com/62fe4a146ead545f426e50c8/66e1a8948ed2b1cd39cd4cee_apostro.svg |

## Processing

The compact icon-only mark is preferred over the wide wordmark for small-screen vault artwork. The standard `post-process-logo.py` pipeline was run on the brandmark sources and produced 256×256 transparent PNGs in `eth_defi/data/vaults/formatted_logos/apostro/` (`generic.png` and `light.png`). `dark.png` is the same processed brandmark with RGB channels inverted to provide the black-on-transparent variant.
