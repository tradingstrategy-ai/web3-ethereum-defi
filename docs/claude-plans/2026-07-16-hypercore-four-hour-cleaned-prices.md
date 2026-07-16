# Publish four-hour Hypercore cleaned prices

## Goal

Publish the finest defensible Hypercore economic share-price history from the
observations already retained by the high-frequency scanner. When usable HF
data exists, the cleaned curve should change at most once per fixed four-hour
UTC bucket instead of once per UTC date. Older history must remain naturally
daily or weekly when Hyperliquid has already downsampled it; the cleaner must
not manufacture observations or interpolate economic returns.

The output remains an approximate PnL/NAV performance index, not
Hyperliquid's unavailable historical unit price. Existing recapitalisation,
terminal-loss, delayed-NAV, clipping and outlier protections must continue to
apply.

## Current problem

The HF scanner is working as designed. It polls every four hours and stores the
raw timestamps returned by `vaultDetails`: approximately 20-minute points for
the latest day, approximately three-hour points for the latest week, and
coarser points for older periods. Both daily and HF databases are merged into
raw `vault-prices-1h.parquet`.

The resolution is lost later in
`approximate_hypercore_share_prices_from_pnl_nav()`. It normalises every
timestamp to a UTC date, selects one checkpoint per vault per date and carries
the same clean price through every other HF row. Thus the cleaned Parquet
contains sub-daily timestamps but does not publish sub-daily economic price
changes.

The 15 July 2026 production snapshot demonstrates that this is a material loss
of usable information. Over its latest seven days it contains:

- 75,961 raw Hypercore rows across 461 active vaults;
- 19,470 usable four-hour buckets, compared with 3,684 daily buckets;
- sub-daily coverage for all 461 active vaults, averaging 5.28 four-hour
  buckets per day;
- 11,059 four-hour checkpoints with a cumulative-PnL change; and
- 12,372 four-hour checkpoints with a NAV change.

The current cleaned result publishes only 2,400 non-zero price changes in the
same period, at most one per vault-day. A read-only naive four-hour calculation
over the recent 19,009 checkpoint intervals produced no gains above 100%, one
loss at or below -100%, and two absolute moves above 50%. Existing safety rules
already cover these exceptional cases.

Across the complete retained raw history, four-hour selection yields about
317,042 checkpoints instead of 85,891 daily checkpoints. Historical outliers
remain present and must still pass the existing conservative repair rules.
These figures are indicative acceptance baselines and must be recalculated
from the final implementation rather than frozen as unit-test constants.

## Output contract

The cleaned output must follow these rules:

1. A fixed UTC bucket is `[00:00, 04:00)`, `[04:00, 08:00)`, and so on.
2. At most one usable PnL/NAV checkpoint may add performance in each four-hour
   bucket for a vault and capital epoch.
3. The selected row keeps its original API timestamp. Do not floor the
   published timestamp to the bucket boundary.
4. Other raw rows remain in cleaned Parquet and carry the previous clean price.
5. Do not create a four-hour row when no raw observation exists. Consumers may
   resample and forward-fill separately when they need a regular grid.
6. Daily and weekly source observations remain publishable as-is. They simply
   occupy their corresponding four-hour bucket, so old history stays coarse.
7. The raw scanner price remains in `raw_share_price`; it never becomes the
   authoritative cleaned price.
8. `returns_1h` remains a compatibility column calculated between consecutive
   output rows. Its documentation must say that Hypercore performance changes
   occur at one selected observation per occupied four-hour bucket rather than
   at true hourly intervals. Gaps can be longer where the source is missing or
   already coarse.
9. No Parquet schema change, DuckDB rewrite or new persisted column is needed.

## Implementation

### Four-hour checkpoint selection

Update `approximate_hypercore_share_prices_from_pnl_nav()` in
`eth_defi/research/wrangle_vault_prices.py`.

Introduce a module-level four-hour checkpoint interval constant. Replace the
normalised `day_ns` grouping key with a four-hour UTC bucket derived from the
naive UTC timestamp. Keep the existing deterministic precedence inside a
bucket:

1. newest finite `written_at` batch;
2. latest economic timestamp;
3. HF source as the final exact-tie preference.

Do not add a generic resampling framework or a configurable public API. A
single Hypercore-specific constant keeps this repair explicit and small. Use
the same NumPy/Pandas vectorised selection already present in the function.

Rename local variables, docstrings, logging and status descriptions from
"daily checkpoint" to "four-hour checkpoint" or the bucket-neutral
"economic checkpoint" where appropriate. Existing status values can remain
unchanged:

- `approximated_pnl_nav` for a selected ordinary checkpoint;
- `approximated_pnl_nav_carried` for an unselected row;
- the existing clipped, wipe-out, delayed and lag-repaired variants for their
  current meanings.

Changing a label is unnecessary and would create avoidable consumer work.

### Economic return calculation

