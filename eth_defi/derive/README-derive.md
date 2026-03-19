# Derive.xyz integration



[Derive.xyz](https://derive.xyz/) (formerly Lyra) is a decentralised perpetuals and options exchange built on Derive Chain (OP Stack L2).

- [Derive API reference](https://docs.derive.xyz/reference/)
- [Funding rate history endpoint](https://docs.derive.xyz/reference/post_public-get-funding-rate-history)
- [Statistics endpoint](https://docs.derive.xyz/reference/post_public-statistics) (open interest)
- [All instruments endpoint](https://docs.derive.xyz/reference/post_public-get-all-instruments)

## Funding rate history

The `scan-funding-rates.py` script fetches hourly funding rate snapshots for Derive perpetual instruments and stores them in a local DuckDB database. On first run it auto-detects each instrument's inception date and fetches the full available history. Subsequent runs resume from the last stored timestamp.

### API quirks (discovered 2026-03-18)

The Derive `get_funding_rate_history` endpoint has several undocumented behaviours:

- **Parameter names**: The API requires `start_timestamp` / `end_timestamp` (not `start_time` / `end_time` as the docs suggest). Using the wrong names silently falls back to the most recent 30 days.
- **Maximum window**: The API returns empty results for windows >= 30 days. The maximum usable window is **28 days** (29 days also works but 28 is a safe round number).
- **Pagination ignored**: `page` and `page_size` parameters are accepted but have no effect — the API always returns all entries in the requested time window.
- **Full history available**: Despite the docs claiming a 30-day limit, the correct parameter names allow querying data back to instrument inception (ETH-PERP since 2024-01-05, ~800+ days).
- **Hourly resolution**: Data is returned at 1-hour intervals. A 28-day chunk returns ~672 entries.

### Quick snapshot (1 day, single instrument)

```shell
LIMIT_DAYS=1 INSTRUMENTS=ETH-PERP poetry run python scripts/derive/scan-funding-rates.py
```

### Full sync (all instruments, full history)

```shell
poetry run python scripts/derive/scan-funding-rates.py
```

On first run, the script probes each instrument's inception date via binary search (~10 API calls per instrument), then fetches the full history in 28-day chunks. For 12 instruments with ~800 days of history each, the initial sync takes roughly 2–3 minutes at 2 requests/second.

### With verbose logging

```shell
LOG_LEVEL=info poetry run python scripts/derive/scan-funding-rates.py
```

### Custom database path

```shell
DB_PATH=/tmp/derive-funding.duckdb poetry run python scripts/derive/scan-funding-rates.py
```

### Environment variables

| Variable | Description | Default |
|---|---|---|
| `INSTRUMENTS` | Comma-separated instrument names | All active perps (auto-discovered) |
| `LIMIT_DAYS` | Limit history to N days | Not set (full history / resume) |
| `DB_PATH` | DuckDB file path | `~/.tradingstrategy/derive/funding-rates.duckdb` |
| `LOG_LEVEL` | Logging level (debug, info, warning, error) | warning |

### Running tests

```shell
source .local-test.env && poetry run pytest tests/derive/test_funding_rate_history.py -v --timeout=180
```

Tests use the public API (no credentials needed). The `test_fetch_early_history` test verifies that data from 6 months ago is accessible.

## Perp daily snapshots (open interest + prices)

The `scan-open-interest.py` script fetches daily snapshots for Derive perpetual instruments by reading on-chain state from the Derive Chain archive node. Each daily snapshot collects three data points per instrument in a single Multicall3 call:

| Data point | Contract function | Description |
|---|---|---|
| `open_interest` | `openInterest(uint256 subId)` (selector `0x88e53ec8`) | OI in base currency (e.g. ETH for ETH-PERP) |
| `perp_price` | `getPerpPrice()` (selector `0x90f76b18`) | Mark/perp price in USD |
| `index_price` | `getIndexPrice()` (selector `0x58c0994a`) | Spot/index price in USD |

All values are 18-decimal fixed-point on-chain. Stored in the `open_interest` DuckDB table alongside the funding rates database.

On first run it fetches the full available history from each instrument's activation date. Subsequent runs resume from the last stored timestamp.

### Data source: on-chain via Derive Chain archive node

The `POST /public/statistics` REST endpoint ignores `end_time` for `open_interest` — it always returns the current live value. Historical data is read directly from the on-chain perp contracts instead:

- **Contract functions**: `openInterest(uint256)`, `getPerpPrice()`, `getIndexPrice()`
- **Contract address**: `base_asset_address` from `GET /public/get_all_instruments`
- **RPC endpoint**: `https://rpc.derive.xyz` (Derive Chain, chain ID 957)
- **Archive support**: full history from chain genesis (~December 2023 for ETH/BTC-PERP)
- **Resolution**: daily (one multicall per day across all instruments, 3 subcalls per instrument)
- **Block estimation**: linear interpolation at 2s/block — accurate to within seconds
- **Multicall3**: all instruments batched into a single `aggregate3` call per day via Multicall3 at `0xcA11bde05977b3631167028862bE2a173976CA11` (deployed at block 1,935,198 on 2023-12-29)
- **History start**: clamped to Multicall3 deployment (2023-12-30) — the ~22 days between earliest instrument activation and Multicall3 deployment are skipped

### API quirks (discovered 2026-03-19)

- **Statistics endpoint ignores timestamps**: The `POST /public/statistics` endpoint accepts `end_time` and `end_timestamp` parameters but always returns the current live open interest. Tested with millisecond timestamps, second timestamps, and both parameter names — all return identical current values. This is why on-chain reads are used instead.
- **Function selectors**: The correct selector for `openInterest(uint256)` is `0x88e53ec8` (the parameterless `openInterest()` selector `0xfa5a2e62` reverts). `getPerpPrice()` is `0x90f76b18` and `getIndexPrice()` is `0x58c0994a` — both return `(uint256 price, uint256 confidence)` tuples.
- **Error handling**: Only `ContractLogicError` (contract revert at pre-deployment blocks) is caught. Transient RPC errors (timeouts, rate limits) propagate to prevent silent holes in history — the sync loop aborts rather than advancing the watermark past missing data.

### Performance (benchmarked 2026-03-19)

- **Full 2-instrument backfill** (ETH-PERP + BTC-PERP, ~811 days each): 1,615 rows in 19m42s
- **Bottleneck**: RPC latency (~1.4s per multicall round-trip), not compute
- **Multicall benefit**: 1 RPC call per day regardless of instrument count (vs N calls without batching)
- **Incremental runs**: only fetch new days since last sync, typically completing in seconds

### Full historical sync (all instruments, from inception)

```shell
poetry run python scripts/derive/scan-open-interest.py
```

On first run, fetches ~800+ days of history for each instrument via daily Multicall3 batches (3 subcalls per instrument per day: OI, perp price, index price). Subsequent runs add only the new days since the last stored snapshot.

### Sync specific instruments

```shell
INSTRUMENTS=ETH-PERP,BTC-PERP poetry run python scripts/derive/scan-open-interest.py
```

### Environment variables

| Variable | Description | Default |
|---|---|---|
| `INSTRUMENTS` | Comma-separated instrument names | All active perps (auto-discovered) |
| `DB_PATH` | DuckDB file path | `~/.tradingstrategy/derive/funding-rates.duckdb` |
| `LOG_LEVEL` | Logging level (debug, info, warning, error) | warning |
| `DERIVE_RPC_URL` | Derive Chain JSON-RPC URL | `https://rpc.derive.xyz` |

### Running tests

```shell
source .local-test.env && poetry run pytest tests/derive/test_open_interest_history.py -v --timeout=180
```

Tests use the public Derive Chain RPC and REST API (no credentials needed).

## Samples

### Funding rate history

```
Instrument      New rows    Total rows  Oldest            Newest
------------  ----------  ------------  ----------------  ----------------
AAVE-PERP          11200         11200  2024-11-02 16:00  2026-03-17 22:00
BNB-PERP            4014          4014  2025-08-12 14:00  2026-03-18 04:00
BTC-PERP           19782         19782  2023-12-14 19:00  2026-03-18 12:00
DOGE-PERP          11358         11358  2024-10-16 22:00  2026-03-17 16:00
ENA-PERP           10482         10482  2024-12-01 11:00  2026-03-16 10:00
ETH-PERP           19260         19260  2024-01-05 15:00  2026-03-18 12:00
HYPE-PERP           3193          3193  2025-11-05 12:00  2026-03-18 12:00
LINK-PERP          10316         10316  2024-12-02 18:00  2026-03-16 10:00
OP-PERP            11139         11139  2024-11-02 18:00  2026-03-16 10:00
SOL-PERP           14433         14433  2024-07-21 22:00  2026-03-18 12:00
SUI-PERP           11377         11377  2024-11-03 01:00  2026-03-16 10:00
XRP-PERP            2714          2714  2025-11-03 10:00  2026-03-18 03:00
```

### Open interest history

```
Instrument      New rows    Total rows  Oldest      Newest
------------  ----------  ------------  ----------  ----------
BTC-PERP             811           811  2023-12-30  2026-03-19
ETH-PERP             804           804  2024-01-06  2026-03-19
```