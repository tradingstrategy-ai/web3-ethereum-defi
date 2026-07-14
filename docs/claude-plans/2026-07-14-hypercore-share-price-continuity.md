# Hypercore share price continuity repair plan

**Date:** 2026-07-14

## Goal

Produce a stable, actionable Hypercore share price with the smallest practical
change to the existing pipeline:

1. New daily and high-frequency scans must continue on the same price scale.
2. Existing batch-scale jumps in the historical Parquet must be repaired during
   wrangling.

Hyperliquid does not expose a native share price or share supply. The value we
publish is a synthetic time-weighted return index derived from portfolio NAV and
PnL. This plan keeps that existing model instead of adding a second
fill-by-fill, candle-based accounting system.

## Root cause

There are two related defects.

- `_merge_portfolio_periods()` overlays high-resolution NAV from the `month`,
  `week`, and `day` windows, but replaces their matching PnL with the nearest
  sparse `allTime` PnL. Trading changes are then mistaken for capital flows by
  `netflow_update = NAV change - PnL change`, corrupting synthetic share supply.
- Each API read recalculates the complete synthetic curve from rolling windows.
  The HF scanner appends the newly calculated rows without aligning their price
  scale with the rows already stored. A `written_at` batch boundary can
  therefore create a large price-level jump even when NAV and PnL barely move.

HODL My Perps demonstrates both problems after its April 2026 recapitalisation:
its May price changes of roughly 2x, 56x, and 3.6x coincide with scanner batch
changes rather than economic returns.

## Part 1: Fix new data going forward

### 1. Preserve each period's NAV/PnL pairs

Update `eth_defi/hyperliquid/daily_metrics.py::_merge_portfolio_periods()`.

- Pair `account_value_history` and `pnl_history` by timestamp within each API
  period.
- Treat `allTime` PnL as the common cumulative scale.
- For each shorter period, find its latest timestamp that is also present in
  `allTime`, then add the PnL difference at that shared timestamp to every PnL
  value in the shorter period. If there is no shared timestamp, do not overlay
  that period.
- Overlay the aligned `(timestamp, NAV, PnL)` rows together. Do not perform a
  nearest-neighbour lookup against sparse `allTime` PnL.

This retains the high-resolution trading PnL that the current merge discards,
while leaving `portfolio_to_combined_dataframe()` and `_calculate_share_price()`
unchanged.

### 2. Chain a recalculated curve to stored history

Add one small shared helper for the daily and HF scanners. Given a newly
calculated curve and the latest stored share price:

1. Locate that stored timestamp in the new curve, using exact overlap for daily
   rows and time interpolation for HF rows.
2. Calculate one multiplicative scale factor so that the new curve equals the
   stored share price at the overlap.
3. Multiply new `share_price` values by the factor and divide `total_supply` by
   the same factor. NAV and PnL are not changed.
4. Recalculate the first appended return against the actual last stored share
   price.

Both scanner databases persist their return column independently, so the last
step is required even though the wrangle pipeline later derives returns again.
Use one lower-level scaling primitive for this helper, historical batch
stitching, and recapitalisation normalisation: multiply `share_price` for a row
slice by a factor and divide `total_supply` by the same factor.

For a brand-new vault with no stored price, use the newly calculated curve as
is; the existing synthetic-supply calculation anchors its first funded row at
`1.0`. A missing stored anchor is therefore a valid bootstrap, whereas an
existing stored anchor outside the new API curve is an error.

A zero stored price records a complete NAV wipe-out and cannot be used as a
scale factor. Resume the reconstructed curve unchanged at this boundary; the
wrangle pipeline applies the stricter duration and NAV thresholds that decide
whether a later recapitalisation becomes a retained performance epoch. Keep
negative and non-finite stored prices as hard errors.

For an existing daily vault, only update the overlapping boundary date and
append later dates; do not refresh its complete historical curve on every scan.
The HF scanner keeps its existing append-only behaviour after applying the same
alignment.

If the latest stored timestamp is outside the new API curve, skip that vault for
the scan and log the reason. Guessing a scale without any overlap would recreate
the original problem.

### 3. Keep Hypercore returns derived from the repaired price

After the price calculation is corrected, do not replace positive Hypercore
returns above 50% with zero in `clean_returns()`. That changes the return but
leaves the price level untouched and hides future calculation defects. Keep the
existing generic return cleaning for other protocols.