Keep the current calculation for consecutive selected checkpoints:

```text
pnl_change = account_pnl_now - account_pnl_previous
capital_base = max(total_assets_previous, total_assets_now, 1.0)
period_return = pnl_change / capital_base
clean_price_now = clean_price_previous * (1 + applied_period_return)
```

Keep all existing rules after checkpoint selection:

- positive-return cap;
- funded absorbing-loss deferral;
- NAV-corroborated terminal wipe-out;
- absorbing zero price after a terminal wipe-out;
- recapitalisation epoch separation;
- synthetic `total_supply = total_assets / clean_share_price`; and
- EVM/native-protocol isolation.

Apply clipping and deferral to each selected four-hour economic checkpoint,
not to a subsequently aggregated daily return. Consequently, compounding the
four-hour results over a UTC date need not reproduce the old daily result when
the old single daily interval was clipped or deferred. This is intentional:
the four-hour observations are the new repair decisions, and their compounded
window return is the quantity that must be validated against economic
evidence.

The change must not reintroduce scanner share units, daily/HF batch stitching
or flow-derived synthetic supply.

### Delayed NAV confirmation

The Fish Market repair currently recognises three consecutive *daily*
checkpoints. Four-hour selection must not break its historical repair or miss
the same API behaviour at sub-daily cadence.

Make the lag rule bucket-independent. Add one private confirmation-delay
constant of 26 hours, allowing ordinary API timestamp jitter while excluding
weekly all-time observations:

- the middle checkpoint has a positive PnL change while NAV is unchanged
  within the existing tolerance;
- the previous-to-middle elapsed time is positive and no greater than 26
  hours;
- scan forward from the middle checkpoint for at most 26 elapsed hours,
  skipping selected checkpoints whose PnL and NAV are both unchanged within
  tolerance;
- the first non-flat checkpoint must have unchanged PnL and a NAV change
  matching the earlier PnL move; any intervening economic change disqualifies
  the repair; and
- all existing positive-only and numerical-tolerance conditions remain.

Carry the premature PnL checkpoint and apply the gain once at the confirming
checkpoint using the larger opening or confirmed NAV. Fish Market's actual
HF timestamps are 16 March 23:12, 17 March 23:32 and 18 March 22:32 UTC, while
the merged daily source supplies the confirming NAV at 18 March 00:00. The
predecessor gap is 24 hours 20 minutes, so a strict 24-hour cut-off would
regress the repair. The bounded 26-hour window admits this ordinary jitter and
recent four-hour confirmations but rejects weekly all-time points. Do not
require an exact number of four-hour buckets because missing scanner
observations, flat intermediate observations and API resolution changes are
legitimate.
Missing observations are tolerated by ordinary checkpoint selection, but a
gap longer than 26 hours deliberately disqualifies this specific lag repair.
This conservative asymmetry avoids joining unrelated points from the weekly
all-time series.

Document that a confirming observation may revise the previous checkpoint's
provisional return. An identical rerun remains idempotent; append stability has
this already-known evidence-driven exception.

### Processing and publishing

Do not change pipeline ordering. The relevant sequence remains:

1. merge daily and HF raw Hypercore rows into raw Parquet;
2. discard superseded pre-recapitalisation history from the cleaned view;
3. build the four-hour PnL/NAV index;
4. calculate compatibility returns and TVL masks; and
5. publish `cleaned-vault-prices-1h.parquet`.

Deployment requires regenerating cleaned Parquet from unchanged raw Parquet.
This deterministically repairs historical rows where HF observations were
preserved and automatically leaves older downsampled periods coarse. No raw
Parquet or DuckDB backup/migration is required because neither source is
modified by wrangling.

## Tests

Extend `tests/research/test_clean_prices.py` with focused function tests:

1. **Multiple checkpoints per day:** observations spanning at least three
   four-hour buckets produce three economic price changes when PnL changes.
2. **One checkpoint per bucket:** multiple daily/HF rows in the same bucket
   select exactly one row using `written_at`, timestamp and HF tie precedence;
   other rows carry the price.
3. **No manufactured rows:** a missing four-hour bucket does not add a row or
   an interpolated return.
4. **Adaptive coarse history:** daily and weekly observations remain daily and
   weekly; they are not rejected merely because adjacent buckets are absent.
5. **Fish Market:** the 17–18 March PnL-first/NAV-confirming sequence remains a
   single approximately +54.48% gain at the confirming observation.
6. **Sub-daily NAV lag:** the same stagger across four-hour observations is
   repaired when one or more flat selected checkpoints appear before the NAV
   confirmation.
7. **Lag boundary:** use Fish Market-shaped timestamps to prove that a 24-hour
   20-minute predecessor gap is accepted. A confirmation more than 26 hours
   later and a predecessor more than 26 hours earlier are each rejected. An
   intervening economic change, a negative PnL move or a NAV mismatch is also
   not joined.
