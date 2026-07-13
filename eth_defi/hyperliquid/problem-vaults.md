# Problem vaults

This document records known Hyperliquid / Hypercore vault price-history
problems investigated in July 2026. It is an operational reference for the
native-vault scanners and the downstream wrangle step, not an assessment of a
vault's investment quality.

## Data model and failure mode

Hyperliquid's `vaultDetails` endpoint exposes rolling `day`, `week`, `month`,
and `allTime` account-value and cumulative-PnL series. The native scanner
derives an ERC-4626-like share price from those series so that vault returns
can be compared with other vault protocols.

The account-value and PnL samples in different rolling windows are not always
aligned. Combining a fresh account-value sample with a stale or differently
resolved cumulative-PnL sample makes the inferred net flow wrong. The share
price reconstruction then invents a share mint or burn, producing a temporary
price spike which often reverses at the next refreshed observation. This is a
data artefact, not a realised vault return.

The wrangle repair in
`eth_defi.research.wrangle_vault_prices.fix_hypercore_source_overlap_share_prices`
addresses this case:

- The daily and high-frequency exporters label every new row as `daily` or
  `hf` through `hypercore_source`.
- Positive-price, positive-NAV HF observations are the preferred canonical
  anchors. A daily point bracketed by them becomes a candidate if its symmetric
  price deviation exceeds 50%.
- For legacy daily-only vaults, the newest common `written_at` batch supplies
  the canonical anchors.
- Both paths repair a candidate only when NAV stays within 50% of the
  interpolated anchor NAV, the anchors are at most eight days apart, and their
  interval contains neither zero NAV nor an `epoch_reset`. These checks prevent
  smoothing a genuine deposit, missing observation, wipe-out, or
  recapitalisation boundary.
- The input value is retained in `raw_share_price` for auditability. The repair
  does not extrapolate outside anchor coverage and does not alter anchor rows.
  `hypercore_repair_status` records `repaired_*` for applied changes and a
  `deferred_*` reason when the evidence is insufficient and the raw value is
  kept.
- A separate recapitalisation filter discards the old cleaned-history epoch
  after a durable zero-NAV event. Durability is measured from zero NAV to the
  first later positive NAV; that recovery must take at least seven days. The
  new epoch starts at the first subsequent observation of at least `$1,000`
  and is marked `epoch_reset`. The raw parquet is never discarded or rewritten.

Legacy parquet rows predate explicit source provenance. They are classified as
daily when their timestamp is midnight UTC and HF otherwise. This is valid for
the historical Hypercore exports, but new data must always use the explicit
column.

## Crypto Plaza Relative Momentum Edge

