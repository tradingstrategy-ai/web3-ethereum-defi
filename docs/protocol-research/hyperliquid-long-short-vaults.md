# Comparing Hyperliquid long–short vaults

On 19 November 2026 we reviewed ten Hyperliquid vaults that held long and short positions over **13 October–11 November 2026 UTC**. Two qualified for the **diversified low-exposure long–short portfolio** tag in addition to the broader **long-short portfolio** tag. Five received the **leveraged portfolio** tag for median gross exposure above 120% of equity. The other five received the **unlevered portfolio** tag for falling below that threshold. HyperNeutral #1 spread 69% of equity across its two books at the median. Quantitative Market Neutral Strategy spread 37% across even more markets. Both had smaller drawdowns than the other eight during this window, though their returns differed.

## Portfolio construction

Each vault had 173 complete position snapshots with positive account equity. **Gross exposure** adds the absolute values of longs and shorts and divides by vault equity. **Top-five share** is the percentage of gross exposure held in the five largest positions. Exposure, concentration and market counts in this table are medians.

| Vault | Long / short markets | Gross / equity | Top-five share |
| --- | ---: | ---: | ---: |
| [HyperNeutral #1](https://app.hyperliquid.xyz/vaults/0x8bb033130be354eed1110ce7228d7095f001d9fe) | 49 / 57 | 69% | 28% |
| [Quantitative Market Neutral Strategy](https://app.hyperliquid.xyz/vaults/0x14ecb3f4932522b8d508fa6e237e2746a4999feb) | 74 / 94 | 37% | 14% |
| [Symphony](https://app.hyperliquid.xyz/vaults/0x9114a5161f18739ced233190861ea275c8b3a99b) | 48 / 92 | 79% | 49% |
| [Abs Return Sophon Quant](https://app.hyperliquid.xyz/vaults/0x35d92bd6571d0bdc5413250b28692e6be905d67b) | 9 / 16 | 41% | 57% |
| [Edge & Hedge](https://app.hyperliquid.xyz/vaults/0x060d01aa996003b3731a992462d7f0ba68bf3b04) | 26 / 28 | 299% | 29% |
| [AceVault Hyper01](https://app.hyperliquid.xyz/vaults/0x9e02aca9865e1859bb7865f6f64801e804a173df) | 8 / 58 | 202% | 59% |
| [Orbit Value Strategies](https://app.hyperliquid.xyz/vaults/0x115849ce84370f25cadcf0d348510d73837e1aa5) | 2 / 8 | 102% | 84% |
| [\[ Systemic Strategies \] L/S Grids](https://app.hyperliquid.xyz/vaults/0x07fd993f0fa3a185f7207adccd29f7a87404689d) | 18 / 42 | 290% | 34% |
| [Long HYPE & BTC \| Short Garbage](https://app.hyperliquid.xyz/vaults/0xac26cf5f3c46b5e102048c65b977d2551b72a9c7) | 2 / 36 | 216% | 89% |
| [Buyback Spread Engine](https://app.hyperliquid.xyz/vaults/0xce71894b42f5c31a6ae6ca4fb77441c68ab15086) | 5 / 5 | 391% | 51% |

## Strategy classification

All ten vaults carry the long-short portfolio and algorithmic trading tags. We assigned the diversified low-exposure subtype when gross exposure usually stayed below equity and the largest holdings accounted for a modest share of the portfolio. The leveraged portfolio tag applies above 120% median gross exposure and the unlevered portfolio tag applies below 120%. Each snapshot adds the absolute notionals of all positions before dividing by equity. A median of exactly 120% qualifies for neither tag. A leveraged portfolio also receives the **2x portfolio** tag for unrounded median gross exposure of 180% to 220% of equity, or the **3x portfolio** tag for 280% to 320%. Both ranges include their endpoints. At 200% gross exposure, each $100 of equity supports $200 of combined long and short positions. Individual vaults can also carry tags such as grid trading.

| Vault | Additional portfolio tags and evidence |
| --- | --- |
| HyperNeutral #1 | Daily rebalance. Unlevered (69% median gross exposure) and diversified low-exposure. Positions were spread across both books. Even at the 90th percentile, gross exposure was 76% of equity. |
| Quantitative Market Neutral Strategy | Daily rebalance. Unlevered (37% median gross exposure) and diversified low-exposure. The five largest positions carried the smallest share in the group. Gross exposure reached 41% of equity at the 90th percentile. |
| Symphony | Daily rebalance. Unlevered (79% median gross exposure). Despite the number of markets, five positions carried nearly half the exposure. |
| Abs Return Sophon Quant | Daily rebalance. Unlevered (41% median gross exposure). Five positions carried more than half that exposure. |
| Edge & Hedge | Leveraged portfolio and 3x portfolio. The two books were diversified, with combined exposure of 299% of equity at the median. |
| AceVault Hyper01 | Leveraged portfolio and 2x portfolio. Median gross exposure was 202% of equity. Five positions accounted for most of that exposure. |
| Orbit Value Strategies | Unlevered. Gross exposure reached 140% of equity at the 90th percentile, but its median was 102%. Five positions dominated the portfolio. |
| \[ Systemic Strategies \] L/S Grids | Leveraged portfolio and 3x portfolio. The grid held both sides with median gross exposure of 290% of equity. |
| Long HYPE & BTC \| Short Garbage | Leveraged portfolio and 2x portfolio. Median gross exposure was 216% of equity. Two long markets and large individual positions gave it the highest concentration in the group. |
| Buyback Spread Engine | Leveraged portfolio. Five markets on each side produced median gross exposure of 391% of equity. |

## Trading activity

**Trades per day** gives average fill count and executed order count across all 30 UTC calendar days, including idle days. One order can fill several times. We group partial fills by venue, market and order ID, then use the first fill as the order’s start.

| Vault | Fills/day | Orders/day |
| --- | ---: | ---: |
| HyperNeutral #1 | 89.5 | 54.8 |
| Quantitative Market Neutral Strategy | 1,052.3 | 672.7 |
| Symphony | 488.5 | 170.0 |
| Abs Return Sophon Quant | 51.0 | 25.8 |
| Edge & Hedge | 1,146.1 | 435.2 |
| AceVault Hyper01 | 2,393.3 | 1,446.2 |
| Orbit Value Strategies | 73.1 | 6.2 |
| \[ Systemic Strategies \] L/S Grids | 4,531.8 | 696.0 |
| Long HYPE & BTC \| Short Garbage | 64.6 | 18.6 |
| Buyback Spread Engine | 56.4 | 16.7 |

## Rebalance timing

**Rebalance frequency and regularity** use multi-market trading bursts as a proxy for portfolio adjustments. A new burst starts after more than 30 minutes between consecutive order starts. We count bursts with orders in at least two markets and measure the hours between consecutive qualifying starts. The counts cover 30 UTC days. A day counts once if at least one qualifying burst began that day. The median gap gives the typical time between burst starts. Assign **daily rebalance** when the unrounded median gap is 22 to 26 hours inclusive. A qualifying vault can skip days or rebalance several times in one day. The 10th–90th percentile gap shows the middle 80% of those intervals. A narrow interval indicates more regular spacing during this window. The percentile estimates are sensitive to individual gaps, particularly for Orbit (9 gaps), Edge & Hedge (10), Quantitative Market Neutral Strategy (14) and Long HYPE & BTC / Short Garbage (15).

| Vault | Rebalance bursts | Days with a burst start (of 30) | Median gap | 10th–90th percentile gap |
| --- | ---: | ---: | ---: | ---: |
| HyperNeutral #1 | 31 | 30 | 24.0 h | 23.4–24.0 h |
| Quantitative Market Neutral Strategy | 15 | 15 | 24.0 h | 24.0–64.8 h |
| Symphony | 30 | 30 | 24.0 h | 24.0–24.0 h |
| Abs Return Sophon Quant | 30 | 27 | 24.0 h | 7.2–27.8 h |
| Edge & Hedge | 11 | 11 | 61.5 h | 46.7–75.2 h |
| AceVault Hyper01 | 99 | 24 | 3.1 h | 1.4–18.3 h |
| Orbit Value Strategies | 10 | 9 | 61.5 h | 19.9–103.7 h |
| \[ Systemic Strategies \] L/S Grids | 91 | 24 | 2.9 h | 1.0–23.0 h |
| Long HYPE & BTC \| Short Garbage | 16 | 11 | 36.4 h | 4.9–108.7 h |
| Buyback Spread Engine | 33 | 18 | 11.4 h | 4.2–48.8 h |

Symphony had one qualifying burst on each of the 30 days. HyperNeutral had 31 across the same 30 days. Sophon also had a median gap of 24 hours, though its bursts started on 27 days. Quantitative Market Neutral Strategy averaged 672.7 executed orders per day against HyperNeutral’s 54.8, yet its qualifying bursts started on only 15 days. Its median gap was also 24 hours, but its 90th-percentile gap reached 65 hours.

Edge & Hedge and Orbit both had median gaps of 61.5 hours, about two and a half days, and received the **Two and half days rebalance** tag. Orbit had more variable timing, with a middle 80% range of 19.9–103.7 hours compared with 46.7–75.2 for Edge & Hedge. Long HYPE & BTC / Short Garbage had the widest range in the group, spanning 4.9–108.7 hours. Buyback Spread Engine had 33 bursts on 18 days, so its above-daily average count included clustered trading.

For AceVault and Systemic Strategies, orders started in 696 and 710 of the 720 hours respectively. With so little silence, the 30-minute rule can merge several portfolio adjustments into one burst. Their counts and start-to-start gaps reflect interruptions in near-continuous trading. The underlying portfolio review frequency remains unknown.

## Returns and drawdowns

Return and maximum peak-to-trough drawdown use 30 daily returns from the 12 October closing share price through the 11 November close.

| Vault | 30-day return | Max drawdown |
| --- | ---: | ---: |
| HyperNeutral #1 | +5.0% | −2.0% |
| Quantitative Market Neutral Strategy | +0.4% | −0.9% |
| Symphony | −3.0% | −5.5% |
| Abs Return Sophon Quant | −2.2% | −3.3% |
| Edge & Hedge | −0.6% | −7.9% |
| AceVault Hyper01 | +9.7% | −8.0% |
| Orbit Value Strategies | +10.9% | −4.7% |
| \[ Systemic Strategies \] L/S Grids | +15.4% | −9.4% |
| Long HYPE & BTC \| Short Garbage | +5.4% | −13.1% |
| Buyback Spread Engine | +8.6% | −8.9% |

## All-time performance

All-time means from the first archived observation with at least $1,000 in vault equity through the last observation on **11 November 2026 UTC**, the same end date as the 30-day comparison. The cleaned Hypercore performance index divides each change in cumulative account P&L by the larger of opening and closing equity, with a $1 floor. It compounds those returns using one checkpoint per four-hour UTC bucket. Deposits and withdrawals affect equity but contribute no P&L. The resulting returns are estimates because the archive cannot establish the timing of every flow between checkpoints. [Hypercore performance index](../../eth_defi/research/wrangle_vault_prices.py)

CAGR annualises the first-to-last index change using 365 calendar days per year. Sharpe uses the last available observation in each week ending on Wednesday, annualised with √(365/7) and a zero risk-free rate. Weekly sampling reflects the coarser early history. Each vault had 26 to 93 weekly returns with no missing weeks.

| Vault | History starts (UTC) | All-time CAGR | All-time Sharpe |
| --- | --- | ---: | ---: |
| HyperNeutral #1 | 2025-07-02 | +22.88% | 1.56 |
| Quantitative Market Neutral Strategy | 2026-04-15 | +18.26% | 1.95 |
| Symphony | 2025-09-03 | +23.31% | 1.07 |
| Abs Return Sophon Quant | 2026-05-11 | −11.80% | −1.61 |
| Edge & Hedge | 2025-12-10 | +4.90% | 0.31 |
| AceVault Hyper01 | 2025-08-20 | +27.67% | 0.91 |
| Orbit Value Strategies | 2025-12-17 | +1.79% | 0.19 |
| \[ Systemic Strategies \] L/S Grids | 2025-01-29 | +48.71% | 1.05 |
| Long HYPE & BTC \| Short Garbage | 2025-04-09 | +79.81% | 1.06 |
| Buyback Spread Engine | 2025-08-13 | +157.74% | 1.40 |

Quantitative Market Neutral Strategy and HyperNeutral had the two highest Sharpe estimates. Buyback had the highest CAGR, with some large percentage gains on very small balances. In the week ending 15 October 2025, about $78 of P&L on closing equity of about $165 contributed a 47% gain to its index.

## Strategy tags by vault

These are the strategy category labels from the Hyperliquid tag registry, including the native perpetual futures tag. The exposure and rebalance labels reflect the 30-day review window.

| Vault | Strategy tags |
| --- | --- |
| HyperNeutral #1 | Algorithmic trading, Daily rebalance, Diversified low-exposure long–short portfolio, Long-short portfolio, Perpetual futures, Unlevered portfolio |
| Quantitative Market Neutral Strategy | Algorithmic trading, Daily rebalance, Diversified low-exposure long–short portfolio, Long-short portfolio, Perpetual futures, Unlevered portfolio |
| Symphony | Algorithmic trading, Daily rebalance, Long-short portfolio, Perpetual futures, Unlevered portfolio |
| Abs Return Sophon Quant | Algorithmic trading, Daily rebalance, Long-short portfolio, Perpetual futures, Unlevered portfolio |
| Edge & Hedge | 3x portfolio, Algorithmic trading, Leveraged portfolio, Long-short portfolio, Perpetual futures, Two and half days rebalance |
| AceVault Hyper01 | 2x portfolio, Algorithmic trading, Leveraged portfolio, Long-short portfolio, Perpetual futures |
| Orbit Value Strategies | Algorithmic trading, Long-short portfolio, Perpetual futures, Two and half days rebalance, Unlevered portfolio |
| \[ Systemic Strategies \] L/S Grids | 3x portfolio, Algorithmic trading, Directional trading, Grid trading, High-frequency, high-win-rate trading, Leveraged portfolio, Long-short portfolio, Perpetual futures |
| Long HYPE & BTC \| Short Garbage | 2x portfolio, Algorithmic trading, Leveraged portfolio, Long-short portfolio, Perpetual futures |
| Buyback Spread Engine | Algorithmic trading, Leveraged portfolio, Long-short portfolio, Perpetual futures |

## Why the returns differed

0xArchive fills and funding records, together with opening and closing position values, let us separate price moves on each side from fees and funding. Figures below are rounded USDC from the first available account snapshot on 13 October through the last on 11 November. Positive funding is received and negative funding is paid. Fees are shown as costs and deducted in the after-cost P&L.

| Vault | Long price P&L | Short price P&L |
| --- | ---: | ---: |
| HyperNeutral #1 | −3,075 | +7,793 |
| Quantitative Market Neutral Strategy | −41,388 | +44,989 |
| Abs Return Sophon Quant | −6,745 | +3,815 |

Subtract trading fees from the two price contributions and add net funding to obtain the trading P&L after costs.

| Vault | Trading fees | Net funding | P&L after costs |
| --- | ---: | ---: | ---: |
| HyperNeutral #1 | 81 | −310 | +4,327 |
| Quantitative Market Neutral Strategy | 1,201 | −2,485 | −84 |
| Abs Return Sophon Quant | 415 | +130 | −3,215 |

HyperNeutral’s short positions gained enough to cover losses on its longs, trading fees and funding. Quantitative Market Neutral Strategy earned about 3,602 USDC from the difference between its two books, but paid about 3,686 USDC in fees and funding. Sophon lost money before costs because gains on its shorts covered only part of the losses on its longs.

Account-level trading P&L depends on how much capital was invested when each gain or loss occurred. Share-price returns follow one unit of capital through the period. Quantitative Market Neutral Strategy started with about 990,000 USDC of equity and recorded 293,243 USDC of deposits and 615,826 USDC of net withdrawal proceeds. Its −84 USDC trading contribution and +0.4% share-price return therefore measure different outcomes. After those flows, the cash reconciliation leaves −131 USDC unexplained, about 0.01% of opening equity. HyperNeutral’s corresponding residual was −329 USDC.

## Research record and open questions

- **Equity curves and positions reviewed.** The first pass recorded simultaneous long and short exposure, drawdown and sensitivity to bitcoin. The second pass compared gross exposure and position concentration, including the cases of Symphony and Sophon. [Equity-curve comparison](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1794#issuecomment-7010021175) · [Position comparison and attribution](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1794#issuecomment-7010734163)
- **Archive data downloaded.** The working data are under `~/code/freqtrade-strategies/data/hyperliquid-ls-vault-pilot/`. Use `snapshot-risk.csv` for position sizing, `daily-share-prices.csv` for the equity curves, `attribution.csv` for fills, fees and funding, and `cash-bridge.csv` for cash reconciliation. To check the snapshot counts in `~/.tradingstrategy/vaults/hyperliquid-vaults-hf.duckdb`, count the dated rows per vault in `perp_vault_account_observations` with `position_set_complete` and positive `total_equity`. Join positions on `snapshot_id`, rank their absolute notionals within each snapshot and take the median of the resulting top-five shares. [High-frequency archive guide](../../scripts/hyperliquid/README-hyperliquid-vaults-high-frequency.md)
- **Lifetime performance measured.** Read `vault_high_freq_prices` from the same DuckDB file for the ten addresses in `attribution.csv` up to 00:00 UTC on 12 November 2026, excluding the boundary. Use `tvl` as total assets, then apply `discard_hypercore_initial_low_tvl_history()` and `approximate_hypercore_share_prices_from_pnl_nav()`. Pass the first and last cleaned values and the difference between their UTC dates to `compute_cagr()`. Resample the cleaned series to the last observation in each week ending on Wednesday and pass its percentage changes to `compute_sharpe(annualisation_factor=365 / 7)`. [Performance metric functions](../../eth_defi/research/perf_metrics.py)
- **Execution cadence measured.** The trading activity and rebalance timing tables use finalised fills from the archived core and HIP-3 `positions/changes` pages. Use the `*-positions-changes-*.json` receipts under the pilot directory to locate the compressed pages. Count timestamps from 00:00 UTC on 13 October up to 00:00 UTC on 12 November, excluding the end. The P&L reconstruction uses the slightly shorter window bounded by account snapshots.
- **Classification implemented.** PR #1794 adds the broader tag to nine vaults alongside the previously tagged HyperNeutral and assigns the narrower tag to two. Migrations 083 and 084 refresh those stored tags. Migrations 085 and 086 refresh the five reviewed deployments above and five below the leverage threshold, respectively. Migration 087 refreshes the four daily rebalance assignments. Migrations 088 and 089 refresh the two 3x and two 2x portfolio assignments, respectively. Migration 090 refreshes the two assignments for rebalancing about every two and a half days. Migration 091 refreshes the algorithmic trading classification across all ten vaults. [Strategy definitions](../../eth_defi/vault/strategy_tag.py) · [Hyperliquid vault mappings](../../eth_defi/hyperliquid/tags.py) · [PR and rollout status](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1794)
- **Open questions.** Repeat the exposure and concentration comparison in a different market period to see whether the same vaults qualify. Resolve the cash residuals and the ending inventory mismatch for Systemic Strategies before attributing its full return. The archive shows the managers’ trades, while their private selection and sizing rules remain unknown. The companion research documents the observed rebalancing and remaining uncertainty. [HyperNeutral reverse-engineering notes](https://github.com/tradingstrategy-ai/freqtrade-strategies/blob/d9ac219e5971340140ff9f8e18f7d67364b4b347/.claude/docs/hyperneutral.md) · [Historical exposure study](https://github.com/tradingstrategy-ai/freqtrade-strategies/blob/d9ac219e5971340140ff9f8e18f7d67364b4b347/notebooks/hyperneutral/03-FINDINGS.md)
