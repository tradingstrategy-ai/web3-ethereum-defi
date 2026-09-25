# ARK Invest logo source

- Curator: ARK Investment Management LLC (ARK Invest)
- Official website: <https://www.ark-invest.com/>
- Official header and footer logo (vector word mark):
  <https://assets.arkinvest.com/media-8e522a83-1b23-4d58-a202-792712f8d2d3/90a5283e-7d28-4d42-90cc-2a486d39fdd6/ark-logo-1-1.svg>
  (the footer logo `.../85ec8193-0ac2-4390-875e-1baf591ad319/footer_Logo.svg` is byte-identical)
- Official favicon: <https://www.ark-invest.com/fav.png>
- Official Open Graph image:
  <https://assets.arkinvest.com/media-8e522a83-1b23-4d58-a202-792712f8d2d3/28feb7ae-192b-49f6-b12e-d9f612a1befc/v2.png>
- Downloaded: 2026-09-25

No public brand kit or press kit was found: `/brand`, `/brand-kit`, `/media`,
`/media-kit` and `/press` return 404 on ark-invest.com, and the site links only
to a press contact page.

## Files

- `ark-invest.dark.svg` – the official "ARK INVEST" word mark with the circular
  brand mark, a single `#0a0a23` path on a transparent background (dark logo
  for light backgrounds), 143x51.5 viewBox, downloaded unchanged from the
  website header.
- `ark-invest.brand-mark.dark.svg` – the same official SVG with only the
  `viewBox` and `width` narrowed to `0 0 51.5378 51.5378`, so that it shows
  the square circular "ARK" brand mark without the word mark. The path data is
  unchanged.
- `ark-invest.favicon.png` – the official 52x52 violet favicon brand mark.
- `ark-invest.og-image.png` – the official 8000x2376 Open Graph image showing
  the light grey brand mark on a grey background.

## Formatted logo

`formatted_logos/ark-invest/generic.png` was produced from
`ark-invest.brand-mark.dark.svg` with `scripts/logos/post-process-logo.py`. The
pipeline inverted the dark navy mark into a light mark for dark backgrounds and
saved it as a 256x256 transparent PNG.
