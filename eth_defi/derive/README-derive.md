# Derive.xyz integration

[Derive.xyz](https://derive.xyz/) (formerly Lyra) is a decentralised perpetuals and options exchange built on Derive Chain (OP Stack L2, chain ID 957).

- [Derive API reference](https://docs.derive.xyz/reference/)
- [Funding rate history endpoint](https://docs.derive.xyz/reference/post_public-get-funding-rate-history)
- [Statistics endpoint](https://docs.derive.xyz/reference/post_public-statistics) (open interest)
- [All instruments endpoint](https://docs.derive.xyz/reference/post_public-get-all-instruments)

## Collected data overview

Two datasets are collected and stored in a shared DuckDB database at `~/.tradingstrategy/derive/funding-rates.duckdb`:

| Dataset | Source | Resolution | History |
|---|---|---|---|
| Funding rates | Derive REST API (`get_funding_rate_history`) | Hourly | From instrument inception (~Dec 2023 for BTC/ETH) |
| Open interest + prices | On-chain via Multicall3 on Derive Chain | Hourly | From Multicall3 deployment (2023-12-30) |

Both datasets cover all active perpetual instruments (currently 12: BTC, ETH, SOL, AAVE, DOGE, LINK, OP, SUI, HYPE, BNB, ENA, XRP).

### Example database size (as of 2026-03-20)

A full sync of 12 instruments produces approximately:

| Dataset | Rows | Description |
|---|---|---|
| Funding rates (12 instruments, full history) | ~118,000 | Hourly snapshots from inception |
| Open interest (12 instruments, hourly) | ~158,000 | Hourly OI + perp price + index price |
| **Total** | **~276,000** | **~24 MB on disk** |

The DuckDB file is stored at `~/.tradingstrategy/derive/funding-rates.duckdb`.

## DuckDB schema

### `funding_rates` table

| Column | Type | Description |
|---|---|---|
| `instrument` | `VARCHAR` | Instrument name (e.g. `ETH-PERP`) |
| `ts` | `BIGINT` | Timestamp in milliseconds since epoch |
| `funding_rate` | `DOUBLE` | Hourly funding rate as decimal fraction (e.g. `0.00001234`) |

Primary key: `(instrument, ts)`

### `open_interest` table

| Column | Type | Description |
|---|---|---|
| `instrument` | `VARCHAR` | Instrument name (e.g. `ETH-PERP`) |
| `ts` | `BIGINT` | Timestamp in milliseconds since epoch |
| `open_interest` | `DOUBLE` | OI in base currency units (e.g. ETH for ETH-PERP) |
| `perp_price` | `DOUBLE` | Mark/perp price in USD (18-decimal fixed-point on-chain) |
| `index_price` | `DOUBLE` | Spot/index price in USD (18-decimal fixed-point on-chain) |

Primary key: `(instrument, ts)`

To get OI in USD: `open_interest * index_price`.

### `sync_state` table

Tracks per-instrument watermarks for incremental sync.

| Column | Type | Description |
|---|---|---|
| `instrument` | `VARCHAR` | Instrument name |
| `data_type` | `VARCHAR` | `"funding_rates"` or `"open_interest"` |
| `oldest_ts` | `BIGINT` | Earliest stored timestamp (ms) |
| `newest_ts` | `BIGINT` | Latest stored timestamp (ms) |
| `row_count` | `INTEGER` | Number of stored rows |
| `last_synced` | `BIGINT` | When the sync last ran (ms) |

Primary key: `(instrument, data_type)`

## Scripts

### `scripts/derive/scan-funding-rates.py`

Fetches hourly funding rate snapshots from the Derive REST API. On first run, auto-detects each instrument's inception date via binary search and fetches the full history in 28-day chunks. Subsequent runs resume from the last stored timestamp.

```shell
# Full sync (all instruments, full history, ~2-3 min first run)
poetry run python scripts/derive/scan-funding-rates.py

# Quick snapshot (1 day, single instrument)
LIMIT_DAYS=1 INSTRUMENTS=ETH-PERP poetry run python scripts/derive/scan-funding-rates.py
```

| Variable | Description | Default |
|---|---|---|
| `INSTRUMENTS` | Comma-separated instrument names | All active perps (auto-discovered) |
| `LIMIT_DAYS` | Limit history to N days | Not set (full history / resume) |
| `DB_PATH` | DuckDB file path | `~/.tradingstrategy/derive/funding-rates.duckdb` |
| `LOG_LEVEL` | Logging level (debug, info, warning, error) | warning |

### `scripts/derive/scan-open-interest.py`

Fetches hourly open interest, perp price, and index price by reading on-chain state from the Derive Chain archive node via parallel Multicall3 reads. On first run, fetches the full history from each instrument's activation date. Subsequent runs resume incrementally.

The script prints current DB size, existing entries, scan start/end dates, and a running rows-written counter in the progress bar.

```shell
# Full sync (all instruments, ~20 min first backfill with 2 workers)
poetry run python scripts/derive/scan-open-interest.py

# Sync specific instruments
INSTRUMENTS=ETH-PERP,BTC-PERP poetry run python scripts/derive/scan-open-interest.py

# More parallelism (faster but more RPC load)
MAX_WORKERS=8 poetry run python scripts/derive/scan-open-interest.py
```

| Variable | Description | Default |
|---|---|---|
| `INSTRUMENTS` | Comma-separated instrument names | All active perps (auto-discovered) |
| `DB_PATH` | DuckDB file path | `~/.tradingstrategy/derive/funding-rates.duckdb` |
| `LOG_LEVEL` | Logging level (debug, info, warning, error) | warning |
| `DERIVE_RPC_URL` | Derive Chain JSON-RPC URL | `https://rpc.derive.xyz` |
| `MAX_WORKERS` | Number of parallel worker processes for RPC reads | 2 |

## Python API

The `DeriveFundingRateDatabase` class in `eth_defi.derive.historical` provides both sync and read methods:

```python
from eth_defi.derive.historical import DeriveFundingRateDatabase

db = DeriveFundingRateDatabase()

# Read funding rates as Pandas DataFrame
# Columns: timestamp (datetime64), instrument (str), funding_rate (float64)
fr_df = db.get_funding_rates_dataframe("ETH-PERP", start_time=datetime.datetime(2025, 1, 1))

# Read open interest as Pandas DataFrame
# Columns: timestamp (datetime64), instrument (str), open_interest (float64),
#           perp_price (float64), index_price (float64)
oi_df = db.get_open_interest_dataframe("ETH-PERP", start_time=datetime.datetime(2025, 1, 1))

# OI in USD
oi_df["oi_usd"] = oi_df["open_interest"] * oi_df["index_price"]

db.close()
```

## Tutorial notebook

A Jupyter notebook demonstrating OI and funding rate visualisation is at:

```
docs/source/tutorials/derive-open-interest-funding-rate.ipynb
```

Run with:

```shell
poetry run jupyter execute docs/source/tutorials/derive-open-interest-funding-rate.ipynb --inplace --timeout=900
```

The notebook displays:
- Summary metrics table (hours positive/negative, annualised rate, cumulative funding, OI in USD)
- BTC-PERP and ETH-PERP dual-axis charts (OI in USD + daily mean funding rate)

## Running tests

```shell
# Funding rate tests
source .local-test.env && poetry run pytest tests/derive/test_funding_rate_history.py -v --timeout=180

# Open interest tests
source .local-test.env && poetry run pytest tests/derive/test_open_interest_history.py -v --timeout=180
```

Tests use the public Derive Chain RPC and REST API (no credentials needed).

## API quirks

### Funding rate API (discovered 2026-03-18)

- **Parameter names**: The API requires `start_timestamp` / `end_timestamp` (not `start_time` / `end_time` as the docs suggest). Using the wrong names silently falls back to the most recent 30 days.
- **Maximum window**: The API returns empty results for windows >= 30 days. The maximum usable window is **28 days** (29 days also works but 28 is a safe round number).
- **Pagination ignored**: `page` and `page_size` parameters are accepted but have no effect — the API always returns all entries in the requested time window.
- **Full history available**: Despite the docs claiming a 30-day limit, the correct parameter names allow querying data back to instrument inception (ETH-PERP since 2024-01-05, ~800+ days).
- **Hourly resolution**: Data is returned at 1-hour intervals. A 28-day chunk returns ~672 entries.

### Statistics API / open interest (discovered 2026-03-19)

- **Statistics endpoint ignores timestamps**: The `POST /public/statistics` endpoint accepts `end_time` and `end_timestamp` parameters but always returns the current live open interest. Tested with millisecond timestamps, second timestamps, and both parameter names — all return identical current values. This is why on-chain reads are used instead.
- **Function selectors**: The correct selector for `openInterest(uint256)` is `0x88e53ec8` (the parameterless `openInterest()` selector `0xfa5a2e62` reverts). `getPerpPrice()` is `0x90f76b18` and `getIndexPrice()` is `0x58c0994a` — both return `(uint256 price, uint256 confidence)` tuples.
- **Error handling**: Only `ContractLogicError` (contract revert at pre-deployment blocks) is caught. Transient RPC errors (timeouts, rate limits) propagate to prevent silent holes in history — the sync loop aborts rather than advancing the watermark past missing data.

## On-chain data source details

The open interest scanner reads state directly from perp contracts on Derive Chain:

| Data point | Contract function | Selector | Description |
|---|---|---|---|
| `open_interest` | `openInterest(uint256 subId)` | `0x88e53ec8` | OI in base currency (e.g. ETH for ETH-PERP) |
| `perp_price` | `getPerpPrice()` | `0x90f76b18` | Mark/perp price in USD |
| `index_price` | `getIndexPrice()` | `0x58c0994a` | Spot/index price in USD |

- **Contract addresses**: `base_asset_address` from `GET /public/get_all_instruments`
- **RPC endpoint**: `https://rpc.derive.xyz` (Derive Chain, chain ID 957)
- **Archive support**: full history from chain genesis
- **Block estimation**: linear interpolation at 2s/block
- **Multicall3 batching**: all instruments batched into one `tryBlockAndAggregate` call per hour via `read_multicall_historical` with parallel workers
- **Multicall3 address**: `0xcA11bde05977b3631167028862bE2a173976CA11` (deployed at block 1,935,198 on 2023-12-29)
- **History start**: clamped to Multicall3 deployment (2023-12-30) — the ~22 days between earliest instrument activation and Multicall3 deployment are skipped
- **Checkpointing**: sync state flushed every 500 hours so crash-resume restarts close to where it left off

## Performance (benchmarked 2026-03-19)

| Operation | Time | Details |
|---|---|---|
| Full funding rate sync (12 instruments) | ~2-3 min | REST API, 2 req/s, 28-day chunks |
| Full OI backfill (12 instruments, ~800 days) | ~20 min | On-chain, 2 workers, hourly resolution |
| Incremental sync (either dataset) | seconds | Only fetches new data since last sync |

Bottleneck is RPC latency (~1.4s per multicall round-trip). Increase `MAX_WORKERS` for faster backfill at the cost of more RPC load.

## Sample output

### Funding rate scan (12 instruments)

```
Instrument      New rows    Total rows  Oldest            Newest
------------  ----------  ------------  ----------------  ----------------
AAVE-PERP           6044          6044  2025-06-17 00:00  2026-03-19 03:00
BNB-PERP            3906          3906  2025-08-12 14:00  2026-03-18 04:00
BTC-PERP           19833         19833  2023-12-14 19:00  2026-03-20 15:00
DOGE-PERP          10760         10760  2024-10-24 21:00  2026-03-17 16:00
ENA-PERP           10565         10565  2024-12-01 11:00  2026-03-19 21:00
ETH-PERP           19311         19311  2024-01-05 15:00  2026-03-20 15:00
HYPE-PERP           3289          3289  2025-11-03 15:00  2026-03-20 15:00
LINK-PERP           1515          1515  2025-12-31 23:00  2026-03-16 10:00
OP-PERP            11139         11139  2024-11-02 18:00  2026-03-16 10:00
SOL-PERP           14484         14484  2024-07-21 22:00  2026-03-20 15:00
SUI-PERP           11468         11468  2024-11-03 01:00  2026-03-20 05:00
XRP-PERP            5627          5627  2025-07-06 10:00  2026-03-18 03:00

Total: 117,941 rows across 12 instruments
```

### Open interest scan (12 instruments, hourly)

```
Database: /Users/moo/.tradingstrategy/derive/funding-rates.duckdb
Instruments: 12
Derive Chain RPC: https://rpc.derive.xyz (block 36,967,157)
Workers: 2
Scan end: 2026-03-19 21:32 UTC

Current DB size: 21.0 MB, existing entries: 158,368

Instrument      New rows    Total rows  Oldest            Newest
------------  ----------  ------------  ----------------  ----------------
AAVE-PERP          12272         12272  2024-10-11 00:00  2026-03-19 21:00
BNB-PERP           12293         12293  2024-10-23 00:00  2026-03-19 21:00
BTC-PERP           19462         19462  2023-12-30 00:00  2026-03-19 21:00
DOGE-PERP          16484         16484  2024-05-02 00:00  2026-03-19 21:00
ENA-PERP           11362         11362  2024-12-01 00:00  2026-03-19 21:00
ETH-PERP           19302         19302  2024-01-05 00:00  2026-03-19 21:00
HYPE-PERP           3354          3354  2025-10-30 00:00  2026-03-19 21:00
LINK-PERP          11330         11330  2024-12-02 00:00  2026-03-19 21:00
OP-PERP            12379         12379  2024-10-11 00:00  2026-03-19 21:00
SOL-PERP           16484         16484  2024-05-02 00:00  2026-03-19 21:00
SUI-PERP           12293         12293  2024-10-23 00:00  2026-03-19 21:00
XRP-PERP           11353         11353  2024-12-01 00:00  2026-03-19 21:00

Total: 158,368 new rows, 158,368 total rows across 12 instruments
```

### Example metrics (from tutorial notebook, 2025-01-01 onwards)

```
                          BTC-PERP     ETH-PERP
Hours positive               8,718        8,987
Hours negative               1,918        1,650
% hours positive             82.0%        84.5%
Mean hourly rate        0.00001250   0.00001131
Annualised rate             10.95%        9.91%
Current OI (USD)       $25,622,330   $7,181,877
Peak OI (USD)          $26,845,258  $22,495,469
Mean OI (USD)          $11,685,941   $8,303,178
```
