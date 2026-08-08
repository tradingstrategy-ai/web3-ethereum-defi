# MEV Capital logo sources

The MEV Capital homepage does not publish an independent geometric brandmark.
Its compact favicon/webclip is the official three-letter ``MEV`` monogram, so
that mark is used here instead of the full ``MEV Capital`` wordmark. This keeps
the curator logo legible in a small square while preserving the official
identity.

- Homepage: https://www.mevcapital.com (checked 2026-07-30)
- Official favicon: https://cdn.prod.website-files.com/68515e2e9ecee091e255d1b7/686ea412b44686151330122b_favicon.png
- Official 256px webclip: https://cdn.prod.website-files.com/68515e2e9ecee091e255d1b7/686ea415e6a69cc5cc01b68e_webclip.png
- Full wordmark (kept as the existing source for provenance): https://cdn.prod.website-files.com/68515e2e9ecee091e255d1b7/6852776785e2750535fd02be_mevcapital-logo.svg

``mev-capital.icon.png`` was derived from the official webclip by removing its
uniform dark background while retaining anti-aliased white pixels. The
repository ``post-process-logo.py`` skill then generated 256x256 transparent
outputs. ``generic.png``/``light.png`` contain the white monogram for dark
surfaces; ``dark.png`` is its transparent black inverse for light surfaces.
