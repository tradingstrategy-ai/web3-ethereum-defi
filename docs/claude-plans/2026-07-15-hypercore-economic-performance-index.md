# Hypercore economic performance index plan

**Date:** 2026-07-15

## Goal

Make the cleaned share-price history of Hypercore vaults reflect the best
available estimate of real economic performance. In particular, the cleaned
curves for Crypto Plaza Relative Momentum Edge, HODL My Perps, Magixbox,
C.A.T, Scared Money, Order Block Hunter, HyperBotPro and Titan Vault must no
longer contain changes caused by scanner units, missing flows, incompatible
rolling windows or a partially repaired daily/HF path.

The output is an approximate performance index, not a reconstructed
Hyperliquid share price. It must be suitable for charts and return calculations
without suggesting precision that the source data cannot support.

## Why an exact share price is impossible

Hyperliquid does not publish a historical vault share price or historical share
supply. Its `vaultDetails` response contains rolling account-value and
cumulative-PnL windows, but those windows:

- have different resolutions and retention periods;
- can refresh at different timestamps;
- do not provide the exact timing and unit price of every capital flow; and
- can be reconstructed on a different arbitrary synthetic share-price scale by
  separate daily and high-frequency scanner runs.

The current scanner infers net flows from changes in NAV and PnL, then invents
an ERC-4626-like supply. A stale or differently aligned PnL observation is
therefore interpreted as a deposit or withdrawal and permanently changes that
synthetic supply. Later scans can reconstruct the same economic history in a
different unit. The raw result is internally useful for diagnosis, but it is
not an authoritative investor share price.

The vault ledger helps in some intervals, but it does not make exact historical
time-weighted returns recoverable. Old events can be absent, observations can
fall between price timestamps, and the order of a flow and trading PnL inside a
period is unknown. Position and fill reconstruction is also insufficient: it
cannot recover historical mark-to-market equity, funding and all vault-level
cash movements at every price timestamp.

Consequently, no more elaborate stitching rule can make the raw synthetic
share price exact. The defensible output is a clearly labelled performance
index whose direction and magnitude come from observed account PnL relative to
observed capital.

## Confirmed failure modes

The production data checked on 15 July 2026 shows four distinct ways in which
the current repair stack can leave or create an implausible clean return:

1. A stale daily row is retained as `deferred_hf_gap`, so the next valid row is
   compared with the wrong starting level. Scared Money's apparent 13 August
   `+204.5%` move is this case.
2. A raw synthetic curve can be exactly consistent with the scanner's assumed
   flow ordering but not with a knowable investor return. Magixbox's October
   `+528.3%` and HyperBotPro's March `+285.0%` moves are examples.
3. Hyperliquid cannot provide enough evidence for the conservative anchor
   repair, leaving `deferred_hf_nav` prices in the published clean curve. This
   affects Magixbox, Order Block Hunter and Titan Vault.
4. Repairing only one side of an incompatible price interval mixes two units
   and creates a new return. Order Block Hunter's raw 1–2 February movement is
   about `-4.6%`, but repairing 1 February and deferring 2 February turns it
   into `+275.4%`.

There are 265 `deferred_*` rows across 68 Hypercore vaults in the checked clean
Parquet. Forty-one of those rows across 29 vaults directly produce a move over
50%. A separate 390 unflagged moves over 50% occur across 148 vaults. These are
not all false trading returns, but the raw synthetic price alone cannot prove
which are genuine.

## Chosen approximation

Replace the published Hypercore synthetic price during wrangling with a
conservative PnL/NAV performance index. Do this for every Hypercore vault, not
only a list of known addresses and not only rows exceeding an outlier
threshold.

For consecutive usable economic observations in the same performance epoch:

```text
pnl_change = account_pnl_now - account_pnl_previous
capital_base = max(total_assets_previous, total_assets_now, 1.0)
raw_period_return = pnl_change / capital_base
if raw_period_return <= -1.0 and wipe_out_is_corroborated:
    period_return = -1.0
elif raw_period_return <= -1.0:
    carry previous price and mark deferred_pnl_nav_outlier
else:
    period_return = min(raw_period_return, 1.0)
clean_price_now = clean_price_previous * (1.0 + period_return)
```

