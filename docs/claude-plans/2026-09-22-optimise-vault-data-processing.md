# Optimise vault data processing

## Status

The first high-impact implementation pass covers selected work from phases
0–6 and was reviewed against the 2026-09-22 production log. It does not mark
every proposed item in those phases complete. In particular, a complete
same-input scanner cycle, stage-local peak-memory measurements, removal of the
ETH/BTC temporary Parquet hand-off and phase 7 native attachment work remain
follow-ups. The results below distinguish comparable measurements from
capacity measurements taken on different production-format copies.

This document plans the work; it does not authorise a destructive migration of the production Parquet files. Benchmark and equivalence runs must use copies of production data.

## Why

The post-scan cleaning and merge stage took 19 minutes 30 seconds before upload on 2026-09-22. The time is concentrated in Python-side transformation work rather than Parquet input/output or settlement annotation:

| Stage | Production elapsed | Share of measured post-processing | Rows or observations |
|---|---:|---:|---:|
| Native protocol price merge | 6m 35s | 34% | 1,894,949 replacement partition rows; 22,552,978 total raw rows |
| Stablecoin cleaning | 11m 25s | 58% | 22,552,978 raw rows to 10,116,623 cleaned rows |
| Crypto cleaning and bundle construction | 1m 30s | 8% | 2,958,819 selected raw rows; 2,409,598 combined output rows |
| **Total** | **19m 30s** | **100%** | |

The native replacement count above is the size of overlapping replacement
partitions, not the number of newly collected observations. Dataset growth
must be calculated from the raw Parquet row count before and after replacement.

The largest individually visible delays were:

| Operation | Production elapsed | Evidence |
|---|---:|---|
| ApeX observation preparation | 3m 23s | 217,070 selected price entries |
| Hypercore observation preparation | 2m 22s | 1,600,267 selected price entries |
| Stablecoin share-price outlier repair | 3m 57s | 8,535,236 EVM rows; 6,555 repaired rows |
| Stablecoin metadata, denomination selection and initial sort | about 2m 14s | 22,552,978 raw rows reduced to 10,338,606 stablecoin rows |
| Uninstrumented work after outlier log and before outlier-return log | about 3m 15s | Includes the end of the group operation, EVM reassignment, return calculation and early return cleaning |
| Stablecoin inactive lead-time removal | 42s | 10,338,606 to 10,135,503 rows |
| Crypto share-price outlier repair | 43s | 2,912,298 rows |
| Raw Parquet rewrite and verification | 35s | 22,552,978 rows, 333 MB temporary file |
| Settlement annotation | 10.6s | 10,116,623 price rows and 11,486 settlement rows |

Representative local measurements against the available production-format files support the same ranking:

- Reading the 22,293,343-row raw Parquet file took about 1.64 seconds.
- Reading ApeX observations took about 0.35 seconds, while `derive_perp_vault_metric_snapshots()` took about 172.57 seconds for 186,115 account rows and no position rows.
- Reading Hypercore high-frequency observations took about 0.51 seconds, while snapshot derivation took about 56.67 seconds for 58,697 account rows and 475,450 position rows.

The first optimisation pass must therefore target algorithmic Python and Pandas overhead. Parquet compression, final verification and settlement annotation are measurable but are not the highest-impact starting points.

## Goals

- Reduce the same-data post-processing run from 19m 30s to less than 9 minutes as an initial target, not a promise.
- Reduce native protocol merge from 6m 35s to less than 3 minutes.
- Reduce stablecoin cleaning from 11m 25s to less than 5 minutes.
- Reduce crypto cleaning and bundle construction from 1m 30s to less than 45 seconds.
- Preserve the exact logical output unless a separately reviewed correctness fix deliberately changes it.
- Improve observability so every future regression can be assigned to a named stage without inferring it from gaps between log messages.
- Keep peak resident memory bounded and record it alongside elapsed time. A faster implementation that creates unsafe production memory pressure is not acceptable.

The initial time budget has little spare capacity and must be rebased after Phase 0 attributes the 3m 15s logging gap:

| Budget item | Target |
|---|---:|
| Native merge | 3m 00s |
| Stablecoin cleaning | 5m 00s |
| Crypto cleaning/bundle | 0m 45s |
| Subtotal | 8m 45s |
| Unallocated headroom | 0m 15s |
| Overall target | less than 9m 00s |

If instrumentation assigns material work outside these stages, reduce a stage target or revise the total explicitly rather than hiding the overrun in an unlabelled gap.

## Non-goals

