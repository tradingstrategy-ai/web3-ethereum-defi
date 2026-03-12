# Hyperliquid trade event history

## Overview

Per-account trade event storage for Hyperliquid accounts (vaults and normal
addresses). Stores fills, funding payments, and ledger events in a DuckDB
database with incremental sync that accumulates data beyond the 10K fill
API limit.

Separate from the daily metrics pipeline (`daily-metrics.duckdb`) which
tracks aggregate vault statistics. This database stores raw event-level
data for trade history reconstruction.

## Storage location

Default: `~/.tradingstrategy/hyperliquid/trade-history.duckdb`

Override with the `TRADE_HISTORY_DB_PATH` environment variable.

## DuckDB schema

Five tables: `accounts`, `fills`, `funding`, `ledger`, `sync_state`.

### accounts

Whitelisted addresses to track. Only accounts explicitly added here are
synced -- no data is fetched for unlisted addresses.

| Column | Type | Notes |
|--------|------|-------|
| `address` | VARCHAR | **PK**. Lowercase hex address |
| `label` | VARCHAR | Human-readable name (e.g. "Growi HF") |
| `is_vault` | BOOLEAN | True for vault accounts, False for normal |
| `added_at` | BIGINT | Epoch milliseconds when added |

### fills

Individual trade fills from the `userFillsByTime` API endpoint. Each row
is a single fill execution (partial or full order fill).

| Column | Type | Notes |
|--------|------|-------|
| `address` | VARCHAR | Account address |
| `trade_id` | BIGINT | **PK** (with address). Unique `tid` from API |
| `ts` | BIGINT | Epoch milliseconds |
| `coin` | VARCHAR | Market symbol (e.g. "BTC", "ETH") |
| `side` | TINYINT | 0 = buy, 1 = sell |
| `sz` | FLOAT | Fill size in base units |
| `px` | FLOAT | Fill price in USD |
| `closed_pnl` | FLOAT | Realised PnL from this fill (USD) |
| `start_position` | FLOAT | Position size before this fill |
| `fee` | FLOAT | Trading fee (USD) |
| `oid` | BIGINT | Order ID |

Primary key: `(address, trade_id)`.

### funding

Hourly funding payments from the `userFunding` API endpoint. Funding is
settled every hour for each open position.

| Column | Type | Notes |
|--------|------|-------|
| `address` | VARCHAR | Account address |
| `ts` | BIGINT | Epoch milliseconds |
| `coin` | VARCHAR | Market symbol |
| `usdc` | FLOAT | Payment amount (negative = paid, positive = received) |
| `sz` | FLOAT | Position size at funding time |
| `rate` | FLOAT | Funding rate applied |

Primary key: `(address, ts, coin)`.

### ledger

Non-funding ledger events from the `userNonFundingLedgerUpdates` API
endpoint. Covers deposits, withdrawals, vault distributions, and leader
commissions.

| Column | Type | Notes |
|--------|------|-------|
| `address` | VARCHAR | Account address |
| `ts` | BIGINT | Epoch milliseconds |
| `event_type` | VARCHAR | Event type (vault_create, deposit, withdraw, etc.) |
| `usdc` | FLOAT | USD amount |
| `vault` | VARCHAR | Target vault address (for user-to-vault flows) |

Primary key: `(address, ts, event_type)`.

### sync_state

Per-account watermarks for incremental sync. Tracks the time range of
stored data and when each data type was last synced.

| Column | Type | Notes |
|--------|------|-------|
| `address` | VARCHAR | Account address |
| `data_type` | VARCHAR | `fills`, `funding`, or `ledger` |
| `oldest_ts` | BIGINT | Earliest epoch ms stored |
| `newest_ts` | BIGINT | Most recent epoch ms stored |
| `row_count` | INTEGER | Total rows for this address + data type |
| `last_synced` | BIGINT | When sync last ran (epoch ms) |

Primary key: `(address, data_type)`.

## How events are filled

### Data sources

| Data type | API endpoint | Page size | Hard cap |
|-----------|-------------|-----------|----------|
| Fills | `userFillsByTime` | 2,000 | 10K most recent per account |
| Funding | `userFunding` | 500 | No known hard cap |
| Ledger | `userNonFundingLedgerUpdates` | 2,000 | No known hard cap |

### Incremental sync strategy

The sync is designed to be **safe to interrupt at any point** and resume
without data loss or corruption.

1. **First run**: fetch all available data (fills up to 10K, all funding
   and ledger events). Store records and write timestamps to `sync_state`.