Use the difference in cumulative `account_pnl`, not the exported `daily_pnl`.
The latter is calculated inside individual scanner windows and has been shown
to be inconsistent when daily and HF histories are merged. The maximum of
opening and closing NAV is deliberately conservative when the intra-period
timing of deposits and withdrawals is unknown: capital flows cannot generate a
return, and uncertain small denominators cannot turn PnL into a multi-hundred
percent price jump. The positive 100% return bound prevents one questionable
period from dominating the complete history; it is a data-quality bound, not a
claim that a leveraged vault cannot economically gain more.

A negative return at or below `-100%` needs different handling because zero is
an absorbing compounded-price state. Accept `-100%` only when NAV is also at
the zero threshold and remains zero until the end of the data or a qualifying
`epoch_reset`. That is independent evidence of a wipe-out. If NAV remains
funded, carry the previous price and mark `deferred_pnl_nav_outlier` instead of
turning one possible cumulative-PnL baseline correction into a false permanent
loss. Returns strictly between `-100%` and zero are applied without clipping.

Start each retained vault or recapitalisation epoch at `1.0`. A `-100%` result
ends that epoch. A later funded history must satisfy the existing durable-zero
and `$1,000` recapitalisation rule before it starts a new index at `1.0`; it
must never be compounded from zero or joined to the wiped-out capital.
Clear raw scanner `epoch_reset` flags before applying that rule: production
validation showed that they also mark arbitrary synthetic-supply resets in
funded vaults and would create false clean restarts.

This approximation cannot assign the PnL to an exact trade or flow timestamp.
It intentionally reports the return at the next usable economic observation.
Do not interpolate backwards across an observation gap, because that would
introduce future information into backtests.

## Canonical observations

Daily and HF rows can describe the same calendar period with different scanner
units. Build one deterministic economic checkpoint per vault and UTC date:

1. Consider rows with finite `total_assets` and `account_pnl`.
2. Prefer the row from the newest finite `written_at` batch on that UTC date,
   then the latest economic timestamp. At an otherwise identical timestamp,
   prefer an explicit `hf` row. For legacy rows without `written_at`, fall back
   to latest timestamp and the same source tie-break.
3. Calculate the PnL/NAV index only between these selected checkpoints.
4. Preserve the existing row set for metadata compatibility. Forward-fill the
   last known clean index value through non-checkpoint rows, so no return is
   fabricated from duplicate or stale observations. Mark these rows
   `approximated_pnl_nav_carried` so cadence-sensitive consumers can select
   only actual performance checkpoints. Rows after a terminal loss use the
   same carried status because the index is already zero and cannot record
   further performance within that epoch.
5. If a date has no usable NAV/PnL pair, carry the last clean price and record
   `deferred_pnl_nav` in `hypercore_repair_status`. Use
   `deferred_pnl_nav_outlier` for an uncorroborated return at or below `-100%`.
   The row remains visible but its zero price movement must not be described as
   observed performance.

This produces a daily-resolution economic curve even when the raw file
contains irregular HF timestamps. `returns_1h` remains the existing
compatibility name, but it continues to mean the percentage change between
consecutive rows rather than a guaranteed one-hour return. The documentation
must state this explicitly. Price level, cumulative profit and drawdown are the
supported outputs of this approximation. Volatility, Sharpe and other
cadence-sensitive statistics must use the selected daily checkpoints rather
than treating every carried raw row as an independent hourly return; audit
those downstream call sites during implementation.

Newest `written_at` is the best available freshness signal, not proof that an
API value is economically correct. Cumulative PnL can legitimately rise or
fall, so a monotonicity check would reject real losses. Retain the residual
source-baseline risk in the documentation, expose clipped/deferred statuses and
inspect the largest resulting returns in production validation.