- Do not start by redesigning the entire historical storage layout.
- Do not optimise the 10.6-second settlement annotation before multi-minute hotspots.
- Do not remove Parquet verification or weaken schema checks.
- Do not silently discard or regenerate existing data after a schema, migration or read failure.
- Do not reset reader state or replace historical Monad rows that the current provider cannot reconstruct.
- Do not add parallelism before removing avoidable Python loops, repeated grouping and full-frame copies.

## Correctness and safety gates

Every phase must pass these gates before it is allowed into the production path:

1. Run the old and new implementations against the same immutable copy of the production metadata, DuckDB and Parquet inputs.
2. Compare logical Arrow tables, not compressed Parquet bytes. File bytes and row-group layout may change while values remain identical.
3. Check schema, row count, column order, row identity, timestamp ordering, null placement, dtypes and values. Preserve exact Decimal aggregation until the existing, explicit float64 Parquet-contract conversion; do not introduce an earlier lossy conversion.
4. Compare correction conflict behaviour as well as the happy path. Equal-rank conflicting perpetual bundles must still raise a hard error.
5. Record peak RSS, wall-clock time, CPU time, input/output counts and rows per second for both implementations.
6. Keep comparison code outside the long-lived production path. The previous
   implementation remains available from the parent Git revision for offline
   shadow runs; the production module should expose one maintained algorithm.
7. Use temporary output paths and atomic replacement. Never benchmark by overwriting the live production Parquet file.

The Phase 0 comparison oracle must define logical equality rather than relying on default Pandas equality. Canonicalise both results to the production Arrow schema, compare NaN with NaN, distinguish Arrow null from IEEE NaN wherever the current writer distinguishes them, preserve signed zero unless the current schema normalises it, normalise timestamp units explicitly, and compare dictionary-encoded and plain strings by decoded values. Use this single oracle for every phase.

Sorting must use binary string collation and a stable tie-break based on original row position. This invariant applies to Pandas, DuckDB and PyArrow implementations and is especially important for mixed-case addresses and duplicate timestamps.

## Execution-engine policy

Pandas is not a constraint. Use the engine that best matches each operation, with a small representative benchmark followed by a production-copy benchmark:

- **DuckDB** is preferred for scanning Parquet or DuckDB data with projection and predicate pushdown, window functions, grouped aggregation, joins and external sorting. It is the leading candidate for correction fast-path selection and reading only selected denomination rows. Preserve PEP 440 version comparison and semantic conflict checks in Python where SQL ordering would alter meaning.
- **PyArrow** is preferred for zero-copy interchange, schema-preserving projection/filter/take operations, dictionary encoding and writing the final Parquet table. It is the leading candidate for filtering or concatenating large tables without materialising object-heavy Pandas frames.
- **Numba** is a contingency for genuinely numerical, contiguous-array kernels that fail the target when expressed with DuckDB, PyArrow compute or NumPy. The inactive-lead and outlier operations are both primarily boundary-aware gathers and masks, so benchmark NumPy first. If Numba is still justified, measure first-call JIT cost separately from steady-state cost, enable caching where safe, account for one-shot container startup and provide a clear non-JIT error or fallback policy.
- **Pandas/NumPy** remains appropriate for metadata manipulation and small exceptional sets. Vectorised `map`, `take`, masks and grouped transforms are acceptable when they benchmark competitively and keep the code simpler.

Numba currently appears only as a transitive locked dependency, not a declared project dependency. If selected, add it explicitly to `pyproject.toml` with the repository-required comment explaining why it is needed, place it in the appropriate extra used by production, update the lock file and verify Python 3.14 wheels in the deployment environment. Do not make production correctness depend accidentally on a transitive package.

## Documentation and observability requirements

Optimised data code is harder to understand than the current direct Pandas implementation. Be deliberately verbose in Sphinx-style docstrings and targeted line comments.

For every materially optimised function:

- Explain the business invariant and why the chosen algorithm or execution engine preserves it.
- Explain any sort order, group-boundary, null, duplicate-timestamp, Decimal or correction-tie assumption.
- Add line comments around non-obvious vectorised, SQL, Arrow or Numba operations. Comments must explain *why* the operation exists and what failure it prevents, rather than narrating syntax.
- Document expected DataFrame, Arrow table or array columns and dtypes, plus arguments and return values.
- Include a `Performance history` section in the docstring. It must state the baseline date, input fingerprint or row counts, old elapsed time, new elapsed time, speed-up and peak RSS. During implementation an explicit `TBD` placeholder is acceptable; completed code must replace it with a measurement or state clearly that the old stage was not isolated.
- Keep the historical baseline after filling the new result. These old and new metrics are a future reference point for detecting regressions and understanding why more complex code exists.

Example docstring content, adapted per function:

