# Re7 Labs logo sources

- `re7-labs.generic.svg` and `re7-labs.brandmark.svg` — the official icon-only
  favicon from https://re7.capital/icon0.svg (retrieved 2026-07-30). This is
  the blue Re7 mark and contains no text, so it remains legible in a small
  curator tile.
- `re7-labs.wordmark.svg` — the official Re7 Capital header wordmark from
  https://re7.capital/img/logo.svg, retained as a source reference only and
  not used for the formatted curator artwork.
- `re7-labs.brandmark.png` — existing white icon-only source retained for the
  dark-background variant.

The icon source was post-processed with
`scripts/logos/post-process-logo.py` into 256×256 transparent PNGs:
`generic.png` and `light.png` use the official blue mark, while `dark.png`
uses the white mark for dark backgrounds. The old wordmark is not used in
any formatted output.
