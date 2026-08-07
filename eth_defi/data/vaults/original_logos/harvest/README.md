# Harvest logo

The Harvest curator uses the official Harvest icon/brandmark from the
organisation's website.  The source is the square icon referenced by Harvest's
organisation metadata:

- Source page: <https://harvest.finance/>
- Source asset: <https://harvest.finance/icon.png>
- Retrieved: 2026-07-30
- Original file: `harvest.brandmark.png` (194 × 194 RGBA PNG)

This is the compact orange square with the black `H.` symbol, rather than the
horizontal `Harvest` wordmark.  It is therefore legible at the small display
size used for curator logos.  The source icon is already transparent and
square, so no background removal or padding was required.

The three 256 × 256 transparent variants in
`eth_defi/data/vaults/formatted_logos/harvest/` were generated from this source
with `scripts/logos/post-process-logo.py` using the `extract-project-logo` and
`post-process-logo` workflows.  Harvest supplies one colour icon, so the
generic, light, and dark files intentionally use the same official brandmark.
