# Vault lifetime metrics performance plan

## Goal

Make `calculate_lifetime_metrics()` in `eth_defi/research/vault_metrics.py` at least four times faster without changing its output. Replace the per-vault pandas slicing and copying with one whole-frame NumPy conversion and array-based per-vault calculations.

Decisions:

- **Internal function signatures and the internal pipeline may change.** This covers `calculate_vault_record()`, `calculate_period_results()`, `calculate_period_metrics()`, the flow helpers, the ERC-4626 flow estimator and the private helpers. Their callers and tests are updated in the same PR. They are internal to the export pipeline; other modules mention them only in docstrings.
- **Keep all columns for now.** The output DataFrame keeps every column in the same order, including the legacy compatibility fields (`one_month_*`, `three_months_*`, `lifetime_*`, `netflow` and others), and the exported JSON stays the same. There is no further input-column pruning beyond `UNUSED_METRIC_PRICE_COLUMNS`. `calculate_hourly_returns_for_all_vaults()` keeps its output columns because scripts, notebooks and tests use it.
- `calculate_lifetime_metrics(df, vault_db, ...)` keeps its current parameters, because about fifteen tests and several scripts call it. Changing it would not make it faster. Step 4 may add one optional keyword-only argument.

Keep the change small. Previous rounds found the real costs by measurement, not by building frameworks, and this plan follows the same approach.

## Background

Earlier work removed most of the post-processing cost around this function:

| PR | Relevant change |
|---|---|
| #1586 | Cleaning used one factorisation, positional NumPy kernels and Arrow push-down filters. |
| #1588 | Native ETH/BTC admission thresholds and a projected native metric path. |
| #1590 | Low-TVL vaults recalculated every three days: about 5,765 of 12,795 stablecoin vaults due per run. YAML metadata is cached in-process, and rankings are vectorised. Process-pool parallelism was rejected after fork deadlocks and spawn-worker OOMs. |
| #1595 | The metric input is projected to 30 columns (`UNUSED_METRIC_PRICE_COLUMNS`). A warm top-vault export takes about 175 s. |

`calculate_lifetime_metrics()` now runs twice per scanner cycle in the same process. `top_vaults_json.main()` runs it for the public top-vault export, and `crypto_vaults.build_crypto_vault_metadata()` runs it for the private crypto bundle's stablecoin portion. The native ETH/BTC path, `crypto_vaults._build_native_crypto_metrics()`, calls `calculate_vault_record()` directly.

## Measurements

Measured on 2026-09-25 against `master` at `d3f36b775`, using the production-format snapshot in `~/.tradingstrategy`. The input was a seeded 1,000-vault stablecoin sample: 739,539 source rows, regularised to 520,458 daily rows with 31 columns. The time shares below come from wall-clock wrappers, not cProfile: cProfile inflated this loop about 2.5 times, and py-spy hangs on the Python 3.14 environment.

**Total time: 19.15 s, about 52 vaults/s.** Production logs show about 48 vaults/s. The preceding daily preparation step, `calculate_hourly_returns_for_all_vaults()`, took a further 6.1 s on the same sample.

| Area | Time | Share |
|---|---:|---:|
| `calculate_period_results()`: 6 × `calculate_period_metrics()` | 5.69 s | 30% |
| `_attach_period_flow_metrics()` + `_calculate_netflow_metrics()` | 6.20 s | 32% |
| ↳ `_get_complete_flow_window()`: boolean `.loc` on the wide frame | 2.97 s | |
| ↳ `_get_complete_flow_columns()`: Arrow column take + `notna().all()` | 2.61 s | |
| `_derive_erc4626_estimated_daily_flows()`: full-frame `copy()`, `apply(to_numeric)` | 3.12 s | 16% |
| `prepare_daily_share_price_series()` + duplicate `sanitise_share_price_observations()` | 1.1 s | 6% |
| Whole-row `iloc[-1]` / `iloc[0]` on the mixed-type frame, including the perp-DEX row | ~0.7 s | 4% |
| `sort_index()` and valid-price row filtering copies | ~0.5 s | 3% |
| Metadata, curator matching and `pd.Series` record construction | ~1.5 s | 8% |

