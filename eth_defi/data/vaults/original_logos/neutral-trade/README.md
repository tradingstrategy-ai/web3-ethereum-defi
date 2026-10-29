# Neutral Trade curator logo

The [Neutral Trade brand kit](https://docs.neutral.trade/additional-info/brand-kit)
points to the official [neutral-trade/design-assets](https://github.com/neutral-trade/design-assets)
GitHub repository. The files below were downloaded from that repository at
commit `521dc01ace9e001000f51dbd1ab26123416c2651`.

- `neutral-trade.generic.svg`: [orange gradient brand mark](https://github.com/neutral-trade/design-assets/blob/521dc01ace9e001000f51dbd1ab26123416c2651/logos/marks/nt-logo-mark-gradient-v1.0.0.svg), 1331 × 992, transparent, 744 bytes.
- `neutral-trade.light.svg`: [white brand mark](https://github.com/neutral-trade/design-assets/blob/521dc01ace9e001000f51dbd1ab26123416c2651/logos/marks/nt-logo-mark-white-v1.0.0.svg) for dark backgrounds, 1332 × 992, transparent, 301 bytes.
- `neutral-trade.dark.svg`: [grey (`#2C2C2C`) brand mark](https://github.com/neutral-trade/design-assets/blob/521dc01ace9e001000f51dbd1ab26123416c2651/logos/marks/nt-logo-mark-grey-v1.0.0.svg) for light backgrounds, 1332 × 992, transparent, 305 bytes.
- `neutral-trade.wordmark.light.svg`: [wordmark with the gradient mark and white "Neutral" text](https://github.com/neutral-trade/design-assets/blob/521dc01ace9e001000f51dbd1ab26123416c2651/logos/wordmarks/nt-wordmark-neutral-font1-white-v1.0.0.svg), 3624 × 992, transparent, 4,235 bytes.

Downloaded 2026-10-29. The brand kit has no dark-text wordmark. It also
publishes the marks on solid black and white backgrounds, which are not
needed here.

The [Neutral Trade website](https://www.neutral.trade) header uses the same
gradient mark. The curator's X account is [@TradeNeutral](https://x.com/TradeNeutral).

## Formatted logos

The formatted logos in `eth_defi/data/vaults/formatted_logos/neutral-trade/`
are 256 × 256 transparent PNGs:

- `generic.png` comes from `neutral-trade.generic.svg`. The orange gradient is visible on both light and dark backgrounds.
- `light.png` comes from `neutral-trade.light.svg`.
- `dark.png` comes from `neutral-trade.dark.svg`.

`generic.png` and `light.png` were produced with
`scripts/logos/post-process-logo.py`. That script always inverts dark logos
for dark backgrounds, which would have turned the grey mark light. `dark.png`
was therefore produced with the same script's SVG conversion, square padding
and trim-and-scale functions, without the recolouring step.
