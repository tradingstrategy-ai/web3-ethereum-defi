# Derive.xyz integration

[Derive.xyz](https://derive.xyz/) (formerly Lyra) is a decentralised perpetuals and options exchange built on Derive Chain (OP Stack L2).

- [Derive API reference](https://docs.derive.xyz/reference/)
- [Funding rate history endpoint](https://docs.derive.xyz/reference/post_public-get-funding-rate-history)
- [All instruments endpoint](https://docs.derive.xyz/reference/post_public-get-all-instruments)

## Funding rate history

The `scan-funding-rates.py` script fetches hourly funding rate snapshots for Derive perpetual instruments and stores them in a local DuckDB database. The scan is resumeable — running it again fetches only new data since the last sync.

The Derive API limits history to 30 days per request, so run the script at least once every 30 days to avoid gaps.

### Quick snapshot (1 day, single instrument)

```shell
LIMIT_DAYS=1 INSTRUMENTS=ETH-PERP poetry run python scripts/derive/scan-funding-rates.py
```

### Full sync (all instruments, 30 days)

```shell
poetry run python scripts/derive/scan-funding-rates.py
```

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
| `LIMIT_DAYS` | Limit history to N days | 30 |
| `DB_PATH` | DuckDB file path | `~/.tradingstrategy/derive/funding-rates.duckdb` |
| `LOG_LEVEL` | Logging level (debug, info, warning, error) | warning |

### Example output

```
Database: /Users/you/.tradingstrategy/derive/funding-rates.duckdb
Instruments: 42
Limit: 30 days (from 2026-02-15 20:00 UTC)

Instrument      New rows    Total rows  Oldest            Newest
------------  ----------  ------------  ----------------  ----------------
BTC-PERP             720           720  2026-02-15 20:00  2026-03-17 19:00
ETH-PERP             720           720  2026-02-15 20:00  2026-03-17 19:00
SOL-PERP             720           720  2026-02-15 20:00  2026-03-17 19:00
...

Total: 30240 new rows, 30240 total rows across 42 instruments
```

### Running tests

```shell
source .local-test.env && poetry run pytest tests/derive/test_funding_rate_history.py -v --timeout=180
```

Tests use the public API (no credentials needed) and fetch a small window of data.
