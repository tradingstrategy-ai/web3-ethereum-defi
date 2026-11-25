# Comparing Lighter public pools

On 19 November 2026 we reviewed **349 public pools**, comprising 348 on Lighter Core and one on Lighter on Robinhood Chain. The historical window covers **13 October–11 November 2026 UTC**, the same 30 days as the [Hyperliquid review](hyperliquid-long-short-vaults.md). The [complete pool census](lighter-public-pool-census.md) records the identity, archive coverage, measurements and tag decision for each pool, including pools closed to new deposits.

The review adds tags to 42 Core pools. Edge & Hedge held both books throughout the native sample at 295% median gross exposure. dima's book spread 379% median gross exposure more widely and traded in clusters roughly every 24 hours. Peter Schiff's pool held 865% at the median, with most of its exposure in five positions. The 193 listed single-asset products retain their existing directional leverage classification. The three protocol liquidity pools retain their market-making classifications.

## Exposure and concentration

Gross exposure is the sum of the absolute long and short perpetual position values divided by the vault's equity. A snapshot counts as materially two-sided when each side reaches 5% of equity. We use the same 120% unlevered/leveraged threshold and the same inclusive 180–220% and 280–320% bands for 2x and 3x portfolios as the Hyperliquid review. These tags describe typical observed exposure. The 90th percentile in the census shows how far above the median a vault sometimes ran.

The portfolio exposure bands apply to multi-market allocations. NYP had 124.5% median gross exposure in a single BTC long and receives a directional trading tag. Concurrent books support a long-short portfolio classification when the mandate or portfolio evidence shows that they are held for relative performance. The strategy taxonomy pairs delta-neutral mandates with the long-short portfolio tag.

