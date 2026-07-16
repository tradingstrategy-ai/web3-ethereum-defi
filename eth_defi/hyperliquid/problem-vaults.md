# Problem vaults

This document records known Hyperliquid/Hypercore price-history problems and
the cleaned economic-performance approximation adopted in July 2026. It is an
operational data-quality reference, not an assessment of investment quality.

## Why the share price is approximate

Hyperliquid's `vaultDetails` API does not expose historical share supply or an
investable vault unit price. It exposes rolling account-value and cumulative
PnL windows with different resolutions and retention periods. Daily and
high-frequency reads can therefore combine observations fetched at different
times and reconstruct the same vault on different arbitrary synthetic units.

The vault ledger does not close this gap. It can explain many NAV changes, but
historical events may be missing and the order of trading PnL and flows inside
an observation interval is unknown. Position fills also cannot reconstruct
complete historical mark-to-market equity and funding at every price time.
Exact historical time-weighted investor returns are consequently impossible to
recover from the available API data.

Raw Parquet `share_price` remains useful scanner evidence, but it is not an
authoritative performance series. The wrangle step preserves it as
`raw_share_price` and publishes a conservative PnL/NAV index instead.

## Cleaned economic-performance index

For each vault and fixed four-hour UTC bucket, wrangling selects one finite
NAV/PnL checkpoint. It prefers the newest `written_at` batch, then the latest
economic timestamp, with HF as the final tie-break. The selected row keeps its
original API timestamp. Other rows carry the previous clean price, and no row
is manufactured for an empty bucket. Older history therefore remains daily or
weekly where Hyperliquid has already downsampled it.

For consecutive checkpoints:

```text
pnl_change = account_pnl_now - account_pnl_previous
capital_base = max(total_assets_previous, total_assets_now, 1.0)
period_return = pnl_change / capital_base
clean_price_now = clean_price_previous * (1 + period_return)
```

Positive period returns are capped at 100% per selected checkpoint. A return
at or below -100% is accepted only when NAV is also zero and does not recover
in the retained epoch. Otherwise the price is carried so that a possible
cumulative-PnL baseline correction cannot permanently zero a funded vault.
Several valid checkpoints can compound to more than 100% in one day; the cap
is not a daily performance cap.

The index starts at `1.0` for each retained capital epoch. This denominator is
deliberately conservative when capital-flow timing is unknown: deposits and
withdrawals cannot generate clean performance, and uncertain low NAV cannot
create an unbounded return. It is still an approximation and must not be
presented as Hyperliquid's exact share price.

`hypercore_repair_status` records the evidence used:

- `approximated_pnl_nav`: ordinary four-hour economic checkpoint;
- `approximated_pnl_nav_clipped`: positive checkpoint capped at 100%;
- `approximated_pnl_nav_lag_repaired`: the matching NAV for a PnL gain
  arrived within the bounded 26-hour confirmation window, so the conservative
  return was applied only once the NAV denominator was available;
- `approximated_pnl_nav_wipe_out`: terminal NAV-corroborated complete loss;
- `approximated_pnl_nav_carried`: a non-checkpoint duplicate or a row after a
  terminal loss that adds no further performance;
- `deferred_pnl_nav`: checkpoint inputs were unavailable; and
- `deferred_pnl_nav_outlier`: an uncorroborated absorbing loss was carried.

Clean Hypercore `total_supply` is recalculated as
`total_assets / share_price`. It is a synthetic index-unit quantity, not an
actual share count. `returns_1h` remains a compatibility name: Hypercore
performance occurs at one selected observation per occupied four-hour bucket,
while carried rows have zero change. Gaps can be longer where observations are
missing or already coarse. Price level, cumulative profit and drawdown use the
clean curve. Cadence-sensitive statistics such as volatility and Sharpe must
select checkpoint rows. Low NAV still sets `tvl_filtering_mask`, but does not
rewrite the Hypercore return away from its price; suitability consumers use
the mask to exclude such rows.

The delayed-NAV rule may revise a recent provisional PnL-only checkpoint once
the matching NAV arrives, together with prices compounded after it. It skips
flat intermediate checkpoints but rejects other intervening economic changes
and observations more than 26 hours away. This is the only intended
evidence-driven revision of recently published Hypercore prices.

## Verified affected vaults

The following results come from a read-only run over the 15 July 2026
production raw Parquet. Absolute clean index levels depend on the complete
retained history; the one-period returns demonstrate the repaired economics.