## Wrangle implementation

Add one focused function in
`eth_defi/research/wrangle_vault_prices.py`, provisionally named
`approximate_hypercore_share_prices_from_pnl_nav()`.

The function must:

- operate only on
  `eth_defi.hyperliquid.constants.HYPERCORE_CHAIN_ID` (`9999`, the project's
  synthetic namespace for native Hyperliquid vaults, distinct from HyperEVM
  chain id `999`) and leave all peers unchanged;
- require timestamp-sorted vault groups and keep epoch boundaries separate;
- use only duration/NAV-qualified epoch boundaries rebuilt by
  `discard_hypercore_pre_recapitalisation_history()`, never raw scanner reset
  flags;
- initialise `raw_share_price` from the untouched scanner value before any
  clean price is changed;
- select daily checkpoints deterministically as described above;
- build and forward-fill the compounded index;
- set `hypercore_repair_status` to `approximated_pnl_nav` at usable economic
  checkpoints, `approximated_pnl_nav_clipped` at positive capped checkpoints,
  `approximated_pnl_nav_carried` at ordinary non-checkpoint rows and after a
  terminal loss,
  `deferred_pnl_nav` where checkpoint inputs are missing, and
  `deferred_pnl_nav_outlier` for an uncorroborated absorbing loss;
- recompute the synthetic `total_supply` as `total_assets / share_price` for
  finite positive values, so the exported NAV identity is not left internally
  contradictory; and
- log aggregate vault, checkpoint, carried-row and clipped/deferred-return
  counts rather than one line for every repaired price; and
- fail the production validation if the raw input contains Hypercore metadata
  but the function selects zero `HYPERCORE_CHAIN_ID` rows. This catches a
  namespace or input-filter mistake that synthetic fixtures alone would miss.

Its docstring must record the production findings behind the policy: exact
Hyperliquid unit-price history is unavailable, partial daily/HF repairs created
Order Block Hunter's clean spike, and known ledger flows still do not establish
intra-period time-weighted returns. The docstring must explain why cumulative
PnL, the conservative NAV denominator, daily checkpoints, the asymmetric
positive cap/negative wipe-out rule and carry-forward were selected, and
document all expected DataFrame columns and types.

Call the function immediately after
`discard_hypercore_pre_recapitalisation_history()` and before
`calculate_vault_returns()`. Once the index is authoritative for cleaned
Hypercore rows, remove the Hypercore calls to:

- `stitch_hypercore_high_freq_share_price_batches()`;
- `cap_hypercore_share_prices()`;
- `fix_hypercore_flow_reconciled_share_price_paths()`; and
- `fix_hypercore_source_overlap_share_prices()`.

Delete the now-unused repair implementations, constants and narrowly coupled
tests after a repository-wide reference check. Do not layer the new index on
top of those repairs: that would retain unnecessary code and make
`raw_share_price` mean "partly repaired input" rather than the scanner value.
Keep the generic EVM share-price repair unchanged and continue excluding
Hypercore from it.

`calculate_vault_returns()` must run after the approximation so
`returns_1h`, compounded profit and chart performance all derive from the same
clean `share_price`. Do not repair only `returns_1h`; a return series that
disagrees with its price level is not actionable.

The generic TVL cleaner must keep `tvl_filtering_mask` for low-capital
Hypercore rows but must not zero their return after it was derived from the
index. Consumers can exclude masked rows for suitability analysis without
making published profit disagree with price.

## Historical data

Historical repair needs no raw-data migration and no destructive DuckDB edit.
The ordinary production wrangle already rebuilds the cleaned Parquet from the
raw Parquet, so the same function repairs all retained history on the next
cleaning run.

The historical procedure is:

1. Make a copy of the current raw and cleaned production Parquets.
2. Run the updated wrangle against the raw copy.
3. Compare all named vaults and the full Hypercore anomaly census.
4. Publish the regenerated cleaned Parquet through the existing temporary-file
   and atomic-replacement path.