```python
"""Select the canonical perpetual observation for every correction group.

The common case is selected with a vectorised latest-write fast path. A
validation pass preserves the existing hard failure for every invalid PEP 440
collector version, while PEP 440 ranking and semantic bundle comparison are
limited to tied rows because SQL lexical version ordering is not equivalent.

Performance history
-------------------

Baseline (2026-09-22, ApeX production-format copy): 186,115 account rows, zero
position rows, 172.57 seconds for snapshot derivation. A later
production-format copy took 11.65 seconds for 210,200 account rows and peaked
at 548 MiB. Because the input copy and row count differ, this demonstrates
capacity but is not a like-for-like speed-up measurement.

:param accounts:
    ...
:return:
    ...
"""
```

Add monotonic stage timers around at least:

- each native database read, correction selection, position aggregation, price attachment and partition append;
- metadata index construction, metadata mapping, denomination filtering and sorting;
- inactive lead-time removal;
- each Hypercore-specific transform;
- EVM outlier preparation, kernel execution and reconstruction;
- return calculation and every return-cleaning substage;
- stablecoin daily materialisation and crypto bundle concatenation;
- Arrow conversion, Parquet write and verification.

Each timer log must include stage name, elapsed seconds, input rows and output rows. Actions expected to exceed one minute must emit progress or substage output within one minute.

## Phase 0: establish reproducible measurements

### Changes

1. Add the focused, environment-driven benchmark script
   `scripts/erc-4626/benchmark-vault-price-cleaning.py`, using the existing
   logging conventions and no command-line parser.  It has been smoke-tested
   on the Hemi fixture and emits JSON with the input schema fingerprint,
   wall/CPU time, peak RSS and output counts.
2. Accept input paths through environment variables and refuse to use the same path for input and output.
3. Record a dataset fingerprint containing file size, modification time, schema, row count and relevant DuckDB table counts. Avoid hashing hundreds of megabytes on every normal scan.
4. Emit a machine-readable JSON result and a concise phase table containing wall time, CPU time, peak RSS, row counts and throughput. On Linux, record cumulative peak RSS with `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss`, documenting its KiB units, and sample current RSS from `/proc/self/status` around named stages where a stage-local view is useful.
5. Benchmark warm and cold/JIT cases separately if Numba is evaluated.
6. Add the stage timers listed above to the normal production log.
7. Measure the known peak-memory sites independently: the wide 22.5-million-row sort, mixed Hypercore/EVM slice and reassignment, final cleaned sort, and crypto stablecoin reread.

### Acceptance

- The baseline script reproduces the broad production ranking.
- Repeated warm runs on the same host have sufficiently low variance to distinguish a material improvement; record the median of at least three focused runs for small kernels and one or more full production-copy runs.
- Peak RSS on the production copy does not exceed the current implementation. Phases 2 and 4 must reduce their known wide-frame peaks rather than merely stay equal.
- There are no wall-clock performance assertions in ordinary CI because shared runners are noisy.

## Phase 1: eliminate perpetual correction-selection loops

### Baseline issue

`eth_defi/perp_dex/parquet.py::select_perp_observation_corrections()` iterates over every correction group in Python. For every group it allocates filtered frames and maps `packaging.version.Version`. Tied candidates repeatedly filter the complete positions frame by `snapshot_id`, and `_semantic_bundle_signature()` uses `iterrows()`.

The local ApeX measurement—172.57 seconds for 186,115 account rows with no position rows—shows that correction selection, not I/O, dominates the 3m 23s production stage.

### Proposed design

1. Build a vectorised common path that selects the greatest `written_at` for each identity/effective-time group using either:
   - a DuckDB `row_number()`/`max()` window over projected account columns; or
   - Pandas/Arrow grouped indices if that proves faster after conversion overhead.
2. Detect groups tied at the latest write timestamp without doing per-group Python work.
3. Preserve the current fail-fast validation of every latest-row `collector_version`, including singleton groups. Perform PEP 440 *ranking* only for tied latest-write candidates; do not replace it with lexical SQL ordering.
4. Detect equal winning-version ties, then run semantic bundle comparison only for this exceptional subset.
5. Build a `snapshot_id`-keyed positions index once, or use a semi-join to fetch only tied snapshots. Never scan the entire positions frame once per candidate.
6. Replace `iterrows()` signatures with deterministic tuples/hashes constructed from column arrays sorted by binary `source_market_id`. Canonicalisation must reproduce the current `str()` representation for Decimal exponents, timestamps, NaN and other stored dtypes. A hash may narrow candidates, but a collision-safe exact comparison must decide whether bundles conflict.
7. Push position aggregation into DuckDB only if `DECIMAL(38, 18)` scale and overflow behaviour matches the existing Python `Decimal` aggregation through the explicit float64 output conversion. Validate against the production positions table and define a hard failure or exact Python fallback for an overflowing group; never truncate silently.
8. Change `read_perp_vault_observations()` from `SELECT *` to named projections for the common selection and aggregation path. Fetch the extra account and position columns required for semantic signatures only for equal-rank ties, or prove that one wider projected read is faster.
9. Define selected-row ordering explicitly. Either preserve current group-first-appearance order or canonicalise it and prove all consumers sort independently; never rely on unspecified DuckDB query order.

