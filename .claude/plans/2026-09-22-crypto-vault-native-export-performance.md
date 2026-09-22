# Crypto vault native export performance plan

## Goal

Reduce the private crypto-bundle build time and memory use with a narrow,
low-complexity fast path for ETH- and BTC-family vaults. Qualification,
thresholds and the primary asset/performance metrics remain in the vault's
native denomination. No live or historical USD price may influence whether a
native vault is included. Preserve the existing optional additive USD metric
view only as a compatibility field calculated after native admission.

The hard native admission thresholds are:

| Family | Minimum lifetime peak `total_assets` |
|---|---:|
| BTC | `0.1` BTC-family units |
| ETH | `2.5` ETH-family units |

The existing public stablecoin export, stablecoin qualification behaviour and
generic metric functions are outside this change. The crypto bundle continues
to contain its current stablecoin records; this plan changes only how its
ETH/BTC rows are admitted and calculated.

## Production evidence

The production-shaped crypto Parquet inspected on 2026-09-22 contains
2,409,598 rows and 15,758 vaults. The current metadata build uses about 33 GB
RSS, has used about 7.5 GB swap, and normally takes roughly 1 hour 45 minutes to
2 hours after the top-vault JSON export.

A uniform 50-vault smoke test used 9,731 source rows and expanded them to
26,442 regular daily rows. The complete local metadata build took 19.67
seconds. Unprofiled phase timings were:

| Phase | Time |
|---|---:|
| Load, validate and build USD context | 1.82 s |
| Daily regularisation and returns | 7.75 s |
| Per-vault metrics and rankings | 8.39 s |

Profiling showed that `DataFrame.resample("D").last()` over the full 49-column
frame dominates daily regularisation. Object-heavy columns force Pandas into
`_aggregate_series_pure_python`; the profiled 50-vault run spent about 14.7
seconds below `resample().last()` and about 14.0 seconds in the Python fallback.
These cProfile timings include substantial instrumentation overhead and must
not be compared directly with the 7.75-second unprofiled wall time; they locate
the hot call stack rather than provide a second duration measurement.
The implementation retains every per-vault result frame until a final
`pd.concat()`, which explains the production memory high-water mark.

`calculate_lifetime_metrics()` then processes each vault serially. The profile
also found a fixed start-up cost from building three stablecoin lookup maps:
approximately 900 YAML parses across the slug, rate and depeg caches. That
lookup work is worth addressing later, but it is outside this ETH/BTC-only
change.

### Effect of the new native thresholds

The thresholds were evaluated directly against the same production Parquet,
using lifetime maximum `total_assets` in the stored native denomination:

| Family | All vaults | Qualifying | Skipped | All rows | Qualifying rows | Skipped rows |
|---|---:|---:|---:|---:|---:|---:|
| BTC | 693 | 403 | 290 | 106,450 | 83,366 | 23,084 |
| ETH | 2,270 | 1,198 | 1,072 | 357,517 | 282,740 | 74,777 |
| Total native | 2,963 | 1,601 | 1,362 | 463,967 | 366,106 | 97,861 |

The early threshold removes 46.0% of native vault groups and 21.1% of native
source rows before expensive calculations. Because stablecoin processing stays
unchanged, it reduces the whole combined input from 15,758 to 14,396 processed
vaults, not to 1,601. The implementation and rollout report must therefore
separate native-path speed-up from total crypto-bundle speed-up and must not
promise a 46% reduction in total wall time.

## Constraints and invariants

- Do not modify `calculate_hourly_returns_for_all_vaults()`,
  `calculate_lifetime_metrics()`, `calculate_vault_record()`, the public
  top-vault export or stablecoin filtering/qualification code.
- Do not add PyArrow, DuckDB or Numba dependencies. PyArrow is already present
  and may be used where it materially reduces allocations. Prefer ordinary
  Pandas when the operation is a single projected group-by and is already
  measured in seconds.
- Native qualification uses only the maximum finite, non-negative
  `total_assets` value for the vault. It must not read the exchange-rate
  Parquet, `periodic_metrics_usd`, fixed guideline USD rates or current market
  prices.
