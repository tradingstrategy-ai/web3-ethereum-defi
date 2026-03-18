# Derive.xyz integration

[Derive.xyz](https://derive.xyz/) (formerly Lyra) is a decentralised perpetuals and options exchange built on Derive Chain (OP Stack L2).

- [Derive API reference](https://docs.derive.xyz/reference/)
- [Funding rate history endpoint](https://docs.derive.xyz/reference/post_public-get-funding-rate-history)
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

## Samples

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