### Tests

- No corrections and one observation per group.
- A later write wins.
- PEP 440 version ordering decides a same-write tie, including versions whose lexical order differs.
- An invalid latest-row PEP 440 version still raises for a singleton correction group.
- Semantically equal duplicate bundles select deterministically by `snapshot_id`.
- Equal-rank conflicting account data raises.
- Equal-rank conflicting position data raises.
- Zero positions and unavailable/incomplete position states preserve existing null/zero semantics.
- A production-copy old/new comparison matches selected snapshots and derived metrics exactly.
- Selected-row ordering follows the documented contract rather than DuckDB's incidental output order.

### Acceptance

- Target ApeX snapshot derivation: below 10 seconds from the 172.57-second local baseline.
- Target Hypercore high-frequency derivation: below 15 seconds from the 56.67-second local baseline.
- Any chosen DuckDB query has its ordering and null semantics documented beside the SQL.

## Phase 2: filter before enriching and sorting raw prices

### Baseline issue

`process_raw_vault_scan_data()` currently:

1. materialises missing vault-state columns across all 22.5 million raw rows;
2. normalises `deposit_closed_reason`, including an Arrow-to-object-to-string round trip;
3. constructs `id` and maps names over all rows;
4. maps `event_count` and `protocol` over all rows with Python lambdas;
5. finds unique IDs and, for each missing ID, rescans the full frame for logging context;
6. converts and indexes timestamps;
7. sorts the full wide frame; and only then
8. filters to the requested denomination families.

The stablecoin run discards more than half the rows after paying enrichment and sort costs.

### Proposed design

1. Build a compact metadata relation once per run with canonical `id`, name, protocol, event count, denomination family and any later-required fields.
2. Preserve `assign_unique_names()` mutation and duplicate-name ordering exactly, but perform row enrichment with vectorised `Series.map`, a validated join, or a DuckDB/Arrow dictionary lookup instead of Python lambdas.
3. Prefer a typed `(chain, address)` metadata join or Arrow/DuckDB semi-join so the pipeline does not concatenate a 22.5-million-row string ID before filtering. Preserve the current case-sensitive match against `VaultSpec.as_string_id()` unless a separately reviewed normalisation changes it.
4. Push the filter and column projection into `pyarrow.dataset` or DuckDB `read_parquet()` if the canonical chain/address identifier can be joined without changing address/string semantics. Benchmark this against one Arrow read followed by `is_in`; conversion overhead can outweigh SQL benefits on already resident frames.
5. Sort only the filtered frame. Preserve priority IDs, stable ordering and duplicate timestamp ordering.
6. Avoid materialising object strings repeatedly. Evaluate Arrow string and dictionary-encoded metadata columns, but verify downstream Pandas compatibility before adopting them.
7. Move `ensure_vault_state_columns()` and `derive_deposit_closed_reason()` after denomination filtering unless a required filter/join column is missing. This avoids carrying roughly 30 default columns through the full raw-frame sort and copy. Prove that output defaults and columns are identical.
8. Replace `check_missing_metadata()` with one anti-join and one grouped context summary for missing IDs. Preserve the invariant that missing rows are removed before the unguarded `event_count` lookup.
9. After filtering, create the exported `id` once, factorise it once to integer group codes, and carry the codes through every row mask so inactive-lead removal, outlier repair, returns and TVL cleaning can reuse them or their cached group boundaries. Recalculate only if a stage reorders rows. Measure this cross-stage reuse because its magnitude is currently a hypothesis.
10. Benchmark hot-column conversion after filtering: retaining Arrow dtypes versus converting only numerical kernels to contiguous NumPy arrays. Explicitly preserve Arrow-null versus IEEE-NaN behaviour.

### Tests

- Exact equality for stablecoin, ETH/BTC and mixed denomination selections.
- Duplicate and empty vault names retain current output.
- Missing metadata rows are logged and removed exactly once.
- Native and EVM identifiers preserve existing canonical formatting.
- Mixed-case raw addresses preserve the current match/drop behaviour.
- Priority vault ordering and duplicate timestamp order remain unchanged.
- Output dtypes and nulls match the baseline.

### Acceptance