- The family classifier remains the existing reviewed symbol policy. A wrapper
  such as `wstETH` or `cbBTC` uses the threshold of its classified family, in
  the same denomination units used by `peak_total_assets` in its output record.
  This is a family-unit policy, not a wrapper redemption-price conversion:
  `2.5 wstETH` is compared as 2.5 observed units and is not converted to ETH.
- Native `period_results`, current/peak total assets and qualification
  threshold remain in native units. Existing JSON-only
  `periodic_metrics_usd` stays additive and is calculated only after native
  admission.
- Stablecoin rows and records must remain value-equivalent and retain their
  established ordering apart from generation metadata. Parquet recompression
  does not need to be byte-for-byte identical.
- The current R2 keys are flat and overwritten sequentially. Manifest-last is
  a commit marker for manifest-aware consumers, not atomic replacement of all
  fixed payload keys. If an upload fails, the old manifest remains and its
  digests will reject the partially replaced payload set; direct-key consumers
  can still observe an intermediate state, as they can today. Versioned payload
  keys would be a separate publication-protocol change and are out of scope.

## Design

### 1. Add explicit native threshold policy

Add immutable threshold constants in `eth_defi/vault/crypto_vaults.py`, close
to the crypto-bundle policy rather than the generic denomination classifier:

```python
CRYPTO_NATIVE_MIN_PEAK_TOTAL_ASSETS = {
    DenominationFamily.btc: Decimal("0.1"),
    DenominationFamily.eth: Decimal("2.5"),
}
```

Add a small typed helper that accepts vault metadata plus an `id`/`total_assets`
projection and returns qualifying and rejected native vault IDs with per-family
counts. Convert `total_assets` to a numeric type with invalid values becoming
null, reject negative and non-finite values, and use a grouped lifetime maximum.
Stablecoin IDs are not inputs to this helper.

Pin threshold comparison to the physical Parquet representation: convert
`total_assets` to `float64`, convert the `Decimal` policy value once with
`float(threshold)`, and qualify with `peak >= threshold`. Do not round and do
not convert each binary float through `Decimal`. Thus `float(0.1)` qualifies,
while `numpy.nextafter(float(0.1), -numpy.inf)` does not; `2.5` is exact. Keep
`Decimal` as the source-of-truth configuration and JSON policy value.

The first implementation should use a projected Pandas frame because the
production calculation over 2.4 million rows completed in a few seconds and is
easy to audit. If the focused benchmark shows this helper becoming material,
replace only its aggregation with `pyarrow.Table.group_by()`; do not introduce
DuckDB query construction or Numba for a one-column maximum.

### 2. Filter native vaults before expensive work and publication

Apply native admission immediately after the existing ETH/BTC cleaning and
daily materialisation, and before those rows are concatenated with the
unchanged stablecoin frame. The cleaned frame is authoritative: pre-cleaning
raw outliers must not qualify a vault. Filter both the private daily Parquet and
the later metadata input to the qualifying native IDs. This means the metadata
phase naturally reads the reduced Parquet; do not load the rejected full-width
rows a second time. This reduces R2 payload size as well as CPU and memory use.

Log one observable summary per run:

```text
Crypto native admission: BTC 403/693 vaults, ETH 1198/2270 vaults;
1362 vaults and 97861 daily observations excluded
```

Treat the native thresholds as hard admission rules. Existing native sticky
state must not retain a BTC/ETH vault that fails the new threshold, otherwise
the requested export reduction would not occur. Prune excluded native IDs from
the sticky state. Preserve the current sticky behaviour for stablecoin IDs
without modification.

The state ownership is already isolated: `CryptoVaultPaths.sticky_state_path`
resolves to `crypto-vaults/crypto-vault-export-state.json`, while the public
top-vault export resolves and saves its own sticky state. Add a regression test
that exercises both paths so this separation cannot be accidentally removed.

Replace the native record's old USD-guideline-derived
`qualification_threshold` with the exact family value (`0.1` or `2.5`). Add a
top-level, schema-versioned metadata field such as:

```json
"native_min_peak_total_assets": {"btc": 0.1, "eth": 2.5}
```

Keep existing stablecoin/USD-guideline fields for compatibility, but document
that they no longer determine BTC/ETH admission. Update the manifest metadata
and README so consumers can audit the native policy without reverse-engineering
the code.