| Vault | Address | Affected period or row | Previous/raw symptom | New clean result |
| --- | --- | --- | ---: | ---: |
| Crypto Plaza Relative Momentum Edge | `0xdc9955a83218b71713a83ee072055591bd4c7304` | 2–15 February 2026 | Daily prices `3.54`/`3.49` mixed with HF near `1` | Largest inspected move about `+15.8%`; no multi-unit jump/reversal |
| HODL My Perps | `0x13b43faa22d854bf43b4e7581c1b3dfcd416f8c3` | post-recapitalisation May 2026 | 21 May raw unit jump about `54.7x` | `+2.44%` compounded from six PnL/NAV checkpoints on 21 May |
| Magixbox | `0x1764dd740aba4195643bbb6a44648e0306b00cfa` | 30 January–7 February 2026 | Raw prices `1.28`, `1.77`, `4.61`, `12.90`, `26.04` | `+4.45%`, `+27.94%`, `-1.20%`, `+7.49%`, `-7.25%`, `+2.43%` at successive checkpoints |
| Magixbox | same | 15 October 2025 | `+528.3%` synthetic price move | `+84.08%` conservative economic return |
| C.A.T | `0xdfb729b4b789de6d13d6ad7ac8e2750909360af9` | 21 January–12 February 2026 | Price rose from about `1.9` to `36` while NAV stayed near `$5,000` | PnL/NAV index stays around `1.16`–`1.23` over the peak raw-price dates |
| Scared Money | `0x5290ab34acb59cfe1371baa5782eba14433d308f` | 13 August 2025 | `+204.5%` after a stale deferred row | `+44.59%` over the observed PnL interval |
| Order Block Hunter | `0x0a8499b5e925d95badc893ec7f0b1613e08f6d7c` | 2 February 2026 | Partial repair created `+275.4%` | `-4.87%`, matching PnL direction |
| HyperBotPro | `0x12e358f38741c07c1e04a8102a3170d40a600f05` | 18 March 2026 | `+285.0%` synthetic move around a withdrawal | `+12.72%` from PnL/NAV |
| Titan Vault | `0x4b0eab9444a75a03f1ef340c8beac737afa5ab09` | 7 April 2026 | `+82.9%` and `deferred_hf_nav` | `+45.15%` when the matching PnL appears in the 04:00–08:00 UTC bucket |
| Fish Market | `0x61549534dec101179983169644d7929a3d706c97` | 17–18 March 2026 | PnL moved one day before the matching NAV, producing a clipped `+100%` | `+54.48%` on 18 March using the confirmed larger NAV |

These replacements do not assert that every retained PnL observation is exact
or that every Hypercore curve is suitable for unfiltered backtesting. They
remove the known mechanism by which share units and flows became performance
and expose clipping or deferral explicitly for later inspection.

## HODL My Perps and recapitalisation

HODL is also a real lifecycle boundary. NAV reached zero on 15 October 2025
with cumulative PnL around `-$326,631` and stayed zero until new capital arrived
in April 2026. One continuous share price cannot represent both the old
investors' -100% result and the new investors' performance.

The cleaned recapitalisation rule requires:

1. a previous NAV of at least `$1,000`;
2. zero NAV with no positive recovery for at least seven days; and
3. a later NAV of at least `$1,000` before tracking restarts.

The old rows remain untouched in raw Parquet. Cleaned history starts the new
capital epoch at `1.0`, so destroyed old supply is not chain-linked to new
capital. Raw scanner `epoch_reset` flags are cleared first because they also
mark arbitrary reconstruction resets in funded vaults.

The 15 July production snapshot has four duration/NAV-qualified
recapitalisations:

| Vault | Address | New retained epoch | Rows discarded from clean output |
| --- | --- | --- | ---: |
| Rehobot LR | `0x81a20870c5c7558f117166b2a598abaa8ce91f50` | 22 April 2026 | 44 |
| HODL My Perps | `0x13b43faa22d854bf43b4e7581c1b3dfcd416f8c3` | 20 April 2026 | 161 |
| Sifu | `0xf967239debef10dbc78e9bbbb2d8a16b72a614eb` | 15 May 2024 | 25 |
| HLP Liquidator | `0x2e3d94f0562703b25c83308a05046ddaf9a8dd14` | 19 November 2025 | 139 |

The naive rule "any zero followed by positive NAV" finds hundreds of transient
scanner zeroes. The duration and capital thresholds are therefore required.

## Production census and validation

The raw production snapshot contains 874,878 Hypercore rows across 569 vaults.
After removing 369 superseded recapitalisation rows, the economic-index run
produces:

- 316,695 four-hour PnL/NAV checkpoints;
- 558,116 carried non-performance rows, including duplicate daily/HF rows,
  268 premature PnL checkpoints, and rows after a terminal wipe-out;
- 268 delayed NAV confirmations repaired;
- 54 uncorroborated absorbing losses carried for review;
- 5 positive returns capped at 100%; and
- 1 terminal NAV-corroborated wipe-out. Later rows in that epoch remain at zero
  and are carried rather than recording repeated losses or gains.

All finite one-step clean returns are between -100% and +100%. There are no
`deferred_hf_*` raw prices in the published clean curve because the index is
applied to every Hypercore vault rather than only suspicious candidates.
Every selected checkpoint occupies a unique vault/four-hour bucket, all clean
prices are finite and non-negative, and synthetic supply reproduces NAV to
floating-point precision.

The latest seven days contain 75,961 retained rows across 461 active vaults
and 10,880 non-zero clean price changes. There are 317 active vaults with more
than one clean change on at least one UTC date, confirming that the published
curve is no longer daily-only when retained HF evidence exists.

This is structural cleaning, not proof that every source PnL change is genuine.
The largest compounded vault-day is TIDAL AFFAIRS at `+247.27%`: three separate
PnL increases reconcile with the rising NAV, so it is not a synthetic share-unit
jump, although its low opening NAV makes it unsuitable for an unfiltered
backtest. Large observations that follow the documented PnL/NAV approximation
still need the existing TVL mask and explicit quality review before
return-based backtesting.

Historical repair requires no DuckDB or raw-Parquet rewrite. Regenerating
`cleaned-vault-prices-1h.parquet` from the unchanged raw Parquet applies the
same deterministic rule to old and future observations. The manual ledger-flow
backfill remains useful for raw accounting diagnosis but is no longer required
to obtain a cleaned share price.