No new Parquet columns or pricing database are needed.

## Part 2: Fix historical data

Historical high-resolution period PnL was discarded by the old merge and can no
longer be fetched for expired rolling windows. Repair the scale discontinuities
that are still observable in the raw Parquet instead of attempting a more
precise reconstruction than the retained data supports.

### 1. Normalise recapitalised epochs

Extend `discard_hypercore_pre_recapitalisation_history()` so that, after it
selects the first retained row of a recapitalised epoch, it normalises that row
to share price `1.0` and applies the same factor to all later rows in the epoch.
Adjust `total_supply` inversely so that `total_assets / total_supply` remains
consistent.

The old epoch remains in the raw Parquet and retains its real -100% result. For
HODL, the cleaned epoch continues to start at the existing 20 April 2026 `$1,000`
tracking threshold.

Initialise `raw_share_price` before this step so the original input value remains
available after normalisation and later repairs.

### 2. Stitch legacy HF scanner batches

Add one wrangle function before
`fix_hypercore_source_overlap_share_prices()` and before returns are calculated.
For each Hypercore vault and recapitalisation epoch:

- Inspect consecutive HF rows where `written_at` changes.
- Estimate the economic boundary return from the change in `cumulative_pnl`
  divided by the preceding NAV.
- When the observed share-price boundary differs from that estimate by more
  than 50%, multiply the current and all later rows by one correction factor.
- Only use short, consecutive boundaries with finite positive NAV and PnL. If
  those inputs are unavailable, leave the rows unchanged.
- Record an applied repair using the existing `hypercore_repair_status` column,
  for example `repaired_hf_batch_scale`.
- Log the vault, timestamp, and factor for every repair; highlight factors over
  2x so large historical corrections are visible in the wrangle summary.

The correction factor is cumulative across later batches. This repairs HODL's
post-recapitalisation May jumps and the same `written_at`-aligned failure in
other vaults without vault-name allowlists or hand-authored dates.

After HF batches are on one scale, run the existing daily/HF source-overlap
repair so daily rows use the corrected HF curve as their anchor.

### 3. Regenerate the cleaned production Parquet

- Run the wrangle pipeline from the unchanged raw production Parquet.
- Write the result through the existing temporary-file/atomic replacement path.
- Do not rewrite or delete the raw Parquet.
- Compare HODL, Magixbox, C.A.T, and the previously identified batch-jump census
  before replacing the cleaned production file.

## Focused tests

Add small synthetic tests only:

1. A shorter portfolio period whose PnL starts at zero is aligned to `allTime`
   while retaining its intra-period PnL changes.
2. Re-reading the same vault with shifted rolling timestamps produces an HF
   append on the existing price scale.
3. A daily refresh updates only the overlap date and new dates.
4. A `written_at` boundary with stable PnL is stitched, while a matching genuine
   PnL return is left unchanged.
5. A recapitalised epoch starts at `1.0`; its old cleaned rows are removed and
   its raw share prices remain preserved.
6. Hypercore returns are not silently zeroed solely because they exceed 50%.

Run the relevant tests with:

```shell
source .local-test.env && poetry run pytest \
  tests/hyperliquid/test_share_price_continuity.py \
  tests/research/test_clean_prices.py -k 'hypercore or recapitalisation' -q
```

Run the completed wrangle step read-only against a copy of the current
production Parquet and confirm:

- HODL's new epoch starts at `1.0` and no longer has the May batch-scale jumps.
- No repair crosses a zero-NAV/recapitalisation boundary.
- Re-running the wrangle step produces identical prices.

## Documentation updates during implementation

- Update `eth_defi/hyperliquid/problem-vaults.md` with the final HODL result and
  the number of other historical batches repaired.
- Update the share-price and healing sections of
  `scripts/hyperliquid/README-hyperliquid-vaults.md` to describe PnL alignment,
  scanner scale chaining, and historical batch stitching.
- Correct the `compute_event_share_prices()` documentation: it currently uses
  realised PnL only and is not the continuous mark-to-market method selected by
  this plan.

## Out of scope

- A new event-history database or candle-based equity reconstruction.
- New quality/status schemas beyond the existing `raw_share_price` and
  `hypercore_repair_status` columns.
- Interpolating missing C.A.T history where neither HF observations nor a safe
  scanner-batch boundary exists.
- Rewriting the raw production Parquet.
