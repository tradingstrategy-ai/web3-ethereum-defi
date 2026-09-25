# Monthly vault report: blog post outline

This file describes the structure of the monthly "The best-performing stablecoin
vaults" post on the [Trading Strategy blog](https://tradingstrategy.ai/blog). It
covers what each section shows, how its vaults are selected and what the editor
adds. The pipeline that generates the post is described in
[README-vault-report.md](./README-vault-report.md). The section order lives in
`eth_defi.vault_report.post.SECTION_TEMPLATES`, and the thresholds in
`eth_defi.vault_report.sections.ReportCriteria`.

## Post metadata

| Field | Value |
|---|---|
| Title | The best-performing stablecoin vaults, {Month YYYY} |
| Slug | `the-best-performing-stablecoin-vaults-{month}-{yyyy}` |
| Excerpt | The best stablecoin yield in DeFi, {Month YYYY} report. |
| Feature image | 1200×630 hero: the top 5 stablecoin yield vaults with 90-day sparklines, excluding Severe and Dangerous risk vaults |
| Social image for X | `hero-square.png`, 1080×1080, attached by hand when posting |

## Vault groups

Every stablecoin vault belongs to one group (`classify_vault()`):

| Group | Rule |
|---|---|
| Perpetual futures DEX | Flagged `perp_dex_trading_vault`: Hyperliquid, GRVT, Lighter, Hibachi, ApeX |
| Tokenised fund | Flagged `tokenised_fund`: money market, treasury and credit funds such as BlackRock BUIDL |
| Lending | A lending strategy tag, or a known lending protocol (`LENDING_PROTOCOL_SLUGS`: Aave, Morpho, Euler, Fluid, Spark, Silo, Llama Lend, Curvance and others) |
| Other | Everything else: yield aggregators, trading vaults, RWA and synthetic dollar vaults |

## Common rules

- **Eligibility:** blacklisted vaults and vaults whose data is more than a week older than the report date are left out of every section.
- **Ranking metric:** "by return" is the annualised one-month return (1M CAGR), net of fees (n) when fee data exists and gross (g) otherwise. The website ranks vaults by the same metric. "By Sharpe" is the three-month Sharpe ratio.
- **Table thresholds:** at least $100k TVL. Lending and other vaults also need at least 10 deposit and redemption events. Tables list the top 20.
- **Performance charts:** 90-day equity curves, in percent, of the top 8 vaults of the table below, all in one chart with a shared axis so they can be compared directly. The chart subtitle states the minimum TVL, and the legend shows annualised returns. Legend numbers are table ranks. The benchmarks used by at least half of the vaults are drawn in grey: the US 3M T-bill for calm yield vaults, BTC and ETH for perp DEX and volatile vaults. A single vault far above the others is drawn off scale, and returns above 100% switch the axis to a log scale.

## Outline

| # | Heading | Content | Editor input |
|---|---|---|---|
| — | *Intro paragraph* | One generated sentence | ✏️ The month's highlight |
| 1 | About the report | Copied from the previous post | — |
| 2 | Report content updates | Data statistics: chains, protocols, vault count, TVL, stablecoins | ✏️ New integrations; changelog candidates are listed |
| 3 | DeFi vault community news, {Month} | — | ✏️ News as `h3` subsections |
| 4 | Average yield by blockchain | Dot plot of the **10 largest blockchains by TVL** | ✏️ Comment |
| 5 | Average yield by protocol | Dot plot of the **10 largest identified protocols by TVL**, each with at least $1M TVL | — |
| 6 | Stablecoin vault TVL by protocol | Stacked weekly TVL over 12 months | ✏️ Comment on the trend |
| 7 | Inflows and outflows | The **10 largest TVL increases and decreases over 30 days**, in dollars | ✏️ Explain the largest moves |
| 8 | The best-performing vaults | Treasury bill caption: how many yield vaults beat the T-bill, and their median return | ✏️ Comment on the top vaults |
| 8.1 | ↳ Lending vaults | Performance chart and table, **by return** | — |
| 8.2 | ↳ Perpetual futures DEX vaults by return | Performance chart against BTC and ETH, and table, **by return** | — |
| 8.3 | ↳ Perpetual futures DEX vaults by Sharpe ratio | Performance chart and table, **by 3M Sharpe** | — |
| 8.4 | ↳ Other vaults | Performance chart and table, **by return** | — |
| 9 | The best-performing tokenised funds | Performance chart and table, by return | — |
| 10 | The best-performing new vaults | Table: launched in the last 60 days, at least $15k TVL | — |
| 11 | Risk and return | Bubble scatter of 3M volatility against 3M return for the yield vaults | — |
| 12 | The best-performing vaults on each chain | Table: the top 3 per chain with at least $100k TVL | — |
| 13 | Partners | Copied from the previous post | — |
| 14 | Next steps | Copied from the previous post | — |

The average yield charts (sections 4 and 5) show:
- each vault as a small dot and the TVL-weighted average as a large dot;
- the US 3M T-bill as a dashed line;
- a right-hand column with the average, its difference to the T-bill in percentage points, and the TVL.

Outliers above 400% annualised return or 50% annualised volatility are left out of the averages.

## Changes from the earlier outline

| Change | Reason |
|---|---|
| Correlation of returns removed | Hard to read, and little editorial value |
| Top movers replaced with inflows and outflows | Dollar TVL changes show where money moved. Rank moves were dominated by the gap between reports and by changes in the vault universe |
| Best-performing vaults split into lending, perp DEX by return, perp DEX by Sharpe and other | Each group has different risk and return, and a different benchmark |
| Best-performing large vaults folded into the split tables | The $100k threshold and the TVL column cover large vaults |
| Separate perp DEX section folded into 8.2 and 8.3 | Part of the split |
| Tokenised funds section added | Funds such as BlackRock BUIDL move the largest amounts, and readers compare them with DeFi yield |
| Average yield by protocol added; average yield by blockchain limited to the 10 largest chains | Comparable overviews of the largest markets |
| Low-volatility performance chart removed | Covered by the lending section |
| Performance small multiples replaced with one shared chart per section | Equity curves can only be compared on the same axis |

## Decisions to confirm

1. **"By CAGR" means the annualised one-month return**, the metric the website ranks by. Ranking by three-month CAGR would be steadier; for perp DEX vaults, the one-month value often hits the export's 10,000% cap.
2. **The top 10 protocols are the largest by TVL, each with at least $1M TVL.** Ranking by yield among protocols with $1M TVL gives a different list, led by small volatile protocols: YieldBasis, GMX and Enzyme were all above 90% in September 2026.
3. **TVL changes include returns as well as deposits and redemptions.** The export has estimated net flows (`flow_value`) for only about 1,100 of 5,500 vaults, so dollar TVL changes are used. Funds listed under several share tokens, such as the Janus Henderson Anemoy fund classes on Centrifuge, can appear more than once.
4. **The lending classification is partly a curated protocol list**, because strategy tags cover only Aave, Euler and Morpho vaults. Tagging more protocol adapters would retire the list.
5. **Kept from the earlier outline:** TVL by protocol, risk and return, and the per-chain table. Drop any of them to shorten the post.