- Address: `0xdc9955a83218b71713a83ee072055591bd4c7304`
- Trading Strategy page:
  [Crypto Plaza Relative Momentum Edge](https://tradingstrategy.ai/trading-view/vaults/crypto-plaza-relative-momentum-edge)
- Affected period: February 2026

This was the original reported example. The large February share-price jump
and reversal were caused by the rolling-window merge described above, not by
the vault's positions or a real trading gain/loss. The raw values on 5 and 6
February were respectively `3.538529` and `3.485236`; the HF-anchor repair
estimates `1.079443` and `1.029948`.

The repair changes ten historical rows for this vault. Its maximum absolute
return in the late-January to mid-February inspection window falls to about
16.5% after repair.

## Magixbox

- Address: `0x1764dd740aba4195643bbb6a44648e0306b00cfa`
- Trading Strategy page: [Magixbox](https://tradingstrategy.ai/trading-view/vaults/magixbox)
- Affected period: 25 January to 5 February 2026

Magixbox has six daily/HF price-discrepancy candidates. The conservative
policy automatically repairs two. Four remain unchanged because their NAV is
more than 50% away from the interpolated HF-anchor NAV; although their prices
look suspicious, the stored observations do not prove that interpolation is
economically safe.

| Date | Raw share price | Anchor estimate | Decision |
| --- | ---: | ---: | --- |
| 2026-01-25 | 0.443179 | 0.668308 | Repair |
| 2026-01-30 | 1.278832 | 0.642874 | Defer: NAV |
| 2026-01-31 | 1.774786 | 0.724207 | Defer: NAV |
| 2026-02-01 | 4.605958 | 0.815830 | Defer: NAV |
| 2026-02-03 | 12.898591 | 1.035319 | Repair |
| 2026-02-05 | 26.035410 | 1.303902 | Defer: NAV |

The four deferred values retain `share_price == raw_share_price` and carry
`deferred_hf_nav`; downstream consumers can therefore distinguish unresolved
data from repaired data. The separate large October 2025 move is supported by
roughly `$12,742.80` of realised PnL on 10 October and must not be removed as a
data artefact.

At the 13 July 2026 live API check, Magixbox had no open perpetual positions
and a perp account value of about `$746.22`.

## C.A.T

- Address: `0xdfb729b4b789de6d13d6ad7ac8e2750909360af9`
- Trading Strategy page: [C.A.T](https://tradingstrategy.ai/trading-view/vaults/c-a-t)
- Affected period: 22 January to 6 February 2026

C.A.T has no HF history for the affected period, so this case motivated the
refreshed-daily-anchor fallback. The canonical weekly observations were about
`1.019542` on 28 January, `1.893065` on 4 February, and `1.961532` on 11
February. In contrast, stale daily values rose to `19.672888` on 3 February
and to `36.320766` on 6 February before reversing near `1.90`.

This cannot be an economic return: NAV remained around `$5,000`, and the
locally synchronised vault ledger records no deposits from 29 January through
10 February. The repair changes fourteen rows. It reduces the maximum
one-step return from about 1,814% to 58.1%; for example, the raw values
`36.239740` and `36.320766` on 5 and 6 February become `1.902698` and
`1.912379`.

At the 13 July 2026 live API check, C.A.T had no open perpetual positions and
a perp account value of about `$2,277.49`.

## Similar price-history candidates

The repair now records its candidate classification directly in
`hypercore_repair_status`. A value beginning with `repaired_` means the cleaned
price differs from `raw_share_price`; `deferred_` means the row exceeded the
price-discrepancy threshold but failed one or more safety rules and remains
unchanged. A large price discrepancy is a reason to inspect a row, not by
itself proof that the raw value is wrong.

The read-only production snapshot contained two non-overlapping populations:

| Pattern | Anchor method | Vaults | Candidates | Repaired | Deferred |
| --- | --- | ---: | ---: | ---: | ---: |
| Magixbox-style | HF observations | 143 | 759 | 612 | 147 |
| C.A.T-style | Refreshed daily observations | 38 | 292 | 135 | 157 |
| Total |  | 181 | 1,051 | 747 | 304 |

Of the 181 affected vaults, all candidates are automatically repaired for 97;
84 have at least one deferred row. The latter group requires better source
data or manual investigation rather than more aggressive interpolation.

The safeguards defer 260 rows for NAV inconsistency, 60 for an anchor gap over
eight days, and 34 for crossing zero NAV or `epoch_reset`; these counts overlap.
For example, all 28 deferred rows for BBQ & Tegelwerken BV span fourteen-day
anchor gaps. Lifecycle-boundary deferrals include 21M, SMM Foundation, and
Vela Trading. These are real limitations in the evidence available to the
repair, not proof of genuine investment returns.

### Magixbox-style examples

These are the largest per-vault symmetric discrepancies in the HF-anchor
population. A ratio of 20 means that one of the two prices was twenty times
the other.

| Vault | Address | Candidates | Repaired | Deferred | Largest ratio |
| --- | --- | ---: | ---: | ---: | ---: |
| Tao Hedge | `0x3d848183c406deae93c2641c045a541cf1d4a6cb` | 8 | 5 | 3 | 587.6x |
| HLP Liquidator 3 | `0x5e177e5e39c0f4e421f5865a6d8beed8d921cb70` | 2 | 0 | 2 | 164.6x |
| Hyperland | `0x8e108f7c619ab460885edd0f84e313daf119c779` | 12 | 11 | 1 | 94.8x |
| Long LINK Short XRP | `0x73ce82fb75868af2a687e9889fcf058dd1cf8ce9` | 12 | 10 | 2 | 49.2x |
| Magixbox | `0x1764dd740aba4195643bbb6a44648e0306b00cfa` | 6 | 2 | 4 | 20.0x |
| TAPTRADE | `0x5f42236dfb81cba77bf34698b2242826659d1275` | 45 | 26 | 19 | 19.3x |

### C.A.T-style examples

These vaults lack sufficient HF history for the affected interval. Their
latest daily refresh batch supplies anchors, but each candidate still has to
pass all three safety checks.

| Vault | Address | Candidates | Repaired | Deferred | Largest ratio |
| --- | --- | ---: | ---: | ---: | ---: |
| Automated AI trading vault | `0x87fdbcf7c8fc949e2ddb4f1a50337ee75dc8233a` | 10 | 6 | 4 | 35.5x |
| PUMP TRADE | `0xafc7b17e3d7b564fcbd1b5220455f16a66857ef0` | 29 | 23 | 6 | 22.8x |
| C.A.T | `0xdfb729b4b789de6d13d6ad7ac8e2750909360af9` | 14 | 14 | 0 | 19.0x |
| TA trader | `0x6fe9749eab7f66f002d7541d6c1e3bb3df19c701` | 22 | 17 | 5 | 10.2x |
| MC Recovery Fund | `0x914434e8a235cb608a94a5f70ab8c40927152a24` | 8 | 8 | 0 | 6.3x |
| $100K Target | `0xe528531fc82ab104397fe06c550f0bb00720e0ab` | 14 | 6 | 8 | 4.9x |

For any future scan, obtain the complete inventory by filtering cleaned
Hypercore rows for non-empty `hypercore_repair_status`, then grouping by
`address` and status. Filtering for `share_price != raw_share_price` returns
only the applied subset and would omit deliberately deferred candidates.

## HODL My Perps

- Address: `0x13b43faa22d854bf43b4e7581c1b3dfcd416f8c3`
- Trading Strategy page:
  [HODL My Perps](https://tradingstrategy.ai/trading-view/vaults/hodl-my-perps)
- Lifecycle boundary: 15 October 2025 to 17 April 2026

HODL My Perps is not a daily/HF overlap problem. Its all-time API history
shows a real wipe-out on 15 October 2025: NAV became zero and cumulative PnL
was `-$326,631.13`. NAV remained zero until recapitalisation began on 17 April
2026.

The post-recapitalisation synthetic share price is not economically continuous
with the pre-wipe-out shares. It begins near `6.55e-7` for a roughly `$100`
deposit and later contains non-economic HF jumps, including a roughly 54.7x
move on 21 May while NAV was near `$31,700`. These values arise because the
available rolling PnL history begins after a large historical loss, so the
scanner cannot know the original share base.

Do **not** repair HODL by interpolation: that would hide the real complete
loss. The wrangle pipeline applies a separate recapitalisation policy:

1. Detect a prior NAV of at least `$1,000`, zero NAV, and no later positive NAV
   for at least seven days.
2. After that durable recovery, wait until NAV again reaches `$1,000`, discard
   all preceding rows from the **cleaned** data, and mark the first retained
   row `epoch_reset`.
3. Preserve the raw parquet for audit and historical analysis.

This scopes the cleaned chart and metrics to the current capital epoch. It does
not by itself reconstruct any remaining post-recapitalisation synthetic-price
anomaly. The preceding -100% outcome remains available in raw data, but it is
not chain-linked into a fictitious return on recapitalised capital.

At the 13 July 2026 live API check, the vault held a long position of `830.3`
HYPE, entered at `64.8714`, with unrealised PnL of about `-$945.21` and a
liquidation price of `61.4724294668`.

## Durable zero-NAV and recapitalisation episodes

The raw Hypercore history contains many isolated zero-NAV observations caused
by scanner or rolling-window artefacts. They must not automatically become
performance epochs. The exploratory census used NAV above `$10`, followed by
NAV at or below `$0.000001`, then later NAV above `$10`, with at least one day
before recovery. The implemented automatic rule is deliberately stricter: the
old and new tracked epochs must reach `$1,000`, and the zero period must last
at least seven days before *any* positive NAV reappears.

There are four such episodes across 569 tracked Hypercore vaults. All four
also satisfy a seven-day recovery-delay threshold; HODL My Perps and Rehobot
LR exceed thirty days.

| Vault | Address | Zero interval | New tracked epoch | Rows discarded |
| --- | --- | --- | --- | ---: |
| Rehobot LR | `0x81a20870c5c7558f117166b2a598abaa8ce91f50` | 15 Oct 2025 – 15 Apr 2026 | 22 Apr 2026, about `$1,012` | 44 |
| HODL My Perps | `0x13b43faa22d854bf43b4e7581c1b3dfcd416f8c3` | 15 Oct 2025 – 17 Apr 2026 | 20 Apr 2026, about `$7,859` | 161 |
| Sifu | `0xf967239debef10dbc78e9bbbb2d8a16b72a614eb` | 17 Apr 2024 – 1 May 2024 | 15 May 2024, about `$987,001` | 25 |
| HLP Liquidator | `0x2e3d94f0562703b25c83308a05046ddaf9a8dd14` | 12 Nov 2025 | 19 Nov 2025, about `$1.00M` | 139 |

HODL first regained positive NAV on 17 April with about `$100`; it crossed the
tracking threshold on 20 April. Measuring the seven-day delay to 20 April
would be wrong: for example, `$2,000 -> $0 -> $900 next day -> $1,000 after
seven days` is a prompt recovery, not a seven-day wipe-out. The code therefore
tests durability at the first positive value and applies the `$1,000` threshold
only when choosing where the new chart epoch starts.

The naive rule "any zero followed by a positive value" finds 325 episodes
across 322 vaults. Of those, 319 vaults recover within roughly one hour and
are excluded as transient data artefacts. The duration/recovery threshold is
therefore mandatory for the automated `epoch_reset` policy.

## Validation and operational status

The source-aware repair was run read-only against the production raw parquet
snapshot containing 848,333 Hypercore rows:

| Repair path | Candidates | Repaired | Deferred | Candidate vaults |
| --- | ---: | ---: | ---: | ---: |
| Daily rows compared with HF anchors | 759 | 612 | 147 | 143 |
| Stale daily rows compared with refreshed daily anchors | 292 | 135 | 157 | 38 |
| Total | 1,051 | 747 | 304 | 181 |

No production parquet was modified during this validation. Focused tests cover
HF-source provenance, legacy provenance inference, daily-only repair,
unbracketed rows, NAV/gap/lifecycle deferrals, recapitalisation epoch filtering,
and the distinction between first positive NAV and the later `$1,000` tracking
threshold. A read-only production run removes 369 pre-recapitalisation rows
across HODL My Perps, Rehobot LR, Sifu, and HLP Liquidator.

See also `scripts/hyperliquid/README-hyperliquid-vaults.md`, in particular
the *Healing share price data* section, for the scanner-level healing process.
