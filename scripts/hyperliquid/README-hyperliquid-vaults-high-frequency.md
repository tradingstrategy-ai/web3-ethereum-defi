# High-frequency Hyperliquid vault data fetcher

## Overview

The high-frequency (HF) pipeline is an alternative Hyperliquid vault data collector
that operates at configurable sub-daily intervals (default 4h, down to 1h). It
runs alongside the existing daily pipeline, using a separate DuckDB database with
timestamp-precision rows and optional Webshare rotating proxies for parallel
throughput.

The motivation is more responsive PnL tracking. The daily pipeline produces one
data point per vault per day. For vaults with intra-day volatility or for
consumers that need fresher data, this is insufficient. The HF pipeline captures
the same metrics — share prices, TVL, cumulative PnL, deposit/withdrawal flows —
but at 4-hour (or finer) resolution.

### Data flow

```
Hyperliquid API
├── stats-data (bulk GET) ── filter by TVL ─→ vault list
└── vaultDetails (per-vault POST, via post_info with proxy rotation)
        │
        ├── portfolio: allTime / month / week / day
        │   └── _merge_portfolio_periods() → highest available resolution
        │       └── portfolio_to_combined_dataframe()
        │           └── _calculate_share_price() → share_price, total_assets, pnl
        │
        └── userNonFundingLedgerUpdates (deposit/withdrawal events)
            └── aggregate_daily_flows(events)
                │
                ▼
        Raw API timestamps preserved (no flooring or normalisation)
                │
                ▼
        HyperliquidHighFreqPriceRow (timestamp, not date)
                │
                ▼
        hyperliquid-vaults-hf.duckdb
        ├── vault_metadata (shared schema)
        └── vault_high_freq_prices (PK: vault_address, timestamp)
                │
                ▼
        build_raw_prices_dataframe_hf()
        ├── forward-fill state columns per vault
        ├── compute deposit_closed_reason
        └── map flow column names (deposit_count → daily_deposit_count)
                │
                ▼
        vault-prices-1h.parquet (chain 9999 rows replaced, raw timestamps)
                │
                ▼
        process_raw_vault_scan_data() → cleaned-vault-prices-1h.parquet
        (forward_fill_vault() resamples to 1h when needed downstream)
```

### Key components

| Module | Purpose |
|--------|---------|
| `eth_defi/hyperliquid/high_freq_metrics.py` | Core: database, timestamp normalisation, per-vault fetcher, session pool orchestrator |
| `eth_defi/hyperliquid/vault_data_export.py` | Export: DuckDB → 1h-resampled Parquet |
| `eth_defi/hyperliquid/deposit.py` | `aggregate_flows()` with configurable bucket |
| `eth_defi/vault/post_processing.py` | Dispatcher: daily vs HF merge based on `hypercore_mode` |
| `eth_defi/vault/scan_all_chains.py` | `scan_hypercore_hf_fn()`, `HYPERCORE_MODE` env var |
| `scripts/hyperliquid/high-freq-vault-metrics.py` | Standalone script with optional loop mode |

## How this differs from the native Hyperliquid API solution

### What the API actually returns

The Hyperliquid `vaultDetails` endpoint returns portfolio history in four periods
with varying temporal resolution:

- **allTime**: ~weekly snapshots for the full vault lifetime
- **month**: higher resolution for the last 30 days
- **week**: higher resolution for the last 7 days
- **day**: highest resolution for the last 24 hours (~hourly or better)

The daily pipeline calls this once per day, truncates every timestamp to `.date()`,
and stores one row per vault per calendar day. This discards all intra-day
resolution — the `day` period's hourly data points are collapsed into a single
daily entry.

### What the HF pipeline does differently

The HF pipeline exploits the same API data more aggressively:

1. **Raw timestamps instead of date truncation**: API timestamps are stored
   as-is from the merged portfolio history.  The API returns data at varying
   resolution (~weekly for `allTime`, sub-daily for `day` period) — all
   points are preserved without flooring or deduplication.

2. **Resumable with overlap**: each poll stores all rows with
   floored timestamp `>=` the last stored timestamp. The `>=` (not `>`) ensures
   the latest bucket is always re-upserted, refreshing corrected values or
   sparse state fields. If the job misses one or more cycles, the API's
   historical data fills in the gaps automatically on the next run.

3. **Proxy-aware parallelism**: Hyperliquid rate-limits at 1200 weight/min/IP,
   with `vaultDetails` costing 20 weight (= ~1 req/s per IP). With N Webshare
   proxies, the pipeline gets Nx throughput via a pre-created session pool
   where each worker gets its own cloned session with independent rate limiting.

4. **Daily flow aggregation**: deposit/withdrawal events are aggregated by
   calendar date (same as the daily pipeline) and matched to price rows via
   `.date()` on the raw timestamp.  This is natural since flow events are
   reported at daily granularity by the API.

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