- Reduce the production metadata/filter/sort segment from about 2m 14s to below 30 seconds.
- Demonstrate that filtering occurs before the large sort and denormalised-column expansion.
- Demonstrate that missing/default vault-state columns are materialised only after filtering and that integer group codes are reused rather than refactorising string IDs in each stage.

## Phase 3: vectorise inactive lead-time removal

### Baseline issue

`remove_inactive_lead_time()` uses a DataFrame-returning `groupby().apply()` across millions of rows. This incurs one Python callback and frame reconstruction per vault and took 42 seconds in the stablecoin run.

### Proposed design

1. Express the current rule as group-local array positions: find the first usable supply value and the first later position where supply changes according to the existing zero/NaN semantics.
2. Compute group boundaries once on data already sorted by `id` and timestamp.
3. First benchmark a NumPy/Pandas transform implementation.
4. Use Numba only if the production-copy NumPy implementation demonstrably misses the target. If selected, implement a small kernel over contiguous supply values, null masks and group offsets, return one Boolean keep mask and apply it once to the frame.
5. Keep the kernel independent of Pandas indices so duplicate timestamps cannot collapse rows.

### Tests

- Existing fixture coverage plus zero, null, nullable Arrow dtype, one-row group, never-changing supply, first-row change and duplicate timestamps.
- Duplicate timestamps straddling the first-supply-change boundary.
- Property-style comparison of old and new masks for generated sorted groups.
- Exact production-copy keep-mask equality.

### Acceptance

- Reduce the stablecoin stage from 42 seconds to below 10 seconds.
- Document the group-boundary arrays and precise interpretation of missing supply values.
- Do not introduce Numba unless the measured NumPy implementation misses the target by enough to justify dependency and JIT-cache complexity.

## Phase 4: replace groupby/apply outlier repair with an array kernel

### Baseline issue

`fix_outlier_share_prices()` performs a DataFrame `groupby().apply()`. Every group is forward-filled, temporary columns are allocated, and each abnormal position is processed through `iloc`/`iat` in Python. The mixed Hypercore/EVM path then assigns a large repaired subframe back with aligned `.loc`. The stablecoin outlier stage took 3m 57s and the crypto version took 43 seconds.

The timestamped log is emitted before MultiIndex reconstruction and before the repaired EVM frame is assigned back, so part of the following 3m 15s gap may still belong to outlier reconstruction rather than return calculation.

### Proposed design

1. Add finer timers before changing the algorithm so the forward-fill, median interval calculation, candidate construction, repair kernel, group reconstruction and mixed-frame assignment are measured separately.
2. Preserve a stable integer row-position column. Never use timestamp labels as row identity because duplicate timestamps are valid.
3. Make removal of the per-group full-frame `ffill()` the primary design decision. Extract only the columns needed by the cleaner and forward-fill the price/state arrays whose existing behaviour depends on it, while preserving unknown interval-flow fields. Time forward-fill independently because its fixed per-vault cost is likely larger than the 6,555-row repair loop.
4. Calculate group boundaries and median polling intervals once. Derive each vault's effective look-back and look-ahead row offsets using the current rounding and fallback rules.
5. Implement the variable-offset candidate lookup as a cross-group-safe NumPy gather: clip each row's look-back/look-ahead target to its own group boundary, use `take`, then apply one elementwise repair mask. Evaluate a cached Numba kernel only if this misses the target materially.
6. Ensure candidate positions never cross a vault boundary. Preserve current boundary `bfill`/`ffill` candidate semantics exactly, including genuine-crash decisions.
7. Return only the repaired `share_price` array, raw price audit column and repair count, then update by integer row positions. Avoid reconstructing and assigning every EVM column.
8. Keep Hypercore outside the generic outlier kernel, as its mixed cadence is handled by the economic index logic.

### Tests

- Hourly, daily, weekly and irregular vault cadences.
- Duplicate timestamps and duplicate prices.
- Short groups below the interval-estimation threshold.
- Null/zero values at each boundary.
- A missing current share price whose existing group-local forward fill affects later candidates.
- Sparse state values and flow columns that must remain unknown rather than forward-filled.
- A temporary spike followed by recovery is repaired.
- A genuine crash without corroborating recovery is retained.
- Exact old/new comparison on a representative production-copy sample and then the full selected production frame.
- Property-style old/new comparison over generated group sizes, cadences, offsets, missing values and duplicate timestamps.
- If Numba is selected, compare first-call compilation, cached cold-process startup and steady-state execution.

### Acceptance