2. **Subsequent runs**: fetch only data newer than `newest_ts` in
   `sync_state`. Append with `INSERT OR IGNORE` on primary keys.
   Update `sync_state` after each batch.

3. **Over time**: the database accumulates more than 10K fills for active
   accounts as the API window slides forward with each sync.

### Forward pagination

All three API endpoints return records in **ascending order** (oldest
first). Pagination advances forward:

```
current_start_ms = sync_state.newest_ts (or 1 year ago on first run)

while current_start_ms < end_ms:
    batch = fetch(startTime=current_start_ms, endTime=end_ms)
    INSERT OR IGNORE into DuckDB
    update sync_state
    current_start_ms = max(batch timestamps) + 1
    if len(batch) < page_size: break
```

### Crash safety

- Each data type (fills, funding, ledger) is synced independently per
  account. If a crash occurs mid-sync, only the current data type for
  the current account has a partial batch.
- `INSERT OR IGNORE` on primary keys means partial batches can be
  re-inserted safely on restart without duplicates.
- `sync_state` is updated after each successful batch insert, so on
  resume we re-fetch at most one batch of overlap.

### Thread safety

When `max_workers > 1`, multiple accounts are synced concurrently:

- API calls run in parallel (the HTTP session's rate limiter is shared
  across threads via a SQLite-backed adapter).
- Database writes are serialised via an internal `threading.Lock`.
- Each insert batch uses per-address `COUNT(*)` queries to correctly
  track inserted row counts under concurrent access.

## Storage estimates

| Data type | Rows/year (active vault) | Compressed size/year |
|-----------|-------------------------|---------------------|
| Fills | 5,000--50,000 | 0.4--4 MB |
| Funding | ~8,760 (hourly) | ~0.3 MB |
| Ledger | 100--1,000 | <0.1 MB |

For 50 whitelisted accounts: **50--250 MB/year** total.

## Usage

### Sync trade history

```shell
# Sync specific addresses
ADDRESSES=0x1e37a337ed460039d1b15bd3bc489de789768d5e,0x3df9769bbbb335340872f01d8157c779d73c6ed0 \
  LABELS="Growi HF,IchiV3 LS" \
  poetry run python scripts/hyperliquid/sync-trade-history.py

# With parallel workers and logging
ADDRESSES=0x1e37a337ed460039d1b15bd3bc489de789768d5e \
  MAX_WORKERS=4 LOG_LEVEL=info \
  poetry run python scripts/hyperliquid/sync-trade-history.py
```

### Display trade history

```shell
ADDRESS=0x1e37a337ed460039d1b15bd3bc489de789768d5e \
  DAYS=30 LOG_LEVEL=info \
  poetry run python scripts/hyperliquid/vault-trade-history.py
```

### Inspect database

```shell
poetry run python -c "
from eth_defi.hyperliquid.trade_history_db import HyperliquidTradeHistoryDatabase, DEFAULT_TRADE_HISTORY_DB_PATH

db = HyperliquidTradeHistoryDatabase(DEFAULT_TRADE_HISTORY_DB_PATH)
for account in db.get_accounts():
    state = db.get_sync_state(account['address'])
    print(f\"{account['label'] or account['address'][:16]}:\")
    for dtype, s in state.items():
        print(f\"  {dtype}: {s['row_count']} rows, newest={s['newest_ts']}\")
db.close()
"
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ADDRESSES` | *(none)* | Comma-separated addresses to add and sync |
| `LABELS` | *(none)* | Comma-separated labels matching `ADDRESSES` |
| `TRADE_HISTORY_DB_PATH` | `~/.tradingstrategy/hyperliquid/trade-history.duckdb` | DuckDB path |
| `MAX_WORKERS` | `1` | Parallel workers for concurrent API calls |
| `LOG_LEVEL` | `warning` | Logging level |

## Key modules

| Module | Role |
|--------|------|
| `eth_defi/hyperliquid/trade_history_db.py` | DuckDB persistence, incremental sync, thread-safe writes |
| `eth_defi/hyperliquid/trade_history.py` | Trade history reconstruction, round-trip trades, funding |
| `eth_defi/hyperliquid/position.py` | Fill dataclass, position event reconstruction |
| `eth_defi/hyperliquid/session.py` | Rate-limited HTTP session (thread-safe) |

## Tests

```shell
# Unit tests (no network)
poetry run pytest tests/hyperliquid/test_trade_history.py -x --timeout=300

# Integration tests (requires network)
source .local-test.env && poetry run pytest \
  tests/hyperliquid/test_trade_history_integration.py \
  -x --timeout=300 --log-cli-level=info
```
