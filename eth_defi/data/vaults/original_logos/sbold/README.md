# sBOLD / K3 Capital logo assets

## Source

sBOLD is developed by K3 Capital. The sBOLD repository does not publish a
separate product icon, so the curator artwork uses K3 Capital's official
icon-only favicon. This is the compact K3 brandmark and contains no wordmark,
which keeps it readable in the curator list at small sizes.

- **Official homepage:** <https://www.k3.capital/>
- **Official favicon / brandmark:** <https://cdn.prod.website-files.com/675844d9a635db4e94a73237/67584945e3b5b3fcb424e763_k3-256.png>
- **Downloaded:** 2026-07-30

## Files

| File | Description |
|------|-------------|
| `sbold.brandmark.png` | Official K3 icon-only favicon source (opaque PNG) |
| `sbold.brandmark-transparent.png` | Transparent K3 brandmark used for processing |
| `k3-capital.brandmark.dark.png` | Existing extracted black K3 brandmark |
| `k3-capital.brandmark.light.png` | Existing extracted light K3 brandmark |
| `k3-logo.svg`, `k3-logo-light.svg` | Official K3 Capital wordmark sources retained for provenance |
| `sbold.twitter.jpg` | K3 Capital social avatar retained for provenance |

## Processing

The transparent brandmark was passed through
`scripts/logos/post-process-logo.py` for the generic and light variants. The
dark variant is the processed generic mark with its RGB channels inverted;
alpha transparency is preserved. All outputs are 256×256 RGBA PNGs:

- `formatted_logos/sbold/generic.png` — white K3 mark
- `formatted_logos/sbold/light.png` — white K3 mark for dark surfaces
- `formatted_logos/sbold/dark.png` — black K3 mark for light surfaces

The former formatted outputs contained the full “K3 Capital” wordmark. They
have been replaced with this icon-only brandmark.

## Additional sources

- sBOLD code repository: <https://github.com/K3Capital/sBOLD>
- K3 Capital website logo assets: <https://www.k3.capital/>
