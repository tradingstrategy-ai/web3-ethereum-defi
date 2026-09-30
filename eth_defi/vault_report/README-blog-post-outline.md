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
| Feature image | Left empty for the editor. The bundle's `hero.png` is a ready-made option, 1200×630: the top 5 stablecoin yield vaults with their curator, protocol and chain, and 90-day sparklines, excluding Dangerous or worse risk ratings, returns above 400%, volatility above 50% and vaults the investability check excluded |
| Social image for X | `hero-square.png`, 1080×1080, attached by hand when posting |

## Vault groups

Every stablecoin vault belongs to one group (`classify_vault()`):

| Group | Rule |
|---|---|
| AMM pool | The `amm_pool_like` vault feature (`ERC4626Feature.amm_pool_like`), which the scanner sets for GMX GM and GLV pools and the Curve-based YieldBasis LTs. Strategy tags are not used: vaults tagged `amm`, such as Gains Network gTrade and KiloEx, stay in their other groups |
| Perpetual futures DEX | Flagged `perp_dex_trading_vault`: Hyperliquid, GRVT, Lighter, Hibachi, ApeX |
| Tokenised fund | Flagged `tokenised_fund`: money market, treasury and credit funds such as BlackRock BUIDL |
| Real-world assets (RWA) | An RWA strategy tag (`RWA_STRATEGY_TAGS`: `rwa`, `rwa_credit`, `rwa_lending`, `rwa_royalties`), e.g. private credit, trade finance and royalty vaults. RWA lending vaults count as RWA, not lending |
| Lending | A lending strategy tag, or a known lending protocol (`LENDING_PROTOCOL_SLUGS`: Aave, Morpho, Euler, Fluid, Spark, Silo, Llama Lend, Curvance and others) |
| Other | Everything else: yield aggregators, trading vaults and synthetic dollar vaults. Only vaults with an identified protocol are ranked |

## Unidentified protocols

Vaults whose protocol has not been mapped yet form one **Other** pile, never a
protocol of their own. This covers:

- generic ERC-4626 vaults (`erc-4626`)
- unknown and placeholder protocols (`unknown`, `protocol-not-yet-identified`, `unknown-erc-7450`)
- empty protocol names, "Unknown", "Unknown vault protocol" and `<…>` placeholder names

The rule is `is_identified_protocol()` in `eth_defi.vault_report.sections`, the
same as the website's `isUnknownVaultProtocol()` in the frontend
(`src/lib/top-vaults/helpers.ts`).

**These vaults are counted only in TVL summaries, never in performance
comparisons,** because their data is often broken: generic wrappers, lending
pool receipts and tokens with unusual share accounting produce capped returns,
one-day price jumps and similar artefacts. `select_comparable_vaults()` removes
them before any return is compared.

| Section | Unidentified protocol vaults |
|---|---|
| Report statistics (vault count, combined TVL) | Included; not counted as identified protocols |
| Stablecoin TVL by DeFi vault protocol | Included, summed into Other with the protocols outside the top 7 |
| Stablecoin TVL by blockchain | Included, under their blockchain |
| Inflows and outflows | Included |
| Average yield by blockchain and by protocol | Left out |
| Treasury bill caption | Left out |
| All best-performing tables and their performance charts, including *Other vaults* | Left out |
| Best-performing new vaults, per-chain table, risk and return, hero image | Left out |