- Reduce stablecoin outlier repair from 3m 57s to below 45 seconds.
- Reduce crypto outlier repair from 43 seconds to below 10 seconds.
- No full-frame group callback or per-abnormal-row Pandas indexing remains.
- Mixed Hypercore/EVM reconstruction updates only integer row positions and changed audit/price columns; it must not slice and reassign the full-width EVM frame by duplicate timestamp labels.
- Do not introduce Numba unless the measured NumPy implementation misses the target by enough to justify dependency and JIT-cache complexity.

## Phase 5: remove redundant return and reconstruction work

### Baseline issue

`calculate_vault_returns()` derives both a grouped shifted prior price and a grouped `pct_change()`, causing repeated grouping. The mixed protocol path reconstructs a large frame before returns are calculated. The production log does not separate this work from adjacent cleaning stages.

### Proposed design

1. Derive the previous share price once per `id` and calculate the raw return by direct vectorised division, preserving the existing invalid-price filtering and repeated-zero behaviour.
2. Pass the caller's logger so invalid-row output is timestamped consistently.
3. Retain stable row-position identity from phase 4 and merge Hypercore/EVM share prices by array position, not index-aligned whole-frame assignment.
4. Remove the full `outlier_returns.sort_values()` used only before count logging. The group count does not depend on row order, so this is pure work on a wide subset.
5. In `clean_by_tvl()`, map `avg_assets_by_vault` once and derive `dynamic_tvl_threshold` from the stored result. For denomination-specific mappings, use the phase 2 factorised group codes and a per-code threshold array instead of per-row Python dictionary lookup.
6. Reuse the phase 2 integer group codes for the previous-price shift, TVL mean and next-row mask expansion rather than repeatedly hash-factorising the string `id` column.
7. Time `finalise_perp_metric_columns()` and its full-frame normalisation. Add a semantics-preserving fast path for frames with default perp metrics if measurement confirms it is material; native rows and invalid statuses must still receive full validation.
8. Avoid repeated sorts: establish and assert the required `(id, timestamp, stable original position)` order once, then let downstream functions declare whether they preserve it.

### Tests

- Exact prior price, return and invalid-row removal equivalence.
- NaN, zero, infinite and negative share-price cases.
- Duplicate timestamps and interleaved Hypercore/EVM inputs.
- Existing outlier-return threshold and low-TVL suppression behaviour.
- Bit-exact `returns_1h` values, including first rows and repeated zero prices, under Pandas 2.3.3 semantics.
- Default-only and native populated perpetual metric columns both retain validation and status behaviour.

### Acceptance

- Attribute the previously uninstrumented 3m 15s gap to named substages.
- Reduce return calculation itself to one grouped shift and array arithmetic.
- Remove the logging-only wide sort and duplicate average-TVL map even if their individual timings are below the phase threshold, because neither has a semantic purpose.

## Phase 6: avoid rereading and regrouping stablecoin output for crypto bundles

### Baseline issue

`build_crypto_vault_prices()` reloads the 10,116,623-row cleaned stablecoin Parquet, filters it, materialises daily rows, then separately cleans ETH/BTC raw data and concatenates/sorts the bundle. This repeats work immediately after the stablecoin cleaner held the same frame in memory.

### Proposed design

1. Materialise the daily stablecoin sidecar from the in-memory cleaned frame during the primary cleaning run, or return an explicitly typed result object containing the hourly output and optional daily materialisation. Extend the same result object to the ETH/BTC leg so it does not write and immediately reread a temporary Parquet file merely to pass a frame to its caller.
2. Write the sidecar atomically and let crypto bundle construction read the much smaller daily file.
3. If memory pressure makes in-memory daily materialisation unsafe, use DuckDB or PyArrow to stream the just-written Parquet with column projection and grouped daily selection rather than loading every column into Pandas.
4. Continue to clean ETH/BTC independently because its denomination thresholds and output cadence differ.
5. Avoid a final Pandas-wide sort if DuckDB external sort or Arrow sort can produce the required ordering with lower peak memory.
6. Preserve current ordering: settlement annotation happens before daily materialisation, so the daily sidecar includes `vault_settlement_at`. Any deliberate change requires a separate compatibility decision.
7. Both stablecoin and ETH/BTC daily frames are already sorted. Benchmark a two-way key merge or a key-only PyArrow `sort_indices` followed by `take` instead of sorting the concatenated wide frame.

### Tests

- Daily stablecoin sidecar equals current `materialise_daily_crypto_prices()` output.
- Daily selection preserves the stable `tail(1)` tie-break for duplicate timestamp and block-number rows within one UTC day, and retains settlement annotation.
- Combined crypto bundle has identical schema, ordering, values and row count.
- Interrupted sidecar write cannot replace a valid existing file.
- Empty stablecoin or ETH/BTC inputs remain supported.

### Acceptance