### How HF data enters the existing pipeline

The ERC-4626 cleaning pipeline expects all vaults (EVM + native protocols) in
a single `vault-prices-1h.parquet`.  The HF pipeline integrates by:

1. **Replacing chain 9999 rows**: the merge function removes all existing
   Hypercore rows from the Parquet, then appends the fresh HF data.  This is
   idempotent — running twice produces the same result.

2. **Raw timestamps, no resampling**: HF data is written with the original
   API timestamps (irregular spacing).  The downstream cleaning pipeline
   computes `returns_1h` via `pct_change()` on consecutive rows — this already
   works for irregular timestamps.  The daily pipeline has always produced
   ~24h returns labelled `returns_1h` for Hypercore, so irregular spacing is
   not a new issue.  When consumers need a regular 1h grid, they call
   `forward_fill_vault()` which does `.resample("h").last().ffill()`.

### Column name mapping

The HF row model uses bucket-neutral names (`deposit_count`, `withdrawal_count`),
but the downstream Parquet/cleaning pipeline expects `daily_deposit_count`,
`daily_withdrawal_count`, etc. The export function maps these back:

```python
"daily_deposit_count": prices_df["deposit_count"].values
"daily_withdrawal_count": prices_df["withdrawal_count"].values
```

### COALESCE upsert semantics

Both the daily and HF pipelines use `ON CONFLICT DO UPDATE SET` with `COALESCE`
for sparse columns. This means:

- `share_price`, `tvl`, `cumulative_pnl`: always overwrite (most recent wins)
- `is_closed`, `allow_deposits`, `leader_fraction`, flow fields: `COALESCE(new, existing)` —
  a `None` new value preserves the existing value, so tombstone rows and
  overlap re-upserts do not wipe state

### Post-processing dispatch

`post_processing.py` routes based on `hypercore_mode`:

- `"daily"` (default): opens `HyperliquidDailyMetricsDatabase`, calls
  `merge_into_uncleaned_parquet()` — no resampling needed
- `"high_freq"`: opens `HyperliquidHighFreqMetricsDatabase`, calls
  `merge_into_uncleaned_parquet_hf()` — includes 1h resampling

Both paths feed into the same `clean_prices()` → `generate_cleaned_vault_datasets()`
step. The cleaning pipeline does not know or care which mode produced the data.

### Integration via scan_all_chains.py

Set `HYPERCORE_MODE=high_freq` to switch the Hypercore scan and post-processing
to HF mode:

```shell
SCAN_HYPERCORE=true HYPERCORE_MODE=high_freq SCAN_CYCLES="Hypercore=4h" \
  poetry run python scripts/erc-4626/scan-vaults-all-chains.py
```

The DuckDB path changes automatically:
- `daily` → `hyperliquid-vaults.duckdb`
- `high_freq` → `hyperliquid-vaults-hf.duckdb`

### Potential issues and caveats

**1. `returns_1h` is returns between consecutive rows, not true 1h returns**

The cleaning pipeline computes `returns_1h = pct_change()` on consecutive rows
regardless of actual time delta.  For HF data with raw timestamps, these are
irregular-interval returns (sometimes minutes apart for `day` period data,
sometimes a week for `allTime` data).  This is the same as the daily pipeline
where Hypercore `returns_1h` values are actually ~24h returns.  Downstream
consumers that need uniform 1h returns should use `forward_fill_vault()` first,
which resamples to a regular 1h grid.

**2. Flow metrics share daily granularity across multiple price rows**

Since flow data is aggregated by calendar date, multiple HF price rows on the
same date will carry the same flow values (matched via `.date()`).  Consumers
aggregating flows must deduplicate by date, not by row count.

**3. Two DuckDB files for the same chain**

The daily and HF pipelines use separate DuckDB files. They share the same
`vault_metadata` schema but have different price tables. If both run against the
same Parquet, the last writer's chain-9999 rows win (the merge function deletes
all existing Hypercore rows before appending). Running both pipelines
simultaneously against the same Parquet requires external coordination (the
`scan-pipeline` file lock in `scan_all_chains.py` handles this).

**4. API portfolio resolution is the bottleneck**

The `allTime` period returns ~weekly snapshots regardless of poll frequency.
Only the `day` period (last 24h) has sub-daily resolution. Polling every 1h does
not magically produce 1h-resolution data for the full vault history — it only
captures the freshest data point sooner. Historical data remains as coarse as
the API provides.

**5. Proxy cost and monitoring**

Each Webshare backbone proxy adds cost. The `ProxyStateManager` tracks failures
persistently (SQLite), so bad proxies are skipped in future runs. Monitor the
proxy health via the session's `_rotation_count` and `_request_count` attributes
in logs.
