# K3 Capital logo sources

## Source

- **Official homepage**: <https://www.k3.capital/>
- **Official favicon / brandmark**: <https://cdn.prod.website-files.com/675844d9a635db4e94a73237/67584945e3b5b3fcb424e763_k3-256.png>
- **Downloaded**: 2026-07-30

The favicon is K3 Capital's icon-only ``K3`` brandmark (256×256). It does not
contain the organisation's wordmark, so it remains readable at small sizes.
The source favicon has an opaque near-black background; the transparent source
variant in this folder removes that background while preserving antialiased
brandmark edges.

## Processing

``k3-capital.brandmark-transparent.png`` was passed through
``scripts/logos/post-process-logo.py`` for the generic and light variants. The
dark variant is the processed generic mark with its RGB channels inverted;
alpha transparency is preserved. All outputs are 256×256 PNGs with transparent
backgrounds:

- ``formatted_logos/k3-capital/generic.png`` — white K3 mark
- ``formatted_logos/k3-capital/light.png`` — white K3 mark for dark surfaces
- ``formatted_logos/k3-capital/dark.png`` — black K3 mark for light surfaces