- Reduce crypto cleaning and bundle construction from 1m 30s to below 45 seconds.
- Demonstrate lower or equal peak RSS than the existing double-load path.

## Phase 7: revisit native attachment and raw Parquet rewrite only after profiling

The final native partition attachment, sort, write and verification took about 35 seconds for 22.5 million rows. This is meaningful but smaller than correction selection and cleaning.

After phases 1–6:

1. Measure `attach_perp_metrics_to_price_rows()` separately. It performs sorts, `merge_asof`, a forward-alignment merge and a final restoration sort.
2. Consider a DuckDB `ASOF JOIN` or window-based attachment if it preserves exact identity, duplicate and forward-alignment semantics.
3. Keep raw rows as Arrow tables through partition replacement, using `pyarrow.compute` filters and `take` where practical.
4. Evaluate dictionary encoding and row-group sizing only with read/write and downstream compatibility measurements.
5. Do not remove verification to save time.
6. Replace final original-order restoration after `merge_asof` with an inverse permutation and `take` if it benchmarks faster than a full sort.
7. Verify whether all cleaning operations preserve `(priority, id, timestamp, original position)` order. If so, replace the final 10-million-row cleaned-frame sort with a key-only permutation or a move of the small priority-vault block; retain the full sort if any invariant check fails.

Target this phase only if it remains at least 10% of the optimised end-to-end run.

## Deferred architecture: incremental or partitioned cleaning

A partitioned incremental cleaner could eventually avoid reprocessing years of unchanged history. It is intentionally deferred from the first optimisation pull request because several operations depend on history:

- all-time average TVL influences low-TVL return suppression;
- Hypercore economic share-price reconstruction crosses scan boundaries;
- outlier repair needs a look-back and look-ahead overlap;
- correction and recapitalisation rules can replace prior effective-time data;
- schema and reader-state mistakes can destroy irreplaceable history, especially on Monad.

Before implementing incremental cleaning, define per-vault or per-chain partitions, stable aggregate state, correction invalidation, overlap windows, atomic manifest replacement and a full-rebuild equivalence oracle. A safe faster full rebuild from phases 1–6 is a prerequisite.

Also defer optimisation of `replace_cleaned_vault_histories()`: its per-batch Python chain of `(chain, address)` comparisons should eventually become an Arrow hash membership operation or DuckDB semi-join, but it is not part of the measured normal production path.

## Implementation order

1. Phase 0 instrumentation and benchmark harness.
2. Phase 1 perpetual correction selection and aggregation.
3. Phase 2 early filtering and vectorised metadata enrichment.
4. Phase 3 inactive lead-time mask.
5. Phase 4 share-price outlier kernel.
6. Phase 5 returns and frame reconstruction.
7. Phase 6 crypto daily sidecar.
8. Re-profile, then perform phase 7 only if still material.
9. Run a full production-copy equivalence and benchmark pass.
10. Replace all implementation `TBD` performance-history fields with measured
same-input results before merge; if the old log did not isolate a stage, record
that limitation rather than inventing a speed-up.

After the Phase 0 baseline is captured, the logging-only outlier sort and duplicate average-TVL map may land as early, semantics-neutral commits. Their before/after measurements must still be retained.

Each numbered optimisation should be a reviewable commit or small pull request where feasible. Do not combine a correctness change with a performance rewrite unless the old output is demonstrably wrong and the behavioural change is documented separately.

## Verification commands

Use the repository's Poetry environment and secrets wrapper for focused tests. Copy `.local-test.env` from the main checkout first if the worktree lacks it.

```shell
source .local-test.env && poetry run python scripts/erc-4626/benchmark-vault-price-cleaning.py
source .local-test.env && poetry run pytest tests/research/test_clean_prices.py --log-cli-level=info
source .local-test.env && poetry run pytest tests/research/test_vault_base_usdc_yield_dynavault_v3.py --log-cli-level=info
source .local-test.env && poetry run pytest tests/perp_dex/test_metrics.py --log-cli-level=info
source .local-test.env && poetry run pytest tests/vault/test_crypto_vaults.py --log-cli-level=info
```

Add focused tests for `eth_defi/perp_dex/parquet.py` in the existing relevant test module or a new function-based test module, then run only those tests. Run formatting before opening or updating a pull request:

```shell
poetry run ruff format
```

Do not use full-suite timing as the performance benchmark, and do not build Sphinx documentation locally.

## Rollout

1. Land instrumentation first so old production behaviour produces comparable stage metrics.
2. Use a temporary comparison switch only in an isolated benchmark when a
   phase still needs a production shadow run. The production outlier module
   now has one path; compare it with the parent Git revision rather than
   reintroducing a permanent legacy switch.