5. Leave the raw Parquet and both scanner DuckDB files untouched.

`raw_share_price` preserves the old synthetic series for audit and incident
analysis. The old epoch of a recapitalised vault remains available in raw data,
including HODL's genuine `-100%` outcome, while the cleaned chart represents
only the current investable epoch.

The manual historical-flow backfill is no longer required to obtain the clean
price. Retain it only if it remains useful for raw accounting diagnosis;
otherwise remove it. In either case, remove instructions claiming that
backfilled flows are necessary to resolve `deferred_hf_nav` rows.

## Data going forward

Keep the daily and HF scanners and their raw synthetic calculations unchanged
unless an independent scanner bug needs fixing. New observations continue to
append to the raw stores, preserving source evidence. Every production wrangle
then deterministically rebuilds the PnL/NAV index using both old and new raw
rows.

This separation is intentional:

- scanners collect and preserve what Hyperliquid returned;
- wrangling decides the best comparable economic-performance approximation;
- consumers read only the cleaned index for charts and returns; and
- investigators can still compare the index with `raw_share_price`, NAV, PnL,
  source and write batch.

Re-running wrangle on unchanged input must be idempotent. Appending an ordinary
new raw observation for the current or a future date may add a checkpoint but
must not alter earlier checkpoint prices. A newly observed recovery after a
provisional terminal zero is the exception: it can show that the earlier zero
was not an absorbing loss and deterministically revise that open lifecycle. A
genuinely late raw observation for a past date may likewise select a fresher
checkpoint and rewrite the later compounded index. Preserving a known stale
classification would be worse, and the raw file remains the audit trail. Test
and document these cases.

## Vault-level acceptance checks

Use production-shaped fixtures for the failure mechanics and a read-only run
over the production raw Parquet for exact vault validation. The indicative
returns below come from the 15 July investigation. Recalculate them directly
from the copied raw checkpoints with the final denominator before freezing
fixture expectations; do not make an implementation conform to a transcription
error in this table.

| Vault and event | Current clean symptom | Expected economic-index result |
| --- | ---: | ---: |
| Scared Money, 13 August | `+204.5%` | approximately `+44.6%` |
| Magixbox, 15 October | `+528.3%` | approximately `+84.1%` |
| Magixbox, 30 January | `+94.8%` | approximately `+4.5%` |
| Magixbox, 1 February | `+159.5%` | approximately `-1.2%` |
| Order Block Hunter, 2 February | `+275.4%` | approximately `-4.9%` |
| HyperBotPro, 18 March | `+285.0%` | approximately `+12.7%` |
| Titan Vault, 7 April | `+82.9%` | approximately `+27.9%`; the checked closing NAV is the larger denominator |

Also verify:

- Crypto Plaza Relative Momentum Edge no longer has its February synthetic
  jump/reversal;
- C.A.T no longer rises from about `1.9` to `36` while NAV stays near `$5,000`;
- Magixbox's January/February values no longer mix prices near `1` with values
  of `4.6`, `12.9` and `26.0`;
- HODL starts its post-recapitalisation clean epoch at `1.0`, does not contain
  the May `54.7x` unit jump or the later `+5,589%` clean move, and does not hide
  the old epoch's raw complete loss; and
- the same rules cover Rehobot LR, Sifu, HLP Liquidator and any future durable
  recapitalisation without address-specific branches.

The purpose of these checks is not to force every large result below an
arbitrary chart threshold. A large PnL-supported return may remain, up to the
documented approximation bound. The required invariant is that deposits,
withdrawals, scanner scale changes and partial repair units cannot themselves
become clean performance.

## Focused tests

Replace the specialised anchor/stitch tests in
`tests/research/test_clean_prices.py` with focused tests for the new contract:

1. No-flow PnL changes compound from the previous clean index and have the
   expected sign and magnitude.
2. A large deposit or withdrawal with no PnL leaves the clean price flat.
3. Mixed daily/HF rows on the same date produce one deterministic checkpoint;
   stale duplicates cannot create a return.
