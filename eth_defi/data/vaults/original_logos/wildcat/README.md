# Wildcat logo assets

## Sources

- **Website**: https://wildcat.finance/
- **App**: https://app.wildcat.finance/
- **Docs**: https://docs.wildcat.finance/
- **GitHub**: https://github.com/wildcat-finance

No public brand or media kit was found. The website footer links only to X,
Medium, Telegram and GitHub. The brand marks were therefore taken from the
official website (Webflow CDN) and the official app repositories on GitHub.

## Downloaded files

### Brand mark (logo without text)

| File | Size | Description | Source |
|------|------|-------------|--------|
| `wildcat.light.svg` | 20x20 viewBox | White brand mark with rounded corners (light mark for dark backgrounds) | Website preloader icon: https://cdn.prod.website-files.com/66ffdb8bcd1eefe3f1b06dcd/672b7b60f3c965d07c952907_icon-white.svg |
| `wildcat.dark.svg` | 36x44 viewBox | Near-black (`#141414`) brand mark with sharp corners (dark mark for light backgrounds) | Website header logo: https://cdn.prod.website-files.com/66ffdb8bcd1eefe3f1b06dcd/66ffdc9073218a61d5430379_c53b33b5094dc091ba731f5e8983dba0_Vectors-Wrapper.svg |
| `wildcat.generic.png` | 256x256 px | Favicon: white brand mark on a near-black rounded square | Website favicon: https://cdn.prod.website-files.com/66ffdb8bcd1eefe3f1b06dcd/672819d6038347d2d75f77a6_icon-256.png |

### Word marks and other variants

| File | Size | Description | Source |
|------|------|-------------|--------|
| `wildcat-horizontal.light.svg` | 150x50 viewBox | Current horizontal logo (mark and "WILDCAT" text), white | https://github.com/wildcat-finance/wildcat-app-v2/blob/main/src/assets/icons/logo_white.svg (identical to `public/wildcat-logo.svg`) |
| `wildcat-horizontal.dark.svg` | 150x50 viewBox | Current horizontal logo, `#141414` | https://github.com/wildcat-finance/wildcat-app-v2/blob/main/src/assets/icons/logo_black.svg |
| `wildcat-wordmark-text.dark.svg` | 74x12 viewBox | "WILDCAT" text only, black, as used in the website header next to the mark | https://cdn.prod.website-files.com/66ffdb8bcd1eefe3f1b06dcd/66ffdc912baf9051d404bf58_Vectors-Wrapper.svg |
| `wildcat-v1-square.light.svg` | 119x83 viewBox | Older (v1) white logo, square layout | https://github.com/wildcat-finance/wildcat-landing/blob/main/public/wildcat-logo-white-square.svg |
| `wildcat-v1-horizontal.light.svg` | 151x51 viewBox | Older (v1) white horizontal logo | https://github.com/wildcat-finance/wildcat-landing/blob/main/public/wildcat-logo-white.svg |

The `wildcat-v1-*` files use an earlier, angular mark geometry from the old
landing page repository and are kept for reference only.

## Post-processing choices

Formatted logos are in `eth_defi/data/vaults/formatted_logos/wildcat/`.

- `light.png` is processed from `wildcat.light.svg` with
  `scripts/logos/post-process-logo.py`. The source is already square,
  transparent and white, so no padding, background removal or recolouring
  was needed.
- `dark.png` is processed from `wildcat.dark.svg` with the same pipeline
  steps (SVG conversion, square padding, background check, trim and scale to
  256x256), but without the script's dark-background recolouring step. That
  step would invert the near-black mark to white and defeat the purpose of the
  dark variant.

Brand marks were preferred over word marks because they fit the square listing
format. The two variants use the two official mark renderings found on the
website: the light mark has rounded corners and the dark header mark has sharp
corners. No official rounded dark or sharp white standalone mark was published.

## Extraction date

2026-11-10