The *Other vaults* table is a vault group, see [Vault groups](#vault-groups):
vaults with an identified protocol that are not lending, RWA, perp DEX, AMM or
tokenised fund vaults, for example yield aggregators. It is not the Other protocol pile.

When a protocol is mapped in the vault metadata, its vaults leave the Other
pile automatically. No report change is needed.

## Common rules

- **AMM pools:** their returns include the price moves of the pooled assets, so by default they are ranked only in their own *AMM pools* section, with at least $1M TVL (`ReportCriteria.amm_min_tvl`). They are left out of the average yields, the T-bill caption, the new vaults, the per-chain table, risk and return and the hero image, and counted only in the TVL summaries. `ReportCriteria.include_amm_pools` puts them back in the other rankings.

- **Charts and risk:** charts get shared without the tables around them, so the hero image, the performance charts, the average yield charts and the risk and return chart leave out vaults rated Dangerous or worse (`ReportCriteria.chart_min_excluded_risk`). The tables keep them. Tables have no risk rating column. A performance chart then shows the next vaults instead.
- **No repeated text:** the notes above a chart only add what its title, subtitle and axes do not already say.

- **Investability check:** an agent checks the vaults the rankings would show, see [README-vault-report.md](./README-vault-report.md#investability-check). Vaults that are not investable in practice, such as Morpho or Euler vaults with suspicious collateral or vaults without exit liquidity, are left out of every ranking, chart, the hero image and the inflows and outflows, and the next vaults move up. The TVL totals keep them. Version 1 covers Morpho, Euler and 40acres; more protocols will follow.

- **Eligibility:** blacklisted vaults, rated Blacklisted in the export or given a bad flag in `eth_defi/vault/flag.py` since, and vaults whose data is more than a week older than the report date are left out of every section. Vaults without an identified protocol are left out of every performance comparison, see [Unidentified protocols](#unidentified-protocols).
- **Ranking metric:** tables rank "by return" on the annualised one-month return (1M CAGR), net of fees (n) when fee data exists and gross (g) otherwise, like the website. "By Sharpe" is the three-month Sharpe ratio.
- **Chart metric:** all charts use the steadier annualised three-month return (`sections.CHART_RETURN`): the average yield dot plots, the hero image and the choice of vaults in the performance charts, which show the top 8 of each group by three-month return rather than the top of the table. Their legend numbers are chart ranks. The Sharpe ratio chart ranks by three-month Sharpe ratio. The T-bill caption above the tables stays on one-month returns.
- **Table thresholds:** at least $100k TVL. Lending, RWA and other vaults also need at least 10 deposit and redemption events. Tables list the top 20.
- **Performance charts:** 90-day equity curves, in percent, of the top 8 vaults of the group by three-month return, all in one chart with a shared axis so they can be compared directly. The chart subtitle states the minimum TVL and that the legend returns are annualised; the legend shows the numbers only, without "ann." or a start date. The Sharpe ratio section draws the 90-day rolling Sharpe ratio instead of equity, calculated like the table's 3M Sharpe, so the latest values match the table. The legend return is computed from the chart's daily prices over each line's span, so it can differ from the export's three-month return that chooses and ranks the vaults. Legend numbers are chart ranks; each legend entry shows the vault's curator, protocol and chain with their icons under the name. The benchmarks used by at least half of the vaults are drawn in grey: the US 3M T-bill for calm yield vaults, BTC and ETH for perp DEX and volatile vaults. A single vault far above the others is drawn off scale, and returns above 100% switch the axis to a log scale.

## Outline

Every data section and subsection opens with an introduction paragraph that explains what it shows and links to the matching live page or glossary entry on tradingstrategy.ai. *Next steps*, copied from the previous post, is the call to action at the end.

| # | Heading | Content | Editor input |
|---|---|---|---|
| — | *Intro paragraph* | One generated sentence | ✏️ The month's highlight |
| — | *Table of contents* | `<div id="table-of-contents"></div>` in an HTML card, filled by the Ghost theme from the headings, as in the earlier posts | — |
| 1 | About the report | Copied from the previous post, without empty paragraphs | — |
| 2 | Report content updates | Data statistics with the figures in bold: chains, protocols, vault count, TVL, stablecoins | ✏️ TODO: the new integrations; changelog candidates are in `report.json` |
| 3 | DeFi vault community news, {Month} | — | ✏️ News as `h3` subsections |
| 3b | Latest podcasts | The **4 latest podcast episodes**: guest logo, title linked to the blog post, promotion text, YouTube and Spotify links | — |
| 4 | The best-performing vaults | Treasury bill caption: how many yield vaults beat the T-bill, and their median return | ✏️ Comment on the top vaults; the check's exclusions and undecided vaults |
| 4.1 | ↳ Lending vaults | Performance chart and table, **by return** | — |
| 4.2 | ↳ Real-world asset (RWA) vaults | Performance chart and table, **by return** | — |
| 4.3 | ↳ Perpetual futures DEX vaults by return | Performance chart against BTC and ETH, and table, **by return** | — |
| 4.4 | ↳ Perpetual futures DEX vaults by Sharpe ratio | 90-day rolling Sharpe ratio chart against BTC and ETH, and table, **by 3M Sharpe** | — |
| 4.5 | ↳ Other vaults | Performance chart and table, **by return** | — |
| 4.6 | ↳ AMM pools | Performance chart and table, **by return**, at least **$1M TVL** | — |
| 4.7 | ↳ New vaults | Table: launched in the last 60 days, at least $15k TVL | — |
| 4.8 | ↳ Vaults on each chain | Chart of the two best vaults and the runners-up on each chain, 3M return on a log scale; table of the top 3 per chain with at least $100k TVL | — |
| 4.9 | ↳ Tokenised funds | Performance chart and table, by return | — |
| 5 | Average yield | How to read the dot plots | — |
| 5.1 | ↳ Yield by protocol, high TVL | Dot plot of the **10 largest identified protocols by TVL**, each with at least $1M TVL | — |
| 5.2 | ↳ Yield by protocol, high yield | Dot plot of the **10 highest-yielding identified protocols** among those with at least $150k TVL | ✏️ Where the yield comes from |
| 5.3 | ↳ Yield by blockchain | Dot plot of the **10 largest blockchains by TVL** | ✏️ Comment |
| 6 | Risk and return | Chart titled *Volatility risk and return*: a bubble scatter of 3M volatility against 3M return for the yield vaults. Both axes fit the 1st–99th percentile of vaults, with outliers as edge triangles; vaults with no share price movement are left out | — |
| 7 | Vaults and tokenised funds TVL | — | — |
| 7.1 | ↳ Stablecoin TVL by DeFi vault protocol | Stacked weekly TVL over 12 months, the 7 largest protocols and Other; tokenised funds excluded | ✏️ Comment on the trend |
| 7.2 | ↳ Stablecoin TVL by blockchain | Stacked weekly TVL over 12 months of the same DeFi vaults, the 7 largest blockchains and Other; tokenised funds excluded | ✏️ Comment on the trend |
| 7.3 | ↳ Stablecoin NAV by tokenised fund | Stacked weekly NAV over 12 months, the 7 largest funds and Other; a fund on several chains under one name counts once | ✏️ Comment on the trend |
| 7.4 | ↳ Inflows and outflows | The **10 largest vault TVL increases and decreases over 30 days**, in dollars, then the same by blockchain: the net change of all vaults on each chain. A single change more than three times the next is drawn off scale | ✏️ Explain the largest moves |
| 8 | Partners | Copied from the previous post | — |
| 9 | Next steps | Copied from the previous post | — |

The average yield charts (sections 5.1 to 5.3) show:
- each vault as a small dot and the TVL-weighted average as a large dot;
- the US 3M T-bill as a dashed line;
- a right-hand column with the average, its difference to the T-bill in percentage points, and the TVL.

Outliers above 400% annualised return or 50% annualised volatility are left out of the averages. The x axis reaches the 90th percentile vault but at most twice the highest average; vault dots beyond it are not drawn but still count in the averages.

## Changes from the earlier outline

| Change | Reason |
|---|---|
| Correlation of returns removed | Hard to read, and little editorial value |
| Top movers replaced with inflows and outflows | Dollar TVL changes show where money moved. Rank moves were dominated by the gap between reports and by changes in the vault universe |
| Best-performing vaults split into lending, perp DEX by return, perp DEX by Sharpe and other | Each group has different risk and return, and a different benchmark |
| AMM pools ranked in their own subsection with at least $1M TVL | Their returns follow the prices of the pooled crypto assets |
| RWA vaults split from lending and other into their own subsection | Their risk is real-world borrowers and collateral, and readers look for them. The group depends on strategy tags, so untagged RWA vaults stay in lending or other until tagged |
| Best-performing large vaults folded into the split tables | The $100k threshold and the TVL column cover large vaults |
| Separate perp DEX section folded into 9.2 and 9.3 | Part of the split |
| Tokenised funds section added | Funds such as BlackRock BUIDL move the largest amounts, and readers compare them with DeFi yield |
| New vaults, vaults on each chain and tokenised funds made subsections of the best-performing vaults, new vaults first | All rankings sit under one heading with the same heading style |
| Average yield by protocol added; average yield by blockchain limited to the 10 largest chains | Comparable overviews of the largest markets |
| TVL by protocol split into DeFi vault protocols and tokenised fund NAV | Tokenised funds are a separate market, and their share classes dominated the protocol chart |
| Low-volatility performance chart removed | Covered by the lending section |
| Performance small multiples replaced with one shared chart per section | Equity curves can only be compared on the same axis |
| Stablecoin TVL by blockchain added | The same money as the protocol chart, by where it lives |
| Best-performing vaults first, then average yield, risk and return, and the TVL charts with inflows and outflows last | Readers come for the rankings; the market overview follows |
| Latest podcasts added before the data sections | Promotes the podcast episodes to report readers |
| Excluded vaults listed in a dated Markdown file and a PR comment, not in the post | Editors and reviewers see which vaults were left out and why, and the repository keeps the record; the post stays on the rankings |

## Decisions to confirm

1. **"By CAGR" in tables means the annualised one-month return**, the metric the website ranks by; the charts use the three-month return. For perp DEX vaults, both often hit the export's 10,000% cap.
2. **Protocol yields have two charts:** the 10 largest protocols by TVL (at least $1M), and the 10 highest-yielding protocols with at least $150k TVL. Small protocols with a few vaults can top the high yield chart; its TVL column shows how much money earns that yield.
3. **TVL changes include returns as well as deposits and redemptions.** The export has estimated net flows (`flow_value`) for only about 1,100 of 5,500 vaults, so dollar TVL changes are used. Identical listings, with the same name, chain and TVL change, are counted once, but separate share classes of one fund, such as the Janus Henderson Anemoy classes on Centrifuge, each appear.
4. **The lending and RWA classifications depend on strategy tags**, which not every protocol adapter sets, so lending also uses a curated protocol list (`LENDING_PROTOCOL_SLUGS`). Untagged RWA vaults, such as AlphaGrowth Base RWA in September 2026, stay in lending or other until tagged with the `categorise-vault-strategy` skill.
5. **Kept from the earlier outline:** TVL by protocol, now split into DeFi vault protocols and tokenised funds and joined by TVL by blockchain, risk and return, and the per-chain table. Drop any of them to shorten the post.