Bump `CRYPTO_VAULTS_SCHEMA_VERSION` from 1 to 2 and implement an explicit
version-1 state migration. It must preserve stablecoin entries, preserve only
currently admitted native entries, and write version 2 atomically. Never treat
an old but valid state as corrupt or silently reset it. The current ETH
threshold is already 2.5 under the fixed guideline; the semantic threshold
change is BTC from approximately 0.08333333 to 0.1, plus removal of the native
sticky bypass. Call this out for consumers of `qualification_threshold`.

### 3. Avoid full-frame daily regularisation for native vaults

The ETH/BTC Parquet is already observation-preserving with at most one real row
per occupied UTC date. Native vault flow metrics are explicitly unavailable,
so they do not need the full consecutive state frame constructed for
stablecoin ERC-4626 flow estimation.

In `eth_defi/vault/crypto_vaults.py`, add a crypto-native batch helper that:

1. receives only the already-admitted ETH/BTC rows;
2. groups them by `id` without copying or resampling the full 49-column frame;
3. calls the existing, unchanged `calculate_vault_record()` once per vault;
4. passes the existing shared crypto USD conversion context so
   `periodic_metrics_usd` remains unchanged;
5. preserves the current per-vault exception isolation; and
6. returns the same metric-record DataFrame shape expected by
   `build_crypto_vault_record()`.

`calculate_vault_record()` already regularises the share-price Series needed
for return calculations through `prepare_daily_share_price_series()`. Calling
`calculate_hourly_returns_for_all_vaults()` first therefore performs a costly
full-frame daily resample that native vaults do not use for flow estimation.
The native helper must bypass that outer regularisation only after equivalence
tests prove that eligible ETH/BTC output fields are unchanged.

Before implementing the bypass, record the column-consumption audit in code
comments and tests. The current function reads the timestamp index and required
`id`, `chain`, `event_count`, `share_price` and `total_assets` columns, plus the
optional `utilisation`, `available_liquidity`, `leader_fraction`,
`leader_commission`, `account_pnl`, `follower_count` and `cumulative_volume`
columns. It does not read the outer helper's `returns_1h`. Native flow
estimation is disabled, so it does not require the outer helper's
`_vault_state_observed` or forward-filled state columns. Project precisely this
audited set for each native group and update the audit whenever
`calculate_vault_record()` gains a new input.

Do not calculate rankings for the native helper: `build_crypto_vault_record()`
deliberately clears all native ranking fields because mixed native units are
not comparable. Avoiding `calculate_vault_rankings()` is both simpler and
semantically exact.

Split `build_crypto_vault_metadata()` by family:

- stablecoin rows continue through the current
  `calculate_hourly_returns_for_all_vaults()` and
  `calculate_lifetime_metrics()` calls unchanged;
- admitted ETH/BTC rows use the new native helper; and
- concatenate the resulting record frames only at the compact one-row-per-vault
  stage before existing JSON conversion.

Construct one shared `StablecoinRateFeeder` and pass it explicitly to both the
unchanged stablecoin metrics call and the native helper. Splitting the paths
must not trigger a second set of approximately 900 YAML parses.

This leaves generic and stablecoin behaviour untouched while removing the
known object-heavy resample and meaningless ranking work from the native path.

### 4. Bound allocations in the native path

Project the native admission scan to `id` and `total_assets`. In the native
metric loop, keep the existing per-vault source slice and one output record;
do not accumulate per-vault regularised DataFrames. Release the rejected
native rows before stablecoin metric calculation begins so peak RSS can fall
before the largest remaining phase.

Use `groupby(sort=False, observed=True)` for native admission and metric groups.
Do not rely on Python allocator RSS returning immediately. The local benchmark
therefore measures wall time on identical inputs and leaves isolated memory
profiling to a process-level profiler. The unchanged stablecoin path may
continue to determine the complete process's 33 GB high-water mark.

Do not add joblib, threads or multiprocessing in the first implementation.
The measured problem is unnecessary allocation and Python fallback work, while
parallel Pandas groups would multiply memory pressure and complicate exception
ordering. Reconsider bounded threading only if the post-change profile shows
`calculate_vault_record()` as CPU-bound after the resample removal.

### 5. Add phase-level observability

Add start/end logs with elapsed seconds, vault counts and row counts for:

- native admission;
- unchanged stablecoin metrics;
- native metrics;
- JSON/state serialisation; and
- bundle publication.

Use `tqdm_loggable.auto` for the native per-vault loop, matching repository
requirements for long-running work. The scanner must emit progress at least
once per minute. This removes the current misleading silence after
`VAULT_JSON_PUBLISHED` without changing scheduling.

## Correctness tests

Extend `tests/vault/test_crypto_vaults.py` with focused fixture-based tests:

1. Exact boundaries under the pinned float64 comparison: `float(0.1)` BTC and
   `2.5` ETH qualify; `numpy.nextafter(threshold, -numpy.inf)` does not.
2. Native-only policy: stablecoin IDs are never filtered by the native helper.
3. No USD drift with a positive control: changing the exchange-rate fixture by
   a large factor does not change the admitted native ID set or native
   qualification thresholds, while `periodic_metrics_usd` demonstrably changes.
4. Wrapper families: reviewed ETH/BTC wrappers receive the family threshold
   while their exported amounts retain the observed denomination symbol/unit.
5. Invalid values: null, negative and non-finite peaks do not qualify; one bad
   vault does not exclude valid peers.
6. Parquet reduction: rejected native IDs are absent from the private price
   Parquet, while stablecoin rows and accepted native rows are unchanged.
7. Fast-path equivalence: for eligible ETH/BTC fixtures containing missing
   dates, duplicate source dates, utilisation and USD-rate gaps, compare the
   native helper with the old generic path. Assert equivalent native
   `period_results`, `periodic_metrics_usd`, current/peak assets, risk flags,
   fees and legacy return fields. Ranking fields remain null.
8. Sticky migration: a formerly sticky native vault below the hard threshold
   is removed; stablecoin sticky state retains its existing behaviour.
9. Failure isolation: one invalid admitted native vault is logged and skipped
   without suppressing other records or permitting a partial publication.
10. Manifest/schema: native threshold policy and reduced price/metadata counts
    are internally consistent.
11. Private state ownership: migrating/pruning
    `crypto-vaults/crypto-vault-export-state.json` cannot modify the public
    top-vault sticky state.

Keep generic metric tests unchanged. Do not weaken output assertions to make a
fast path pass; investigate every field difference and either preserve it or
document an intentional native-only schema change.

## Benchmarks and acceptance

Add a manual local benchmark script under `scripts/erc-4626/` using environment
variables only, following the existing script conventions. It must read local
production-shaped files, select a deterministic `N`-vault native candidate
subset, and report wall time, input/output rows and qualifying/rejected counts.
Both timed metric routes must receive the same qualifying rows; threshold
reduction is reported separately and must not be presented as algorithmic
speed-up. Select subsets by sorted SHA-256 digest of the vault ID, not source
row order, so repeated runs and machines choose the same vaults. It must never
write bundle data or upload.

Run these comparisons before merging:

1. deterministic `N=50` ETH/BTC smoke test for fast feedback;
2. deterministic `N=500` ETH/BTC benchmark to expose scaling;
3. full ETH/BTC production-data copy, including a normalised record-by-record
   diff between old and new output; and
4. complete combined crypto metadata build to show the end-to-end gain while
   stablecoin processing remains unchanged.

Acceptance criteria:

- all 1,601 production-snapshot qualifying native vaults are retained and all
  1,362 rejected native vaults are absent, subject to explicitly documented
  source-data changes after 2026-09-22;
- eligible native metric records match the old path in both focused tests and
  the full-snapshot diff, ignoring only generation timestamps, the intentional
  BTC threshold change, schema version and removed ranking values that were
  already nulled during serialisation;
- qualification remains identical when USD-rate inputs change;
- the native path is at least twice as fast as the old native path on both
  `N=500` and the full native snapshot. Measure both sides from native admission
  input through native record conversion, excluding shared Parquet loading,
  USD-context construction and unchanged stablecoin work;
- an isolated process-level memory profile may be used to assess allocations,
  but the sequential local benchmark must not claim comparable per-route peak
  RSS. Do not claim the stablecoin-dominated whole-process RSS will fall by the
  native-vault percentage;
- the benchmark records the complete-build result without claiming that the
  46% native-vault reduction applies to stablecoin work; and
