# Five low-effort speed-ups for vault post-processing

Historical code-only review before the local experiments. The tested outcomes, including the rejected daily sidecar and negligible metric-copy pilot, are in [implementation-results.md](implementation-results.md). Line numbers below refer to the earlier checkout.

I read only the functions you named, plus two short helpers that fell inside the same line ranges (`fix_outlier_share_prices` and `sort_and_index_vault_prices`). I edited nothing, ran nothing and measured nothing, so every cost below is inferred from the code.

## Findings (highest expected value first)

### 1. Read only the columns the metrics need (preparation phase)
- **Where:** `eth_defi/vault/top_vaults_json.py:1258`, plus the downstream effect in `eth_defi/research/vault_metrics.py:4496` and `:4502`.
- **Why it costs time:** `pd.read_parquet(..., filters=...)` reads every column, about 38. `_calculate_regular_daily_returns` then runs `resample("D").last()` and `ffill()` on every column, once per vault, including string and metadata columns. Wide columns multiply the cost of the read, the per-group copy, the resample, the fill and the concat. This is probably the largest part of the roughly 6-minute preparation phase.
- **Minimal change:** pass an explicit `columns=[...]` allow-list: `timestamp`, `id`, `chain`, `address`, `share_price`, `total_assets`, `total_supply`, `block_number`, `event_count`, the flow and state columns, the lending and leader columns, and the perp columns.
- **Correctness risk:** high if the list is incomplete. Helpers I did not read (`build_perp_dex_other_data`, `get_latest_vault_poll_frequency`, `_derive_erc4626_estimated_daily_flows`) read columns directly. A missing column would quietly become `None` in the JSON rather than raising an error.
- **Measurement:** run the production `main()` twice. Keep the change only if the output JSON (excluding `generated_at`) is byte-for-byte identical and the `daily-prep` phase time drops by at least 30%.

### 2. Remove repeated per-vault work in `_calculate_regular_daily_returns`
- **Where:** `vault_metrics.py:4488–4495`.
- **Why it costs time:** for every vault it does an explicit `group.copy()`, rebuilds the column list, runs `notna().all(axis=1)` and runs `fillna(False).astype(bool)`. That is several full wide-frame passes per vault, across thousands of vaults. The two-key `groupby(["chain","address"])` also hashes two columns when `id` already identifies the vault.
- **Minimal change:**
  - Compute `VAULT_STATE_OBSERVED_COLUMN` once for the whole frame, before the loop.
  - Delete `group.copy()`.
  - Keep the default `sort=True` so the output order does not change.
- **Correctness risk:** low. Without copy-on-write, dropping the copy can raise `SettingWithCopyWarning`. Hoisting the column computation also makes it appear in the input frame.
- **Challenge:** `resample().last()` very likely dominates the loop. In that case this saves little on its own; its value grows after finding 1.
- **Measurement:** a py-spy profile of this function. Keep the change only if `copy`, `notna` and `fillna` together take at least 15% of the function's time.

### 3. Skip two wide-frame copies per vault in `calculate_vault_record`
- **Where:** `vault_metrics.py:2933` and `:2955`.
- **Why it costs time:** line 2933, `prices_df.loc[~index.isna()].sort_index(kind="stable")`, copies the whole frame for every vault. The input comes straight from a daily resample, so it is already sorted and has no missing timestamps. Line 2955, `valid_price_rows = prices_df.loc[valid_share_price]`, copies the wide frame again, but it is only used for the first and last row's timestamp and `block_number` (lines 3111–3117).
- **Minimal change:**
  - Guard line 2933 with `if not (idx.is_monotonic_increasing and not idx.hasnans)`.
  - Replace line 2955 with positions: `pos = np.flatnonzero(valid_share_price.to_numpy())`, then read `prices_df.index[pos[0]]` and `prices_df["block_number"].iat[pos[-1]]`.
- **Correctness risk:** very low. The guard only skips a no-op. An empty `pos` fails at the same point that `.iloc[-1]` fails today.
- **Measurement:** line-profile the metrics loop. Keep the change if these two lines take at least 10% of the roughly 6-minute metrics time.