A throwaway NumPy prototype ran in 0.1 s for all 1,000 vaults (0.1 ms per vault). It covered the six period calculations (endpoints, volatility, Sharpe, drawdown, TVL) and six complete-window flow sums on `int64` timestamps. The whole-frame sort, factorisation and `float64` conversion took a further 0.11 s. The prototype omitted error reasons, tolerances and fee logic, so it gives a lower bound only.

Expected result for steps 1–3: about 19 s → 3–5 s per 1,000 vaults, roughly 110 s → 20–30 s per bundle at 5,765 due vaults. Step 4 could also remove most of the 6.1 s preparation step. These figures are local projections, not production claims.

## Scope

In scope:

- The internal pipeline and signatures of `calculate_lifetime_metrics()`, `calculate_vault_record()`, the period helpers, the flow helpers and the ERC-4626 estimator.
- The native crypto caller in `crypto_vaults.py`.
- Step 4, which is measured and gated: folding the daily regularisation into the metric pipeline.
- A benchmark and parity script.

Out of scope, because each needs a separate measured decision:

- Process or thread parallelism (see #1590).
- Sharing one stablecoin metric calculation between the two bundles. #1595 found that their inputs differ in ten daily fields.
- Changes to the YAML metadata loading cost, which is a one-off of about 2 s per process.
- Any change to metric definitions, tolerances, error-reason strings, output columns or column order.

## Target pipeline

Status: implemented on 2026-09-25. This section describes the code as built; see *Implementation results* for deviations from the original steps below.

```
calculate_hourly_returns_for_all_vaults(df)                 # public, unchanged
  └─ _calculate_regular_daily_returns()
       ├─ _regularise_daily_with_arrays()                    # hourly input, cleaned-Parquet dtypes
       └─ _regularise_daily_per_vault()                      # sparse daily input and other dtypes

calculate_lifetime_metrics(df, vault_db, ...)               # public signature unchanged
  └─ _prepare_vault_price_frame(df) -> _VaultPriceFrame      # float64 metric columns, once per frame
  └─ _iterate_vault_arrays() -> (vault_id, _VaultArrays)     # replaces groupby("id", sort=True)
       └─ _calculate_vault_record_from_arrays()              # returns pd.Series, as before
            ├─ _estimate_erc4626_daily_flows()
            ├─ _prepare_daily_share_price_arrays()
            ├─ _calculate_period_results_from_arrays()
            └─ _attach_period_flow_metrics(), _calculate_netflow_metrics()
  └─ calculate_vault_rankings(), generated_at               # unchanged

calculate_vault_record(prices_df, ...)                     # single-vault pandas entry point
calculate_period_metrics(), calculate_period_results()      # pandas entry points over the array kernel
prepare_daily_share_price_series()                          # pandas entry point over the array helper
```

`_VaultPriceFrame` and `_VaultArrays` are two `@dataclass(slots=True)` value holders: the whole frame and one vault's slice of it. There is nothing else: no registry, no plugin layer.

### Semantics to preserve exactly

- **Physical last-row values.** Today several scalars are read with `prices_df[...].iloc[-1]` *before* the NaT filter and sort in `calculate_vault_record()`:
  - `current_nav`, `chain`, `event_count`;
  - `available_liquidity`, `utilisation`;
  - `leader_fraction`, `leader_commission`, `account_pnl`, `follower_count`, `cumulative_volume`;
  - the perp-DEX row passed to `build_perp_dex_other_data()`;
  - the scan cycle from `get_latest_vault_poll_frequency()`, which is the last non-null value in physical order.

  `max_nav` and `now_` are also computed before the sort, as the maximum over all rows. Record each vault's last physical position before sorting and read these values from it. Both callers' inputs are already time-ordered, so parity should confirm there is no difference in practice.
- **Flow presence has two meanings** (Kimi finding 1). The pre-estimate flag checks whether the vault has direct directional or signed flow data, and decides whether to derive an estimate. The post-estimate flag, which gates the period and 1d windows, is `direct_flow or (estimate is not None and np.isfinite(estimate).any())`. Never hoist a single pre-estimate flag.
- **Numeric coercion.** Use `pd.to_numeric(errors="coerce")` before `to_numpy(dtype="float64", na_value=np.nan)`, matching the current `apply(pd.to_numeric, errors="coerce")`.
- **Durations and error strings.** Only searching, slicing and reductions move to NumPy. Convert positions back to `pd.Timestamp`, compute `sample_duration` as a real `pd.Timedelta`, and keep `years = sample_duration.days / 365.25`. Keep every existing `error_reason` f-string.
- **Arrow `NaN` versus null.** Verified with pandas 3.0.1: `[1.0, NaN, null]` gives `notna() == [True, True, False]`, and the window sum becomes `nan`. After `float64` conversion, both count as missing. This affects two cases:
  - a flow window containing an IEEE `NaN` becomes incomplete (`None`) instead of producing a `nan` sum;
  - an all-`NaN` direct flow column no longer suppresses the ERC-4626 estimate.

  The parity script counts both cases. Treat them as an intentional fix documented in the PR; do not reproduce the `NaN` behaviour.

## Implementation steps

Each step is a separate commit. Measure each one with the benchmark and check parity before starting the next.

### Step 0: benchmark and parity harness

Add `scripts/erc-4626/benchmark-lifetime-metrics.py`, following the existing `benchmark-*.py` scripts:

- Environment variables `SAMPLE_SIZE` (default 1,000, `0` for all stablecoin vaults), `SEED` and `OUTPUT_PATH`.
- Read the metric columns exactly as `top_vaults_json.main()` does, run `calculate_hourly_returns_for_all_vaults()`, warm the caches once, then time `calculate_lifetime_metrics()`. Time the preparation step separately.
- Log vaults per second, total time and peak RSS, then pickle the result DataFrame.
- `COMPARE_WITH=<pickle>` compares two results:
  - `list(df.columns)` must be equal;
  - per vault `id`, non-float fields must match exactly;
  - floats, including every `PeriodMetrics` and `NetflowMetrics` field, use `math.isclose(rel_tol=1e-9, abs_tol=1e-12)`;
  - `None` and `NaN` must match in kind.
- Print a `tabulate` summary of the differing fields and vault counts, including the Arrow-`NaN` counts.
- Exclude `generated_at`.

Record the baseline pickles before changing any code: the 1,000-vault sample and the full stablecoin set.

### Step 1: whole-frame arrays, flows and the ERC-4626 estimate

This covers the flow work, about 48% of the time.

- Add `_prepare_vault_arrays()` and change `calculate_vault_record()` to take a `VaultArrays` view instead of a DataFrame. `calculate_lifetime_metrics()` builds the arrays once.
- The native path in `crypto_vaults._build_native_crypto_metrics()` currently resamples each group and then calls `calculate_vault_record()`. Change it to concatenate its regularised groups, build arrays once and call the same per-vault loop. This keeps a single record-building code path. Its per-group `resample("D").last().ffill()` stays in this step.
- Replace `_derive_erc4626_estimated_daily_flows()` / `_get_valid_erc4626_flow_states()` with one array function, `_estimate_erc4626_daily_flows(ts, total_assets, total_supply, share_price, observed, vault_id) -> np.ndarray | None`. It keeps the consecutive-day, duplicate-day, freshness, validity, residual and dust checks, and their debug messages.
- Replace `_get_complete_flow_window()` / `_get_complete_flow_columns()` / `_calculate_flow_window()` with one array function over `ts` and the flow arrays:
  - set `i0 = searchsorted(ts, cutoff, "right")` and `i1 = searchsorted(ts, now, "right")`;
  - the window is complete when `i1 - i0 == days` and the day numbers `ts[i0:i1] // DAY_NS` equal the consecutive range ending at `now_.normalize()`;
  - a column is complete when it has no NaN in the window.
- Change `_attach_period_flow_metrics()` and `_calculate_netflow_metrics()` to take the arrays and the two flow-presence flags.
- Update `tests/research/test_vault_metrics.py:329-434` and `tests/lighter/test_lighter_flow_metrics.py:280-353` to the new internal functions. Keep every assertion: estimated values, rejection reasons and log messages.

### Step 2: period metrics on arrays

This covers about 30% of the time.

- Change `calculate_period_metrics()` / `calculate_period_results()` to take `int64` timestamps and `float64` values for:
  - the sparse share-price observations;
  - the daily prices and returns;
  - TVL, which has its own timestamps because it includes rows with invalid prices;
  - optional utilisation, native fee-basis prices and exchange rates.
- Translate each operation exactly:
  - `Index.asof(x)` becomes `searchsorted(ts, x, "right") - 1`, where `-1` means missing;
  - an inclusive label slice `.loc[a:b]` becomes `searchsorted(ts, a, "left")` to `searchsorted(ts, b, "right")`;
  - `.iloc[0]` of a slice becomes the first element, so duplicate timestamps keep their current first-duplicate behaviour;
  - TVL minimum and maximum use `nanmin`/`nanmax`, falling back to `0` when all values are NaN;
  - volatility and Sharpe share one finite-return array, standard deviation uses `ddof=1`, and the `len < 2` and `MINIMUM_SHARPE_*` rules are unchanged;
  - maximum drawdown uses `np.maximum.accumulate` over the non-NaN daily prices;
  - native fee-basis prices and exchange rates are looked up by position, not by `.loc[label]`.
- Convert period boundaries with `(now_ - DateOffset).value`. `DateOffset(days=n)` equals `Timedelta(days=n)` for naive timestamps (verified). The lifetime period uses the first observation.
- Build `PeriodMetrics` from Python `float`/`int` and `pd.Timestamp` values, as the current code does.
- `calculate_crypto_usd_period_results()` keeps its pandas rate alignment, which runs once per ETH/BTC vault. It converts the aligned Series to arrays once before calling `calculate_period_results()`.
- Add an array version of the daily share-price preparation for the kernel, with a single sanitise call. `prepare_daily_share_price_series()` stays for `wrangle_vault_prices.py`.
- Update `tests/research/test_vault_metrics.py`, `tests/erc_4626/vault_protocol/test_forgeyields_metrics_fallback.py` and `scripts/hyperliquid/vaults-with-abnormal-metrics.py` to the new signatures. A small test helper can convert a Series to arrays.

### Step 3: remaining per-vault overhead

About 2 s per 1,000 vaults.

- Take `first_updated_block`/`last_updated_block` and the timestamps from positions in the `block_number` array and `ts`, instead of `valid_price_rows.iloc[0]` / `iloc[-1]` and the `valid_price_rows` frame copy.
- Build the perp-DEX input from the physical-last-row position as a dict of its five columns, and only when `perp_position_data_status` is present.
- Return a plain dict from `calculate_vault_record()` instead of `pd.Series`; `calculate_lifetime_metrics()` builds the DataFrame once. The parity script's column-order check guards this change.

### Step 4: fold the daily regularisation into the pipeline (gated)

Start this step only when steps 1–3 meet the 4 times target and the preparation step (6.1 s per 1,000 vaults) is still larger than the metric calculation. Stop and leave the preparation step unchanged if exact parity cannot be reached on the full snapshot.

- Add a keyword-only `regularise_daily: bool = False` to `calculate_lifetime_metrics()`. When it is true, the input is cleaned observation rows, and `_prepare_vault_arrays()` produces the same regularised daily arrays that `_calculate_regular_daily_returns()` produces today:
  - for each column, take the last *non-null* value per UTC day (the `resample("D").last()` semantics);
  - reindex onto consecutive calendar days;
  - forward-fill everything except the sparse flow columns and `_vault_state_observed`;
  - derive `_vault_state_observed` from the final row of each day that has complete state, combined with any existing marker.

  Sample counts (`lifetime_samples`, `raw_samples`) count these regularised days, so they must match exactly.
- `top_vaults_json.main()` passes `regularise_daily=True` and drops its `calculate_hourly_returns_for_all_vaults()` call. The crypto bundle's stablecoin portion does the same in place of `calculate_sparse_daily_returns_for_all_vaults()`, and the native path in place of its per-group resampling.
- `calculate_hourly_returns_for_all_vaults()` and `calculate_sparse_daily_returns_for_all_vaults()` stay unchanged for their other callers.

## Verification

- Entry-level tests must pass without changes to their assertions. These call `calculate_lifetime_metrics()` or `calculate_hourly_returns_for_all_vaults()`:
  - Lighter flow and daily metrics, Hyperliquid daily metrics, backfill and high-frequency export;
  - Enzyme, GMX oracle, Midas and Spiko;
  - `tests/research/test_vault_metrics_stablecoin_rate.py` and `test_metrics_freshness.py`;
  - `tests/hyperliquid/test_vault_review_persistence.py`, updated for the new `calculate_vault_record()` signature.
- Internal-function tests are updated to the new signatures with the same assertions: `tests/research/test_vault_metrics.py`, `tests/lighter/test_lighter_flow_metrics.py` and the ForgeYields fallback test.
- New unit tests for the array functions cover these edge cases:
  - a single observation, and a vault younger than the period;
  - sample duration above tolerance, and a negative CAGR base;
  - duplicate timestamps and calendar gaps;
  - an incomplete flow window and a provisional current day;
  - utilisation with all NaN values, and unsorted or NaT input;
  - for step 4, a day where the last row has a null value in one column.
- Parity: compare the baseline pickles with each step's result on the 1,000-vault sample and on the full stablecoin set. There must be no differences except the documented Arrow-`NaN` cases. Also run one native ETH/BTC sample through `build_crypto_vault_metadata()`, and check that its metadata JSON is equal after parsing.
- Performance: at least 4 times faster in the 1,000-vault benchmark, with peak RSS no higher than the baseline. If step 1 alone does not give at least a 1.5 times improvement, stop and profile again before continuing.

## Deliverables

- Code changes in `eth_defi/research/vault_metrics.py` and `eth_defi/vault/crypto_vaults.py`, plus `eth_defi/vault/top_vaults_json.py` in step 4.
- Updated tests and the `vaults-with-abnormal-metrics.py` script.
- `scripts/erc-4626/benchmark-lifetime-metrics.py`.
- Benchmark before-and-after numbers and a parity summary in the PR description.
- Updated docstrings noting the array-based internals and the measured timings, following the #1586 style.
- A `CHANGELOG.md` line.

## Plan review

On 2026-09-25, Kimi Code CLI 2.0.2 reviewed the first version of this plan read-only with the `kimi-code/k3` model (K3, configured `default_effort = "high"`). It checked the plan against the code using 15 read and grep calls, and changed no files. It was told not to propose frameworks, parallelism, JIT compilation or additional phases, and it proposed none.

| Finding | Severity | Disposition |
|---|---|---|
| Hoisting `_has_daily_flow_data()` to one pre-derivation flag would drop ERC-4626 estimated flows | Blocking | Kept: the two flow-presence meanings stay separate (see semantics above). |
| Tests call the private flow and estimator functions with DataFrames | Blocking | Superseded: internal signatures may now change, so the tests are updated to the array functions with the same assertions. |
| `to_numpy(dtype="float64")` is not the same as `to_numeric(errors="coerce")` | Should fix | Kept: coercion before conversion. |
| `Timedelta.days` truncation and `Timedelta` reprs in `error_reason` must be preserved | Should fix | Kept: real `Timedelta` durations and the existing f-strings. |
| The perp-DEX row is read before the sort | Should fix | Extended: every scalar read before the sort uses the physical-last-row position. |
| The Arrow `NaN` divergence also affects `_has_daily_flow_data()` | Nit | Kept: both cases counted and documented. |
| `test_vault_review_persistence.py` calls `calculate_vault_record()` directly | Nit | Kept: listed in verification and updated for the new signature. |

After the review, the plan was changed on 2026-09-25 at the user's direction: internal signatures and the pipeline may change, and all columns are kept. This added the whole-frame array preparation, dict records, the single native record path and the gated step 4.

## Implementation results

Implemented on 2026-09-25 and measured with `scripts/erc-4626/benchmark-lifetime-metrics.py` against the unchanged `master` code (`d3f36b775`) on the same production-format snapshot. Parity is checked on rows after `export_lifetime_row()`. It requires identical column order, exact types (JSON `0` versus `0.0`) and floats equal within `rel_tol=1e-9`.

| Input | Stage | Before | After | Speed-up |
|---|---|---:|---:|---:|
| 1,000 stablecoin vaults | Daily preparation | 5.76 s | 0.32 s | 18× |
| | `calculate_lifetime_metrics()` | 17.95 s | 1.69 s | 10.6× |
| | Total | 23.71 s | 2.01 s | 11.8× |
| All 12,795 stablecoin vaults, 10.1 M rows | Daily preparation | 75.89 s | 9.56 s | 7.9× |
| | `calculate_lifetime_metrics()` | 235.25 s | 22.82 s | 10.3× |
| | Total | 311.14 s | 32.38 s | 9.6× |
| | Peak RSS | 10.89 GiB | 8.90 GiB | |
| All 1,601 admitted native ETH/BTC vaults, USD view included | `_build_native_crypto_metrics()` | 33.77 s | 14.81 s | 2.3× |

Every comparison reported zero differing values. The documented Arrow-`NaN` flow cases did not occur in the snapshot. On the full snapshot, the daily preparation frame was also identical to the per-vault loop under `pd.testing.assert_frame_equal(check_exact=True)`, dtypes included.

Deviations from the plan:

- Steps 1–3 were implemented and measured together, as uncommitted changes; the stages were not committed separately.
- Step 4 speeds up `_calculate_regular_daily_returns()` itself instead of folding it into `calculate_lifetime_metrics()`. The daily frame's dtypes depend on per-vault calendar gaps and on pandas' concat rules. An int64 column becomes float64 as soon as any vault has a day without rows, which is why production publishes `event_count` as a float. A bool column becomes float64 or object depending on the first vault. Folding the step in would have meant reproducing those types for every exported scalar. The array implementation reproduces the whole frame, and exact frame equality is a stronger check. Sparse daily input (the crypto stablecoin path) and unusual dtypes still use the per-vault loop.
- `calculate_vault_record()`, `calculate_period_metrics()` and `calculate_period_results()` stay as thin pandas entry points over the array implementations. The single-vault test, the native ETH/BTC path and the USD path therefore keep working unchanged. The native path still resamples each vault in pandas, which limits its speed-up to 2.3×.
- Records remain `pd.Series`. Building them took 0.08 s per 1,000 vaults, which was not worth the column-order risk of a switch to dicts.
- Clean-up during review: the unused `month_ago` and `three_months_ago` parameters were removed from `calculate_vault_record()` and its callers, since the period lookbacks come from `LOOKBACK_AND_TOLERANCES`. `prepare_daily_share_price_series()` now delegates to the same array helper as the lifetime metrics; on 400 production vaults its output was identical to the pandas resample, index frequency included.

Local test results: 240 tests passed across the metric, flow, freshness, crypto, sticky-export, Hyperliquid, Lighter and Derive suites. After Foundry v1.3.2 was installed, the 14 Enzyme, Midas and Spiko Anvil fork tests also passed. Two Hypersync tests (GMX oracle and Midas history) failed with provider HTTP 429 or `get arrow` errors in the price scan, before any metrics code ran.