8. **Per-bucket protections:** multiple clipped or deferred observations in
   different four-hour buckets of the same UTC date are handled independently;
   their compounded result intentionally differs from the old single daily
   decision.
9. **Existing protections:** terminal wipe-out, recapitalisation, idempotence
   and non-Hypercore isolation continue to pass.
10. **Fresh append:** appending an ordinary new bucket preserves old prices;
   appending a matching NAV confirmation may revise only the provisional
   PnL-only checkpoint and later compounded prices.

The append test must cover the recent publication tail explicitly: write a
cleaned prefix ending at the provisional PnL-only checkpoint, append its NAV
confirmation, rerun the cleaner, and assert that no earlier unrelated epoch or
vault changes. This is the sole intended evidence-driven revision of already
published recent Hypercore prices.

Keep the existing production-shaped fixture values. Do not create a separate
address-specific Fish Market branch in production code.

## Production validation

Run the final wrangle read-only over a copy of the production raw Parquet and
compare daily and four-hour outputs by vault.

Required checks:

- raw and cleaned source files are not modified in place during validation;
- retained row count and schema are unchanged apart from existing
  recapitalisation filtering;
- recent active vaults publish more than one economic checkpoint per day when
  HF data exists;
- no vault publishes more than one selected checkpoint in a four-hour bucket;
- every selected checkpoint corresponds to a finite raw NAV/PnL observation;
- all share prices and returns are finite, share prices are non-negative and
  returns stay within the existing -100%/+100% applied bounds;
- synthetic total supply maintains the NAV identity wherever NAV and price are
  finite and price is positive;
- EVM and other native-protocol rows are byte-equivalent to the current
  cleaner;
- report the number of checkpoints, changing checkpoints, carried rows,
  clipped gains, deferred losses, terminal wipe-outs and delayed-NAV repairs;
  and
- report the maximum positive and negative compounded return per vault and UTC
  date, in addition to enforcing the existing per-checkpoint bounds, so stacked
  near-cap observations are visible.

Also compare compounded four-hour returns over the old daily windows. Report
windows where per-checkpoint clipping or deferral changes the aggregate result,
and reconcile the largest differences against PnL and NAV evidence rather
than assuming equality with the superseded daily approximation. In particular,
validate IKAGI's June window this way.

Inspect the largest positive and negative four-hour returns and rapid
opposite-direction pairs. The purpose is to detect a systematic PnL/NAV timing
or source-baseline failure, not to reject a genuine return solely because it is
large.

## Vault regression checks

The earlier repaired vaults must remain economically coherent:

- Crypto Plaza Relative Momentum Edge must not regain its February unit
  jump/reversal;
- HODL My Perps must retain only its post-recapitalisation epoch and must not
  regain the May synthetic unit jump;
- Magixbox and C.A.T must not mix daily and HF scanner share units;
- Order Block Hunter, HyperBotPro and Titan Vault must retain PnL/NAV-based
  direction and magnitude rather than partial-repair prices;
- Fish Market must retain the approximately +54.48% delayed-NAV repair and the
  economically reconciled December loss; and
- Magixbox and Satori's October gains must remain economically supported.

IKAGI's June gain has retained sub-daily raw data. It should be split across
the selected four-hour checkpoints instead of forced into the previous single
+62.41% daily observation. Report its compounded return over the same original
window and reconcile the direction against NAV, PnL, ledger and fills; do not
force each new sub-period to reproduce the old daily percentage.

## Documentation

Update:

- `approximate_hypercore_share_prices_from_pnl_nav()` and
  `CleanedVaultPriceRow` docstrings;
- `eth_defi/hyperliquid/problem-vaults.md` with the final four-hour census and
  residual limitations;
- `scripts/hyperliquid/README-hyperliquid-vaults-high-frequency.md` to separate
  the four-hour scan trigger, approximately 20-minute raw API resolution and
  four-hour cleaned publication cadence; and
- the prior economic-performance plan where it claims cleaned Hypercore
  performance is daily-only.

The public cleaner docstring and high-frequency README must tell consumers
that a later NAV confirmation can revise the recent provisional PnL-only
checkpoint and its subsequently compounded prices. Add a dated
`CHANGELOG.md` entry when the implementation is opened as a feature PR.

State explicitly that finer granularity improves timing but does not make the
approximate index an exact investor share price. Historical resolution is
limited by what the scanner retained before Hyperliquid downsampled the API
windows.

## Completion criteria

The work is complete when:

- production-shaped tests and the focused cleaner suite pass;
- a read-only production run demonstrates sub-daily clean price changes for
  recent HF-covered vaults;
- earlier repaired vaults remain repaired;
- Fish Market remains repaired under the bucket-independent lag rule;
- documentation no longer claims that cleaned Hypercore performance is
  daily-only; and
- regenerated cleaned Parquet can be published without modifying raw Parquet
  or either Hyperliquid DuckDB database.
