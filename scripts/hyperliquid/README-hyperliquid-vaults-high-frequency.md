# High-frequency Hyperliquid vault data fetcher

## Overview

The high-frequency (HF) pipeline is the production Hyperliquid vault data
collector. It operates at configurable sub-daily intervals (default 4h, down to
1h), using a separate DuckDB database with timestamp-precision rows and optional
Webshare rotating proxies for parallel throughput. The older daily pipeline is
retained for legacy history, standalone use and combined Parquet reads, but the
looped production scanner does not run it.

The motivation is more responsive PnL tracking. The daily pipeline produces one
data point per vault per day. For vaults with intra-day volatility or for
consumers that need fresher data, this is insufficient. The HF pipeline captures
the same metrics — share prices, TVL, cumulative PnL, deposit/withdrawal flows —
at the **finest resolution the API offers, ~20 minutes** (see "What the API
actually returns" below). The `4h` default is the *scan trigger* cadence, not the
data resolution — it controls how often we poll, not how closely spaced the data
points are.

### Data flow

```
Hyperliquid API
├── stats-data (bulk GET) ── filter by TVL ─→ vault list
├── vaultDetails (per-vault POST, via post_info with proxy rotation)
│   └── portfolio: allTime / month / week / day
│       └── _merge_portfolio_periods() → portfolio_to_combined_dataframe()
│           └── _calculate_share_price() → HyperliquidHighFreqPriceRow
├── userNonFundingLedgerUpdates (per-vault POST)
│   └── aggregate_daily_flows(events) → price-row flow fields
└── clearinghouseState (per-vault POST)
    └── collect_hyperliquid_vault_observations() → current position observations

HyperliquidHighFreqMetricsDatabase (hyperliquid-vaults-hf.duckdb)
├── vault_metadata and vault_high_freq_prices
│   └── merge_hypercore_prices_to_parquet() → vault-prices-1h.parquet
│       └── process_raw_vault_scan_data() → cleaned-vault-prices-1h.parquet
└── perp_vault_* observation tables
    └── generic temporal join → `other_data.perp_dex` exposure metrics
```

### Key components

| Module | Purpose |
|--------|---------|
| `eth_defi/hyperliquid/vault_metrics_db.py` | Base class: shared `vault_metadata` table, metadata upsert, lifecycle (tombstone, disappeared), query helpers, save/close |
| `eth_defi/hyperliquid/high_freq_metrics.py` | HF subclass: `vault_high_freq_prices` table, HF ingestion through shared staging/hash joins, per-vault fetcher, proxy-aware session pool orchestrator |
| `eth_defi/hyperliquid/daily_metrics.py` | Daily subclass: `vault_daily_prices` table, daily upsert, share price recomputation, schema migrations |
| `eth_defi/hyperliquid/vault_data_export.py` | Export: `_prepare_hypercore_export()` shared helper, `merge_hypercore_prices_to_parquet()` combined merge |
| `eth_defi/vault/scan_all_chains.py` | `_run_hypercore_scan()` shared orchestrator, `scan_hypercore_fn()` / `scan_hypercore_hf_fn()` thin wrappers |
| `eth_defi/vault/post_processing.py` | Opens whichever Hyperliquid databases exist, passes both to combined merge |
| `scripts/hyperliquid/high-freq-vault-metrics.py` | Standalone script with optional loop mode |

### Class hierarchy

```
HyperliquidMetricsDatabaseBase (vault_metrics_db.py)
├── vault_metadata table (shared schema)
├── upsert_vault_metadata(), update_vault_tvl_bulk()
├── mark_vaults_disappeared(), tombstone_stale_vaults()
├── get_vault_count(), get_recently_tracked_addresses(), get_all_tracked_addresses()
├── _get_last_price_row() — used by subclass _write_tombstone_rows()
└── save(), close()
    │
    ├── HyperliquidDailyMetricsDatabase (daily_metrics.py)
    │   ├── price_table = "vault_daily_prices", time_column = "date"
    │   ├── upsert_daily_prices() through staging/hash joins
    │   ├── _write_tombstone_rows() → HyperliquidDailyPriceRow(date=today)
    │   ├── get_all_daily_prices(), get_vault_daily_prices()
    │   └── recompute_vault_share_prices(), detect_broken_vaults()
    │
    └── HyperliquidHighFreqMetricsDatabase (high_freq_metrics.py)
        ├── price_table = "vault_high_freq_prices", time_column = "timestamp"
        ├── upsert_high_freq_prices() through staging/hash joins
        ├── _write_tombstone_rows() → HyperliquidHighFreqPriceRow(timestamp=now)
        └── get_all_high_freq_prices(), get_vault_high_freq_prices()
```

## How this differs from the native Hyperliquid API solution

### What the API actually returns

The Hyperliquid `vaultDetails` endpoint returns portfolio history in four
fixed-span periods, each with a **server-chosen resolution that we cannot
influence**. Every period carries a roughly fixed budget of points (~70 for
`day`/`week`, ~45 for `month`/`allTime`), so the resolution is simply
`span / point-budget`:

| Period    | Span        | Resolution (measured) | Points |
|-----------|-------------|-----------------------|--------|
| `day`     | last 24h    | **~20 min**           | ~74    |
| `week`    | last 7d     | ~3h                   | ~67    |
| `month`   | last 30d    | ~10.5–24h             | ~45    |
| `allTime` | full life   | ~weekly               | ~44    |

**~20 minutes is the hard resolution floor.** The `day` period is the finest the
API ever serves — there is no sub-20-min vault history anywhere in the endpoint,
so no amount of polling can produce it (see "API portfolio resolution is the
bottleneck" below).

**The API downsamples points as they age.** A point that sits in today's `day`
period at 20-min resolution will, within a few days, only survive inside the
`week` period at ~3h resolution, then `month` at ~10.5h, then `allTime` weekly.
The fine version is discarded server-side and **cannot be re-fetched later**. The
only way to retain 20-min history permanently is to snapshot the `day` period
into our own DuckDB before those points age out of the 24h window — which is the
whole point of this pipeline.

When explicitly run, the legacy daily pipeline truncates every timestamp to
`.date()` and stores one row per vault per calendar day. This discards all
intra-day resolution — the `day` period's ~20-min data points are collapsed
into a single daily entry.

### What the HF pipeline does differently

The HF pipeline exploits the same API data more aggressively:

1. **Raw timestamps instead of date truncation**: API timestamps are stored
   as-is from the merged portfolio history.  This preserves the full per-period
   resolution (~20 min for `day`, ~3h for `week`, ~10.5h for `month`, ~weekly
   for `allTime`) — points retain original clocks; exact duplicate price keys are merged.

2. **Resumable with overlap**: each poll stores all rows with timestamp `>=`
   the last stored timestamp.  The `>=` (not `>`) ensures the latest row is
   re-upserted to refresh corrected economics and sparse economic metrics.
   Original permission fields and first write clocks remain unchanged.
   The next run collects retained newer API points; gaps in older downsampled
   history cannot be reconstructed automatically and need retained backups.

3. **Proxy-aware parallelism**: Hyperliquid rate-limits at 1200 weight/min/IP,
   with `vaultDetails` costing 20 weight (= ~1 req/s per IP). With N Webshare
   proxies, the pipeline gets Nx throughput via a pre-created session pool
   where each worker gets its own cloned session with independent rate limiting.

4. **Daily flow aggregation**: deposit/withdrawal events are aggregated by
   calendar date (same as the daily pipeline) and matched to price rows via
   `.date()` on the raw timestamp.  Flow values are only attached to the
   **last row per calendar date** to avoid inflating downstream sums.

### Rate limiting and proxy architecture

Without proxies: ~1 req/s → scanning 500 vaults takes ~500s (~8 min).

With 10 Webshare proxies: each proxy gets its own rate limit. The session pool
pattern (from `trade_history_db.py`) pre-creates N cloned sessions and leases
them to worker threads via a thread-safe pool:

```python
session_pool = [session.clone_for_worker(proxy_start_index=i) for i in range(n_workers)]
session_lock = threading.Lock()

def _hf_worker(summary):
    with session_lock:
        worker_session = session_pool.pop()
    try:
        return fetch_and_store_vault_high_freq(worker_session, db, summary, ...)
    finally:
        with session_lock:
            session_pool.append(worker_session)
```

Each clone shares the same `ProxyStateManager` (persistent failure tracking)
but has its own rate-limiter adapter. Failed proxies are rotated within
`post_info()` transparently.

## Pipeline integration and potential issues

### Combined merge: no data loss between modes

The central design principle is that **both DuckDB databases are always merged
together** into the parquet.  The function `merge_hypercore_prices_to_parquet()`
accepts both `daily_db` and `hf_db` as optional parameters.

Both export functions (`build_raw_prices_dataframe` and
`build_raw_prices_dataframe_hf`) delegate to the shared
`_prepare_hypercore_export()` helper for carrying sparse economic metrics and
building the EVM-compatible DataFrame after independent permission selection.

This means:

- Running the **daily script** also includes any existing HF data in the parquet
- Running the **HF script** also includes any existing daily data in the parquet
- **Switching between modes** never loses historical data from the other database
- Running both pipelines against the same parquet is safe (file lock coordinates
  concurrent access)

Daily rows have midnight timestamps (from `pd.to_datetime(date)`), HF rows have
raw API timestamps — they rarely collide.  When they do share the exact same
timestamp for the same vault, the HF row is kept (more recent/granular data).

### Raw timestamps, no resampling

HF data is written with the original API timestamps (irregular spacing). The
downstream cleaning pipeline first replaces raw scanner units with at most one
PnL/NAV economic checkpoint per fixed four-hour UTC bucket. The selected row
keeps its original API timestamp. Non-checkpoint HF rows carry the last clean
price, and missing buckets do not create interpolated rows. Daily and weekly
source history remains naturally coarse. The pipeline then computes
`returns_1h` via `pct_change()` on consecutive rows. When consumers need a
regular 1h grid, they call `forward_fill_vault()` which does
`.resample("h").last().ffill()`.

Note: `returns_1h` is a compatibility name — see
`calculate_vault_returns()` in `wrangle_vault_prices.py`. It is `pct_change()`
between consecutive rows regardless of actual time delta.

### Column name mapping

The HF row model uses bucket-neutral names (`deposit_count`, `withdrawal_count`),
but the downstream Parquet/cleaning pipeline expects `daily_deposit_count`,
`daily_withdrawal_count`, etc. The `_prepare_hypercore_export()` helper handles
this mapping via the `flow_col_map` parameter.

### Independent permission and sparse economic metrics

Every successful vaultDetails response supplies an independent coherent permission
snapshot before portfolio checks. Missing flags explicitly mean Unknown; fetch or
parse failures are recorded separately. Changed bulk closures are captured before
TVL/address/count filtering. An already closed genuine snapshot suppresses a
redundant bulk denial without refreshing its receipt clock.

Portfolio rows retain sparse economic snapshots such as follower count, volume
and commission. Export may carry those metrics forwards. It selects permission
from whole independently clocked responses instead of filling individual flags;
explicit Unknown and historical uncertainty boundaries remain intact. Capacity
requires its own original clock and is never inferred from recovered price flags.
The independent permission sidecar retains changes between portfolio points.

Legacy databases **require a backed-up migration**, including removal of unsafe
ART price constraints. Re-export alone cannot recover lost keys or old permission
truth. See the [permission recovery runbook](../../docs/README-hypercore-permission-recovery.md)
for dry-run/apply, source priority, inferred price clocks, R2 cadence and production
maintenance. Manifest v2 remains disabled until coordinated consumer rollout.

### Staging and hash-join ingestion

Daily and HF prices use unconstrained staging tables and application deduplication:

- Dense economics use the latest value for an existing key.
- Sparse economic/flow fields use `COALESCE(new, existing)`; duplicate keys within
  one batch preserve the last non-null value.
- Compatibility permission fields, leader fraction and first `written_at` remain
  immutable for an existing price key. Independent observations supply later
  state changes without rewriting historical prices.

### Post-processing

`post_processing.py` opens whichever Hyperliquid databases exist on disc
(daily and/or HF) and passes both to `merge_hypercore_prices_to_parquet()`.
When both databases exist, they are merged regardless of the active
`HYPERCORE_MODE` setting.

### Integration via scan_all_chains.py

Set `HYPERCORE_MODE=high_freq` to switch the Hypercore **scan** function to the
HF pipeline (proxy-aware, sub-daily).  Both scan functions delegate to the shared
`_run_hypercore_scan()` orchestrator for result tracking, error handling, and
vault metadata merge.  Post-processing always merges both databases regardless
of mode:

```shell
SCAN_HYPERCORE=true HYPERCORE_MODE=high_freq SCAN_CYCLES="Hypercore=4h" \
  poetry run python scripts/erc-4626/scan-vaults-all-chains.py
```

The price merge reads either database when it exists:
- `hyperliquid-vaults.duckdb` — daily pipeline
- `hyperliquid-vaults-hf.duckdb` — HF pipeline

The looped production Compose service defaults to `HYPERCORE_MODE=high_freq`.
It is therefore this scanner that persists current public positions for the
shared perp-Dex exposure export. Set `HYPERCORE_MODE=daily` only for an
intentional standalone or compatibility run; that database is otherwise
inactive in production.

### Potential issues and caveats

**1. `returns_1h` is returns between consecutive rows, not true 1h returns**

The cleaning pipeline computes `returns_1h = pct_change()` on consecutive rows
regardless of actual time delta. Hypercore economic returns occur only at the
selected observations in occupied four-hour UTC buckets; intervening raw rows
carry the price and have zero change. Gaps can be longer where source history
is missing or already coarse. Price level, cumulative profit and drawdown use
this clean curve. Volatility, Sharpe and other cadence-sensitive statistics
must select `hypercore_repair_status` checkpoint rows instead of treating
carried rows as independent hourly observations. Consumers that need a uniform
1h price grid can use `forward_fill_vault()`.

A later NAV observation can confirm a recent PnL-only update. In this bounded
case, the cleaner carries the provisional checkpoint and applies performance
at the confirmation, revising subsequently compounded recent prices. The
confirmation window is 26 hours, may skip flat intermediate observations and
rejects any intervening economic change. This evidence-driven update is the
only intended exception to append stability for cleaned Hypercore prices.

**2. Flow metrics are attached to one row per calendar date only**

Flow data is aggregated by calendar date.  To avoid inflating downstream sums
(e.g. `vault_metrics.py` sums `daily_deposit_count` across rows), flow values
are only attached to the **last row per calendar date**.  All other intraday
rows on the same date carry `None` for flow fields (preserved via COALESCE on
upsert).  Consumers can safely sum the flow columns without deduplication.

**3. API portfolio resolution is the bottleneck — ~20 min is a hard floor**

The resolution of each period is fixed server-side (`day` ~20 min, `week` ~3h,
`month` ~10.5h, `allTime` ~weekly — see "What the API actually returns"). Polling
harder does **not** buy finer data:

- **Resolution is polling-independent.** The `day` period is always ~20 min
  regardless of when or how often you call. There is no sub-20-min data to fetch,
  so polling every 20 min instead of every 4h just re-downloads the same ~74
  points — zero new information.
- **Retention is what polling protects.** Because the API downsamples on aging,
  the only way to keep 20-min history is to snapshot the `day` period before its
  points age past the 24h window and get coarsened to `week`'s ~3h buckets. Since
  `day` spans 24h, you must poll **at least once per 24h** to lose nothing. The
  `4h` default is a 6× safety margin against a missed run, not a resolution knob.

In short: poll *often enough* (≤24h), not *harder*. Polling faster than once a day
yields identical data; the only thing that would raise resolution is Hyperliquid
serving finer buckets, which is outside our control.

**4. Proxy cost and monitoring**

Each Webshare backbone proxy adds cost. The `ProxyStateManager` tracks failures
persistently (SQLite), so bad proxies are skipped in future runs. Monitor the
proxy health via the session's `_rotation_count` and `_request_count` attributes
in logs.