### 4. Cut redundant copies and write-backs in the EVM outlier-repair stage
- **Where:**
  - `wrangle_vault_prices.py:1820`: `reset_index(drop=True).copy()` makes a second full copy of about 8.5 million wide rows.
  - `wrangle_vault_prices.py:2067–2070` (called from 2249): `_copy_columns_by_position` writes back every column. Unchanged columns are included, apart from the two excluded identity columns.
- **Why it costs time:** the docstring gives 55.7 s for the stage, with forward-fill at 4.1 s and the numerical kernel at 1.2 s. About 50 s is therefore unaccounted for. The likely causes are the copy, `iloc` extraction, the `nunique` assertion at 1854, and `iloc` writes into Arrow extension columns. Arrow arrays are immutable, so each positional write probably rebuilds the whole 10-million-row column.
- **Minimal change:**
  - Drop the redundant `.copy()`.
  - In the caller, copy back only `share_price`, `raw_share_price` and the state columns that actually contained missing values in `evm_df`. Compute that set with `evm_df[state_columns].isna().any()`.
- **Correctness risk:** medium. The forward-fill side effect must still reach every column it changes. Test on a mixed Hypercore/EVM fixture where the frame is compared exactly before and after.
- **Measurement:** time each column inside `_copy_columns_by_position`, plus `.iloc[evm_positions]` and the `nunique` assert. Only act where the time actually is. Do not assume the Python loop over per-vault medians at 1869 matters: it is roughly 20 µs per vault.

### 5. Serialise and compress the output once (finalisation phase)
- **Where:**
  - `top_vaults_json.py:1419–1425`: two `validate_strict_json_serialisable` walks, then `json.dump(indent=2)`.
  - `post_processing.py:211–213`: `brotli.compress(quality=11)` runs for each bucket, and runs before the `skip_if_current` check.
- **Why it costs time:**
  - `json.dump` streams through `iterencode` without the one-shot flag, so it uses the pure-Python encoder. `json.dumps` can use the C encoder, but whether it does with `indent` depends on the CPython version.
  - `allow_nan=False` already rejects NaN and infinity values, so the validator walk may be partly redundant.
  - Brotli at quality 11 manages only about 1 MB/s on large text. It is repeated for the primary and alternative buckets, and is paid even when the upload is then skipped as unchanged.
- **Minimal change:**
  - Write the file with `f.write(json.dumps(...))`, which produces identical bytes.
  - Compress once per file and reuse the result for every bucket. Also consider checking the remote source digest before compressing.
- **Correctness risk:**
  - Writing via `json.dumps` holds the whole string in memory.
  - The strict validator may reject things `json` accepts, such as non-string keys or tuples, so keep it unless it proves slow.
  - Lowering brotli to quality 9 or 10 gives a larger `.br` file but does not change its content.
- **Measurement:** on the production `output_data` and JSON file, time the validator, `dump` versus `dumps`, and brotli at quality 11 versus 9, recording the size difference. Keep a change if it saves at least 20 s.

## Assumptions worth challenging
- **Cleaning takes 3–5.5 minutes, but it is spread across many stages.** I could not read `calculate_vault_returns`, `clean_returns` or `clean_by_tvl`. Check the existing per-stage timer logs before optimising any stage.
  - One cheap candidate: `process_raw_vault_scan_data` at `wrangle_vault_prices.py:2167–2171` calls `set_index`, then `sort_and_index_vault_prices` does `reset_index`, sort and `set_index` again. That moves the wide frame around twice and sorts on string `id`s. Check it against the existing "Vault cleaning stage sort" log line before touching it.
- **The biggest metrics win is not a "simple" one.** The per-vault loop in `calculate_lifetime_metrics` (`vault_metrics.py:3355`) runs on one core and each vault is independent, so a process pool could divide the roughly 6-minute metrics phase. The cost is serialising per-vault data to worker processes and extra memory.
- **`pd.DataFrame(records)` at `vault_metrics.py:3374`** builds the result from a list of Series with about 100 keys each, which is slower than building from a list of dicts. At this vault count it is probably only seconds, so time it before prioritising it.
