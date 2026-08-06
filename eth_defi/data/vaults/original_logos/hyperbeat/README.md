# Hyperbeat logo sources

## Sources

- **Homepage**: https://hyperbeat.org
- **Square brandmark PNG**: https://hyperbeat.org/icon-512x512.png (official `Organization.logo` in the homepage JSON-LD, downloaded 2026-07-30)
- **Black SVG wordmark (dark)**: https://hyperbeat.org/brand/logo-navbar-black.svg (black fill on transparent background, wide wordmark ~317×53 viewBox)
- **White SVG wordmark (light)**: https://hyperbeat.org/assets/images/landing/cta/hyperbeat-logo-white.svg (white fill on transparent background, wide wordmark ~181×30 viewBox)

## Files

- `hyperbeat.dark.svg` — Black wordmark SVG (logo for use on light backgrounds). Wide format (~6:1 aspect ratio), includes the "H" grid icon mark and the full brand name text.
- `hyperbeat.light.svg` — White wordmark SVG (logo for use on dark backgrounds). Same design with white fill.
- `hyperbeat.brandmark.png` — Official square icon from the homepage JSON-LD (512×512 PNG). This is the source used for the processed vault logo because it is a compact brandmark rather than a wordmark.

## Notes

- The previous files were wide-format wordmarks. The square `icon-512x512.png` is the official standalone mark and replaces them in the processed output.
- `generic.png` and `light.png` were regenerated from `hyperbeat.brandmark.png` with `scripts/logos/post-process-logo.py` (256×256 transparent PNGs).
- Twitter: @hyperbeat
- LinkedIn: linkedin.com/company/hyperbeatorg