We used the last native account snapshot in each occupied four-hour UTC bucket. Each observation pairs position values with equity from the same response. We retained positive-equity observations, including flat ones. The market counts include flat observations. Top-five concentration uses only snapshots with open positions. The percentages below are medians except for the two-sided frequency. [Lighter account API](https://github.com/elliottech/lighter-python/blob/main/docs/AccountApi.md#account)

| Pool | Snapshots | Both sides ≥5% | Median long / short | Median gross | Long / short markets | Top-five share |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [Edge & Hedge (L/S Factors)](https://app.lighter.xyz/public-pools/281474976688087) | 172 | 100% | 157% / 139% | 294.6% | 26 / 28 | 29% |
| [dima systematic long/short bot](https://app.lighter.xyz/public-pools/281474976501540) | 172 | 100% | 193% / 186% | 379.0% | 33.5 / 29 | 14% |
| [FLEXIBLE POOL](https://app.lighter.xyz/public-pools/281474976651007) | 172 | 100% | 298% / 17% | 315.2% | 15 / 2 | 68% |
| [Centaur Strategies: Post Fiat](https://app.lighter.xyz/public-pools/281474976488848) | 172 | 100% | 82% / 94% | 210.1% | 5 / 5 | 69% |
| [\[ Systemic Strategies \] Peter Schiff's vault](https://app.lighter.xyz/public-pools/281474976622846) | 172 | 100% | 434% / 433% | 865.4% | 6 / 3 | 90% |
| [Silentist Public Frozen](https://app.lighter.xyz/public-pools/281474976639900) | 127 | 80% | 56% / 12% | 70.6% | 18 / 4 | 47% |
| [Insertive Capital (@onchainquant)](https://app.lighter.xyz/public-pools/281474976643895) | 154 | 78% | 656% / 138% | 789.0% | 5 / 5 | 91% |
| [Centaur Strategies: Black Bull](https://app.lighter.xyz/public-pools/281474976489165) | 172 | 37% | 66% / 0% | 66.5% | 6 / 0 | 97% |
| [pmalt](https://app.lighter.xyz/public-pools/281474976552918) | 172 | 8% | 68% / 2% | 69.7% | 1 / 1 | 100% |
| [Guinea Pool](https://app.lighter.xyz/public-pools/281474976694250) | 172 | 0% | 440% / 0% | 440.3% | 3 / 0 | 100% |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | 172 | 0% | 1% / 0% | 1.0% | 2 / 0 | 100% |
| [Steady Wealth Builder](https://app.lighter.xyz/public-pools/281474976552443) | 172 | 0% | 3% / 0% | 2.7% | 1 / 0 | 100% |
| [Lighter Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976710654) | 172 | 30% | 4% / 7% | 11.5% | 93 / 86 | 54% |
| [Lighter Robinhood Liquidity Provider (LLP)](https://robinhoodchain.lighter.xyz/public-pools/281474976710654) | 172 | 100% | 50% / 88% | 125.1% | 26 / 36 | 42% |

Centaur Post Fiat carried material long and short books throughout. Centaur Black Bull held both books above the threshold in 37% of native observations. Its median gross exposure was 66.5% and its 90th percentile was 134.7%, supporting an unlevered portfolio tag alongside directional trading. pmalt held both sides at some size in 545 of 720 archive hours, while both sides reached 5% of equity in just 8% of native snapshots. Its pair-trading tag describes a sleeve within a predominantly long book.

Silentist Public Frozen describes its Lighter pool as a smart-trader benchmark. Its books were materially two-sided in 80% of native observations. The relationship between those books remains unspecified, so the review adds only the unlevered portfolio tag. Insertive Capital's mandate describes discretion and asymmetric opportunities. Its median long and short exposures of 656% and 138% support directional trading alongside the leveraged portfolio tag. Guinea's operator allows trades in either direction and the observed book was long throughout, with median gross exposure above four times equity.

The archive position rows for the smaller systematic delta-neutral pool (281474976486477) reconcile to all 720 hourly account summaries. It held a median of 26 long and 20 short markets, with 20% median top-five concentration while invested. Historical account summaries supply notional and position counts. The pool had no matching native equity series for the window, so gross exposure as a fraction of equity remains unavailable.

Protocol liquidity and insurance pools take inventory as they quote and handle liquidations. Their two-sided balances alone provide insufficient evidence of a portfolio selected for relative performance. Both LLP deployments share account index 281474976710654. Their APIs and synthetic addresses are deployment-specific, and the Core and Robinhood observations remain distinct. [Lighter public pools](https://docs.lighter.xyz/trading/public-pools) and [0xArchive positions](https://docs.0xarchive.io/rest-api/positions)

## Executions and rebalance timing

0xArchive returned hourly account summaries and a position-change query for every listed pool. An hourly position was present in 28 pools. Position changes were observed in 26 pools, including liquidation records. The three protocol pools are outside the executed-order census. No open position or position change was returned for the 193 directional-leverage pools during the window. Each of these products has an operator-published single-asset leverage mandate. The archive observation covers perpetuals and the stated interval.

For pools with archived positions or changes, we requested detailed history, reconciled position counts and absolute notionals against the hourly summaries, and paginated the change log up to a 60,000-row cap per pool. Execution metrics use finalised ordinary fills with qualified continuity and order IDs. Liquidation, deleveraging and settlement legs are excluded. Empty ordinary-fill totals therefore include accounts whose sole recorded changes were liquidations. A capped or unqualified series has no execution metrics in the census. Edge & Hedge reached the cap on 17 October. Its cached native volume for the full 30 days was 90.8 million USDC.

As in the Hyperliquid review, we group partial fills by market and order ID. The first fill marks the executed order's start. More than 30 minutes between starts splits a burst. A burst qualifies when it includes at least two markets. We use the gaps between qualifying starts to measure cadence. A daily rebalance tag requires a median gap of 22–26 hours inclusive and enough qualifying starts to support a regular portfolio adjustment. Continuous execution, small counts and wide gap ranges weaken that interpretation.

| Pool | Fills/day | Orders/day | Bursts | Days with burst | Median gap | 10th–90th pct. gap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [dima systematic long/short bot](https://app.lighter.xyz/public-pools/281474976501540) | 22.4 | 21.3 | 29 | 28 | 24.0 h | 24.0–25.0 h |
| [systematc delta neutral long/short](https://app.lighter.xyz/public-pools/281474976486477) | 14.0 | 13.0 | 20 | 18 | 24.0 h | 16.1–67.4 h |
| [Centaur Strategies: Post Fiat](https://app.lighter.xyz/public-pools/281474976488848) | 3.8 | 2.3 | 10 | 8 | 29.3 h | 2.8–75.8 h |
| [FLEXIBLE POOL](https://app.lighter.xyz/public-pools/281474976651007) | 1.2 | 0.6 | 2 | 2 | 150.3 h | 150.3–150.3 h |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | 493.0 | 376.3 | 134 | 30 | 3.5 h | 1.4–11.3 h |
| [Steady Wealth Builder](https://app.lighter.xyz/public-pools/281474976552443) | 76.5 | 12.7 | 25 | 15 | 15.2 h | 2.6–51.1 h |
| [NK pool](https://app.lighter.xyz/public-pools/281474976684763) | 21.2 | 2.0 | 9 | 6 | 26.3 h | 2.1–215.7 h |

Both systematic delta-neutral pools show a daily median. The smaller account skipped more days, but 18 of its 20 starts fell at minute 41 of the hour between 11:41 and 13:41 UTC. The repeated minute supports a scheduled adjustment despite the wider interval range. Candle Effect v2 has far more bursts, which describe recurring execution across markets. Its separate reconstruction found predominantly long directional inventory with averaging and occasional short cycles. During our window both sides reached 5% of equity in none of its 172 native snapshots. Steady Wealth Builder's long-only grid held no concurrent short book. [Candle Effect research](https://github.com/tradingstrategy-ai/freqtrade-strategies/pull/967#issuecomment-6502886706) and [Steady Wealth Builder research](https://github.com/tradingstrategy-ai/freqtrade-strategies/pull/903#issuecomment-6196765222)

LIT ALPHA POOL's position history showed only long LIT. The available change history did not establish its advertised market-making mechanism, so it retains the perpetual futures default. Symphony, Majors vs Alts and other pools with explicit offsetting mandates can receive a structural strategy tag when the operator specifies the book construction. Their October–November archive may still be flat. The census records which decisions depend on the mandate.

## Share-price performance

Thirty-day returns use native daily share prices from 12 October through 11 November. Maximum drawdown measures the largest decline from a previous peak in that series. We use reported unit prices for investor performance because deposits and withdrawals change the number of shares. The calculations precede any operator fee assessed on withdrawal.

BTC beta uses the same 30 daily intervals. We match each native share-price timestamp with the preceding completed Lighter Core BTC hourly candle. BTC fell 9.9% on those clocks. Beta describes co-movement over the review window and can vary with the sample. [0xArchive candles](https://docs.0xarchive.io/rest-api/candles)

CAGR annualises the observed first-to-last daily share-price change using 365 days per year. The census reports it for at least 90 days of continuous positive prices through 11 November. Sharpe uses full weeks ending on Wednesday, with at least seven consecutive weekly returns, a zero risk-free rate and √(365/7) annualisation. The price date range in the census shows the available history for each pool. We supplement native responses with earlier prices from the local Lighter scanner cache. All 16,858 overlapping date and pool pairs agreed within one part in 100,000 or an absolute difference of 0.0000000001. Short histories and losses close to 100% can make annualised results unstable.

| Pool | 30-day return | Max drawdown | BTC beta | History begins | CAGR | Weekly Sharpe |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [Edge & Hedge (L/S Factors)](https://app.lighter.xyz/public-pools/281474976688087) | -1.6% | -7.0% | -0.19 | 2025-09-18 | +33.5% | 0.71 |
| [dima systematic long/short bot](https://app.lighter.xyz/public-pools/281474976501540) | -1.2% | -6.8% | -0.22 | 2026-08-11 | +19.7% | 1.84 |
| [FLEXIBLE POOL](https://app.lighter.xyz/public-pools/281474976651007) | -39.0% | -43.7% | +3.25 | 2025-10-15 | +22.5% | 0.71 |
| [Centaur Strategies: Post Fiat](https://app.lighter.xyz/public-pools/281474976488848) | -18.1% | -18.4% | +0.49 | 2026-09-11 | — | -10.31 |
| [\[ Systemic Strategies \] Peter Schiff's vault](https://app.lighter.xyz/public-pools/281474976622846) | -9.3% | -32.0% | -1.41 | 2026-03-05 | -84.4% | -1.80 |
| [Silentist Public Frozen](https://app.lighter.xyz/public-pools/281474976639900) | -8.1% | -8.2% | +0.30 | 2025-11-13 | -13.6% | -0.81 |
| [Insertive Capital (@onchainquant)](https://app.lighter.xyz/public-pools/281474976643895) | -99.8% | -99.8% | +12.55 | 2025-10-29 | -99.8% | -0.91 |
| [Centaur Strategies: Black Bull](https://app.lighter.xyz/public-pools/281474976489165) | -9.1% | -16.4% | +0.84 | 2026-09-10 | — | 0.40 |
| [pmalt](https://app.lighter.xyz/public-pools/281474976552918) | -12.4% | -12.9% | +0.42 | 2026-05-07 | -40.8% | -0.64 |
| [Guinea Pool](https://app.lighter.xyz/public-pools/281474976694250) | -97.1% | -97.1% | +4.84 | 2025-08-23 | -95.5% | -0.06 |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | +3.5% | -0.6% | +0.03 | 2026-05-21 | +46.2% | 7.10 |
| [Steady Wealth Builder](https://app.lighter.xyz/public-pools/281474976552443) | +6.8% | -8.2% | +0.08 | 2026-05-07 | +184.2% | 10.20 |
| [Lighter Liquidity Provider (LLP)](https://app.lighter.xyz/public-pools/281474976710654) | +0.8% | -0.0% | -0.02 | 2025-01-16 | +103.9% | 2.42 |
| [Lighter Robinhood Liquidity Provider (LLP)](https://robinhoodchain.lighter.xyz/public-pools/281474976710654) | +11.5% | -2.1% | -0.45 | 2026-06-28 | +787.6% | 6.09 |
| [systematc delta neutral long/short](https://app.lighter.xyz/public-pools/281474976486477) | -6.1% | -11.9% | +0.20 | 2026-09-18 | — | — |

## Realised execution cashflow

The table combines realised P&L and fees from the finalised ordinary-fill history with the signed changes returned by Lighter’s native positionFunding endpoint. The bounded queries returned 720 funding records for NYP, 1,415 for Candle Effect v2 and 2,999 for NK. Net cashflow sums those three components and excludes changes in open-position marks, liquidation legs and external transfers. It therefore measures booked cashflow within the stated scope. [0xArchive position changes](https://docs.0xarchive.io/rest-api/positions) and [Lighter position funding API](https://github.com/elliottech/lighter-python/blob/main/docs/AccountApi.md#position_funding)

| Pool | Gross realised P&L (USDC) | Execution fees (USDC) | Native funding change (USDC) | Realised net of fees plus funding (USDC) |
| --- | ---: | ---: | ---: | ---: |
| [NYP- Not YOUR pool](https://app.lighter.xyz/public-pools/281474976624925) | +0.00 | 14.64 | -7,014.81 | -7,029.45 |
| [Candle Effect v2](https://app.lighter.xyz/public-pools/281474976543116) | +3,656.51 | 333.03 | -24.12 | +3,299.36 |
| [NK pool](https://app.lighter.xyz/public-pools/281474976684763) | -4,255.73 | 39.29 | -276.80 | -4,571.81 |

## Sources and production refresh

The census uses deployment-local publicPoolsMetadata pages with filter=all, public account responses by index and the local read-only lighter-pools.duckdb snapshot. Historical 0xArchive requests used account/history, positions/history and positions/changes under the Core or Robinhood account namespace with a fixed inclusive start and exclusive end. The finalisation watermark extended through 16 November at acquisition. The native and archive position sources sample on different clocks. The archive corroborates scope and direction and the native matched-clock observations supply the equity ratios. The available evidence covers open holdings and executed orders. Unfilled orders, undisclosed automation and trading intent may remain unobserved. [Lighter SDK](https://github.com/elliottech/lighter-python) and [0xArchive position coverage](https://docs.0xarchive.io/rest-api/positions)

The source qualification and accounting follow the Lighter, 0xArchive and vault reverse-engineering notes in the companion freqtrade-strategies repository. [Lighter source notes](https://github.com/tradingstrategy-ai/freqtrade-strategies/blob/master/.claude/docs/lighter.md), [0xArchive source notes](https://github.com/tradingstrategy-ai/freqtrade-strategies/blob/master/.claude/docs/0xarchive.md) and [vault research method](https://github.com/tradingstrategy-ai/freqtrade-strategies/blob/master/.claude/docs/vault-reverse-engineer.md)

The census records venue status and TVL from 19 November. TVL and native volume are denominated in USDC on Core and USDG on Robinhood. Exposure, concentration, returns and maker share are percentages. The archive columns count qualified hours, including hours with any open position and hours with both sides at any size. The native columns use the four-hour sample, including the fraction of snapshots meeting the 5% materiality threshold on both sides. An em dash indicates that a value is unavailable. The current exposure fields come from the point-in-time 19 November account response.

The [maintained strategy registry](../../eth_defi/lighter/tags.py) uses synthetic addresses. [Migration 092](../../migration/scripts/092-lighter-public-pool-strategies.py) refreshes the persisted tags for the 42 changed Core pools. Follow the [production migration runbook](../../.claude/docs/migration-scripts.md) after merge, with the scanner and competing writers stopped before preview and apply.
