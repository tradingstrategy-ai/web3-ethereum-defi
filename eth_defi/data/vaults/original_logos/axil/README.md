# Axil logo

## Source

- Official website: https://www.axil.pro/
- Brandmark source: https://www.axil.pro/favicon.ico (the site's 256 x 256 favicon)
- Extracted on 2026-07-30.

The favicon is the compact AXIL brandmark (a triangular cut-out in a circular
mark), so it is preferred over the previous wide `AXIL` wordmark for small
vault cards. The original header wordmark remains in `axil.generic.svg` as
provenance, but is not used for the formatted artwork.

## Files and processing

- `axil.brandmark.png` - 256 x 256 RGBA copy of the official favicon.
- `formatted_logos/axil/{generic,light,dark}.png` - 256 x 256 transparent
  outputs generated from the brandmark with `scripts/logos/post-process-logo.py`.

The standard pipeline was run once for each variant:

```shell
for variant in generic light dark; do
  INPUT_IMAGE=eth_defi/data/vaults/original_logos/axil/axil.brandmark.png \
  OUTPUT_IMAGE=eth_defi/data/vaults/formatted_logos/axil/$variant.png \
  poetry run python scripts/logos/post-process-logo.py
done
```