- focused unit tests and `tests/erc_4626/test_post_processing.py` pass through
  the required `.local-test.env` wrapper.

Do not set a speculative total-runtime target until the unchanged stablecoin
share has been measured separately. If the complete build remains dominated by
stablecoin work, record that as the next optimisation target rather than
expanding this change's scope.

### Implementation measurements

The corrected same-input benchmark was run against the production-shaped local
snapshot on 2026-09-22. These timings measure only the metric routes after
admission; rejected candidates are reported separately and are not counted as
algorithmic speed-up.

| Candidate sample | Qualifying vaults | Qualifying rows | Old route | Projected route | Speed-up |
|---:|---:|---:|---:|---:|---:|
| 50 | 26 | 5,986 | 10.623 s | 0.542 s | 19.60× |
| 500 | 274 | 60,614 | 54.019 s | 5.554 s | 9.73× |

The 50- and 500-candidate samples rejected 24 and 226 vaults respectively. A
separate full admitted-native run generated 1,601 metric records from 366,106
rows in 43.2 seconds, but no same-input old-route full run was completed, so no
full-native multiplier is claimed. These measurements do not include the
unchanged stablecoin phase and must not be presented as end-to-end bundle
speed-up.

## Implementation sequence

1. Verify and document the fixed-key manifest consistency contract, private
   sticky-state ownership, numeric comparison semantics and exact
   `calculate_vault_record()` column-consumption audit before changing output.
2. Add phase timers and the deterministic benchmark; capture old-path `N=50`,
   `N=500` and full-native baselines. Measure the YAML parsing cost explicitly
   to ensure the split does not duplicate it.
3. Add native threshold constants, admission helper and boundary/no-USD tests.
4. Filter rejected native IDs before the combined Parquet is written; update
   schema version, version-1 sticky-state migration, metadata, manifest and
   documentation.
5. Add the column-projected native batch helper that reuses
   `calculate_vault_record()` without outer full-frame resampling or rankings,
   and shares one `StablecoinRateFeeder` with the stablecoin path.
6. Add fixture equivalence, failure-isolation and full-production-snapshot
   normalised record-diff gates.
7. Run focused tests, format with Ruff, run all four benchmarks, and record the
   before/after table in the pull request description.
8. Run the existing standalone local build with
   `CRYPTO_VAULTS_PUBLISH=false`; inspect Parquet/JSON/manifest consistency.
9. On the production host, run one non-publishing smoke build against a copy of
   current production inputs before deployment. Do not overwrite the live
   bundle during this validation.

## Rollback and follow-up

Keep the old generic route callable from tests during implementation so output
equivalence remains easy to verify, but do not add a permanent environment
switch unless production validation uncovers a concrete need. Rollback is a
normal code revert. A local build failure publishes nothing. During R2 upload,
the old manifest remains the authoritative commit marker until the new manifest
is uploaded last; manifest-aware readers reject mismatched payload digests.

Likely follow-up work, deliberately excluded here, is optimising or reusing the
stablecoin metrics already produced by the public export, combining the three
stablecoin YAML scans, and applying column projection/vectorised aggregation to
the generic path. Those changes can have larger end-to-end impact but need
their own compatibility review.

## Kimi Max review

Kimi K2.8 Preview reviewed the complete initial plan at its configured maximum
reasoning effort after the plan was written. Its verdict was “approve with
conditions”. This revision incorporates its blocking findings and optimisation
ideas:

- corrected the overstatement that flat-key manifest-last publication is fully
  atomic;
- verified the crypto sticky state is private and added an ownership regression
  test;
- pinned BTC float64 boundary semantics;
- added the explicit metric-column audit and full production record-diff gate
  before bypassing outer regularisation;
- added column projection, `sort=False`, shared lookup caching and deterministic
  benchmark selection; and
- narrowed performance and memory acceptance criteria to measurable native
  work while reporting whole-bundle results honestly.

Kimi also proposed a two-pass projected Parquet read. The chosen implementation
gets the same benefit with less I/O by filtering the freshly cleaned/daily
native frame before writing the combined Parquet, so the metadata phase reads
only admitted native rows. If a future entry point must consume an older
unfiltered crypto Parquet, use a projected `id`/`total_assets` first pass and a
filtered, column-projected second pass there.
