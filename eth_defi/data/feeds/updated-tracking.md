# Feed data update tracking

Updated 2026-04-06 after first production scan identified broken RSS and Twitter entries.

## Summary

- **45 YAML files updated** across protocols, curators, and stablecoins
- **36 Twitter handles fixed** (X API couldn't resolve old handles)
- **7 RSS 404 errors resolved** (3 fixed with correct URLs, 4 confirmed no RSS available)
- **2 DNS failures resolved** (found alternative RSS URLs)
- **2 dead projects identified** (flexusd/CoinFLEX, bdo/bDollar)
- **2 winding-down projects noted** (Mountain Protocol acquired, Angle Protocol shuttering)

## Twitter handle corrections

These handles were wrong or outdated - the X API correctly reported them as unknown.

| File | Old handle | New handle | Reason |
|------|-----------|------------|--------|
| usdt, eurt, xaut, cnht, usdt-e, usd-t0, usd-t | Tether_to | tether | Tether rebranded handle |
| ethena, usde, usdtb, susde | ethena_labs | ethena | Ethena shortened handle |
| susd, seur | synthetix_io | synthetix | Synthetix dropped _io suffix |
| usdm | MountainPrtcl | MountainUSDM | Different handle than expected |
| usdo | OpenEdenLabs | OpenEden_X | OpenEden rebranded |
| usdh | native_markets | nativemarkets | No underscore |
| usdm-megaeth | megaborealeth | megaeth | MegaETH main account |
| tusd, tcnh | TrueUSD | tusdio | TrueUSD rebranded to tusd.io |
| par | mimodefi | mimo_labs | MIMO Protocol rebranded |
| lvlusd | leveldotmoney | levelusd | Level Money changed handle |
| iusd-indigo | IndigoProtocol1 | Indigo_protocol | Was Medium username, not Twitter |
| fxd | FathomProtocol | fathom_fi | Fathom uses @fathom_fi |
| euroe | membrane_fi | EUROemoney | Product-specific account (Paxos acquired Membrane) |
| cusd, ceur | MentoProtocol | MentoLabs | Mento rebranded |
| veur | VNX_fi | vnx_platform | VNX uses @vnx_platform |
| grvt | GRVT_Exchange | grvt_io | GRVT main account is @grvt_io |
| deusd | ElixirProtocol | elixir | Elixir shortened handle |

## Twitter handles confirmed correct (API resolution was transient failure)

| File | Handle | Status |
|------|--------|--------|
| usdf-falcon | FalconStable | Active, confirmed correct |
| tfusdc | TrueFi_DAO | Active, confirmed correct |
| infinifi | infinifi_ | Active, confirmed correct |
| ember | EmberProtocol_ | Active, confirmed correct |
| smardex | SmarDex | Active, confirmed correct |

## RSS feed fixes

### Fixed with correct URLs

| File | Old URL | New URL | Issue |
|------|---------|---------|-------|
| csigma-finance | medium.com/feed/csigma | medium.com/feed/@csigma | Publication vs user profile format |
| gamma-strategies | medium.com/feed/gamma-strategies | medium.com/feed/@gammastrategies | Migrated from publication to user profile |
| silk | blog.shadeprotocol.io/rss.xml | medium.com/feed/@shadeprotocoldevs | Blog subdomain DNS dead |
| usda | blog.angle.money/rss/ | medium.com/feed/angle-protocol | Blog subdomain DNS dead |
| ethena, usde, usdtb, susde | ethena.fi/blog/feed | mirror.xyz/0xF99d0E4E3435cc9C9868D1C6274DfaB3e2721341/feed/atom | Blog page not valid RSS |

### RSS added (new discovery)

| File | RSS URL | Source |
|------|---------|--------|
| usdt, eurt, xaut, cnht, usdt-e, usd-t0, usd-t | tether.io/feed/ | WordPress blog at tether.io |

### RSS confirmed unavailable (commented out)

| File | Tried | Finding |
|------|-------|---------|
| auto-finance | blog.auto.finance/rss | Custom blog platform, no RSS support |
| nashpoint | blog.nashpoint.fi, blog.nashpoint.finance | Next.js + Sanity CMS, no RSS |
| centrifuge | centrifuge.io/blog/feed, medium.com/centrifuge | No RSS on any platform |
| paxg | paxos.com/blog-news-stories/feed/, paxos.com/blog/feed/ | Static site, no RSS |
| rlusd | ripple.com/insights/feed/ | Next.js app, no RSS |

## Dead or winding-down projects

| File | Project | Status |
|------|---------|--------|
| flexusd | CoinFLEX | DEFUNCT - collapsed mid-2022, rebranded to OPNX which shut down Feb 2024. flexUSD trading at ~$0.07 |
| bdo | bDollar | DEAD - early BSC algorithmic stablecoin (Jan 2021), no activity since |
| tcnh | TrueCNH | Effectively dead - $0 market cap, TRON-only, same team as troubled TUSD |
| usdm | Mountain Protocol | Winding down - acquired by Anchorage Digital, new USDM minting disabled May 2025 |
| usda | Angle Protocol | Shuttering - governance vote AIP-112 to wind down USDA and EURA, redemptions until March 2027 |
| tusd | TrueUSD | Damaged - SEC settled fraud charges Sept 2024, reserves invested in speculative offshore fund |

## Website corrections

| File | Old website | New website | Reason |
|------|------------|-------------|--------|
| auto-finance | tokemak.xyz | auto.finance | Auto Finance is the 2025 Tokemak rebrand |
| usdh | nativemarkets.xyz | nativemarkets.com | .com is the active domain |
| tusd | trueusd.com | tusd.io | Redirects to tusd.io |

## Remaining issues not addressed

- **63 files with RSS 429 errors**: These are Medium feeds that were rate-limited during scanning. The URLs are likely correct; they just need retry with backoff.
- **58 files with rss-dead-at**: RSS feeds that exist but haven't posted in 1+ year. May indicate inactive projects or blogs migrated elsewhere.
- **41 files with twitter-dead-at**: Twitter accounts that stopped posting. May indicate inactive projects.
- **72 files with linkedin-rss-hub-disabled-at**: LinkedIn requires authentication; RSSHub bridges return 503. This is a platform-level issue, not per-file.