3. Run one shadow comparison that writes old and new outputs to separate temporary files.
4. Compare logical Arrow outputs and performance JSON before deployment.
5. Retain the previous production artefacts and reader state for rollback.
6. Schedule the first production comparison under like-for-like looped-scanner load. Record concurrent scanner activity so CPU or I/O contention is not mistaken for an algorithm regression.
7. On the first production run, monitor every stage timer, peak memory, total rows and repair counts. Abort rather than silently falling back to empty history.
8. Add the observed production metrics to the relevant docstrings and this plan's results table.

## Kimi review

Kimi reviewed this plan and walked the named implementation paths and relevant tests in read-only mode on 2026-09-22. The review confirmed the hotspot ranking and directly motivated these amendments:

- deferring vault-state column materialisation until after filtering;
- making NumPy gathers and masks the default before adding Numba;
- preserving unconditional PEP 440 validation and correction output ordering;
- defining Decimal overflow/scale and semantic-signature rules for DuckDB;
- removing the logging-only sort and duplicate TVL map;
- factorising vault IDs once for reuse across grouped stages;
- short-circuiting default-only perp validation only if profiling confirms it is safe and material;
- pinning sidecar settlement and daily tie-break semantics;
- defining the Arrow equivalence oracle, RSS measurement and temporary rollout switches precisely.

Kimi also identified hypotheses that must be measured rather than accepted as facts: the share of outlier time spent in full-frame forward fill, savings from reused integer group codes, cost of default-only perp validation, benefit of replacing the final cleaned sort, and Numba's net benefit after JIT startup. Phase 0 owns these measurements.

## Measured results

The measurements below were collected on 2026-09-22 against the current
production-format copy (`vault-prices-1h.parquet`, 22,552,978 rows, 38
columns, 333,430,642 bytes) and current protocol DuckDB copies.  The original
production log did not record peak RSS, so an absent baseline is explicitly
marked rather than inferred.  Native merge numbers are replacement-only when
labelled as such; source export and price attachment remain a separate Phase 7
measurement.

| Stage | Old metric | New metric | Speed-up | Peak RSS before/after |
|---|---:|---:|---:|---:|
| ApeX snapshot derivation | 172.57s, 186,115 accounts (representative local run) | 11.65s, 210,200 accounts | not comparable; input copy and row count differ | baseline not captured / 548 MiB |
| Hypercore HF snapshot derivation | 56.67s, 58,697 accounts + 475,450 positions | 8.13s, 77,757 accounts + 632,324 positions | not comparable; input copy and row count differ | baseline not captured / 774 MiB |
| Native merge, replacement only | 6m35s production includes source export and attachment | 21.85s PyArrow replacement benchmark | not comparable; Phase 7 remains | baseline not captured / not captured |
| Metadata/filter/sort | about 134s production | 24.58s: identity scan, Arrow read, filter/enrichment and sort | 5.5x | baseline not captured / 20.4 GiB process peak |
| Inactive lead-time removal | 42s, 10,338,606 rows | 6.87s, same rows; 203,103 removed | 6.1x | baseline not captured / 20.4 GiB process peak |
| Stablecoin outlier repair | 237s, 8,535,236 EVM rows | 55.71s, same rows; 4.14s fill + 1.17s array kernel | 4.3x | baseline not captured / 20.4 GiB process peak |
| Crypto outlier repair | 43s, 2,912,298 rows | 1.28s, same rows | 33.6x | baseline not captured / 4.7 GiB process peak |
| Stablecoin cleaning | 11m25s, 22,552,978 raw → 10,116,623 cleaned | 146.02s, same row counts | 4.7x | baseline not captured / 20.4 GiB |
| Crypto cleaning/bundle | 1m30s, 10,116,623 hourly reread plus crypto leg | 29.27s integrated extra work: 5.91s sidecar write + 23.36s bundle builder | not a same-input pair; sidecar removes the wide reread | baseline not captured / 7.1 GiB |
| Total post-processing | 19m30s production | not measured end-to-end; native source/attachment were not rerun as one scanner cycle | not claimed | baseline not captured / stable cleaner 20.4 GiB |

The new stablecoin benchmark completed below the initial five-minute target for
cleaning, and the isolated native correction targets were met on the measured
copies.  The total post-processing target remains open until a complete scanner
cycle measures native source export, attachment and the crypto publication
steps under like-for-like load.  The peak-RSS acceptance gate is also explicitly
unverified: the original production run did not record a baseline.  The new
20.4 GiB stablecoin peak is therefore a measured capacity requirement, not
evidence that memory use improved.  Capture a like-for-like old-version RSS
baseline before declaring the memory gate met.