4. Missing NAV/PnL carries the previous clean price and records
   `deferred_pnl_nav`.
5. A positive ratio over 100% is capped and counted. A ratio at or below
   `-100%` ends a genuinely zero-NAV epoch but is deferred for a funded vault.
6. A durable recapitalisation discards the previous cleaned epoch and restarts
   at `1.0`; no return crosses the boundary.
7. `raw_share_price` remains byte-for-byte equal to the input scanner values,
   while `total_supply`, `share_price` and `returns_1h` agree after cleaning.
8. Non-Hypercore rows are unchanged.
9. A production-shaped Order Block Hunter fixture cannot create a spike by
   mixing one repaired row with one deferred raw row.
10. A second run on the same input produces the same clean prices and statuses.
11. Appending an ordinary future checkpoint leaves earlier checkpoints
    byte-for-byte unchanged. A future recovery may revise a provisional
    terminal wipe-out, while a fresher past-date row deterministically revises
    the later index.

Run the focused tests with the repository Poetry environment and
`.local-test.env`, then run the full `tests/research/test_clean_prices.py` file
because the implementation removes several existing repair paths.

## Production validation

Run the new wrangle read-only against a copy of the current production raw
Parquet and produce a compact before/after table containing:

- the named vault and timestamp;
- raw scanner price;
- old clean price and return;
- new economic-index price and return;
- NAV, cumulative PnL and repair status; and
- whether the return was clipped or carried.

Scan all Hypercore vaults after the change and report:

- maximum and minimum clean period returns;
- count of clipped checkpoints;
- count of carried and uncorroborated-negative rows and affected vaults;
- count of returns over 50% by status; and
- recapitalised vaults and their first retained timestamp.

Manually inspect the largest positive and negative results rather than adding
more automatic rules. The implementation is complete when all named failure
mechanics use the approximation, no `deferred_hf_*` raw price remains in the
published clean curve, price-derived profits agree with the clean price, and
an unchanged rerun is identical.

## Documentation changes during implementation

- Rewrite the data-model and affected-vault sections of
  `eth_defi/hyperliquid/problem-vaults.md`. It currently describes HF anchors
  as canonical and says Magixbox's October move must remain; neither statement
  is appropriate once exact unit performance is acknowledged as unavailable.
- Update `CleanedVaultPriceRow` and `hypercore_repair_status` documentation to
  describe `approximated_pnl_nav`, `approximated_pnl_nav_clipped`,
  `approximated_pnl_nav_carried`, `deferred_pnl_nav` and
  `deferred_pnl_nav_outlier` rather than only `repaired_*`/`deferred_hf_*`
  statuses. Explicitly document that Hypercore `total_supply` is a synthetic
  index-unit quantity, not an actual share count, and confirm with a
  repository-wide consumer audit that nobody treats it as on-chain supply.
- Update `scripts/hyperliquid/README-hyperliquid-vaults.md` and
  `scripts/hyperliquid/README-hyperliquid-vaults-high-frequency.md` to separate
  raw scanner price from the cleaned daily-resolution economic index.
- Mark
  `docs/claude-plans/2026-07-14-hypercore-share-price-continuity.md` as
  superseded for cleaned-price construction. Its scanner continuity work
  remains useful for raw data quality, but its historical HF-anchor repair is
  no longer the published performance method.
- Correct or remove healing-script documentation that promises an exact share
  price from stored `daily_pnl`, flow backfills or API re-fetches.
- Include Python files, scripts, notebooks and documentation in the reference
  audit before deleting the old repair helpers.

## Out of scope

- Claiming an exact historical Hyperliquid investor share price.
- Reconstructing mark-to-market equity from fills, candles and funding.
- Rewriting the raw Parquet or either scanner DuckDB.
- Address-specific repair dates or allowlists.
- Adding a new database, schema migration or operational service.
- Renaming the widely consumed `returns_1h` column in this change.
