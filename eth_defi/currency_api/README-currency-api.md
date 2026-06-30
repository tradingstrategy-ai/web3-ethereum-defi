# Currency API historical exchange rate pipeline

## Overview

Incrementally scans daily historical exchange rates for a configurable set of
named currencies (default: EUR, GBP, JPY, AUD, BTC, ETH against USD) and stores them
in a DuckDB database. The data is sourced from the **fawazahmed0 Exchange API**
(`@fawazahmed0/currency-api`).

The schema carries a `source` column so additional rate sources (e.g.
ECB/Frankfurter, CoinGecko, on-chain oracles) can be added later without
disturbing existing rows.

**No authentication is required** — all data comes from a free, public,
no-API-key endpoint with no documented rate limit.

## Architecture

```
fawazahmed0 Exchange API                 Local storage
========================                 =============
jsDelivr CDN (primary)
  cdn.jsdelivr.net/npm/@fawazahmed0/
    currency-api@{date}/v1/
      currencies/usd.min.json
      |
      |  (HTTP 404 / 5xx → fallback host)
      v
pages.dev (Cloudflare fallback)
  {date}.currency-api.pages.dev/v1/
    currencies/usd.min.json
      |
      v
fetch_rates_for_date()                   exchange-rates.duckdb
  one request per date                     exchange_rates table
  returns base vs ~200 currencies          unavailable_rates table
  extract named quote currencies
      |
      v
run_incremental_scan()  --upsert-->      exchange_rates
  completeness-driven (date,quote) set     (date, base, quote, source) PK
  joblib threaded fetch
  records permanent gaps ----------->      unavailable_rates
  (whole-date 404s + missing quotes)       (date, base, quote, source) PK
```

## Data source

| Property | Value |
|----------|-------|
| Provider | fawazahmed0 Exchange API (`@fawazahmed0/currency-api`) |
| Cost | Free, no API key, no documented rate limit |
| Primary URL | `https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{date}/v1/currencies/{base}.min.json` |
| Fallback URL | `https://{date}.currency-api.pages.dev/v1/currencies/{base}.min.json` |
| `{date}` | `latest` or `YYYY-MM-DD` |
| Frequency | Daily |
| Earliest data | ~`2024-03-02` (constant `EARLIEST_AVAILABLE_DATE`) |

### Response shape

A single request for a `base` currency returns that base against ~200 fiat and
crypto currencies:

```json
{
  "date": "2026-06-28",
  "usd": { "eur": 0.87803784, "gbp": 0.75771599, "jpy": 161.75, "btc": 0.000016638, "eth": 0.00063644, ... }
}
```

One HTTP request therefore covers every named currency for that date, so the
scanner parallelises **across dates**, not currencies.

### Rate direction

`usd[quote]` is the number of units of `quote` per **1 unit of base** (e.g.
1 USD = 0.878 EUR). We store this **raw** value. USD-per-unit is the inverse
(`1 / rate`); invert on read. Because both `base_currency` and `quote_currency`
are stored, the direction is always unambiguous.

## DuckDB schema

```
exchange_rates                              unavailable_rates
==============                              =================
date           DATE     \                   date           DATE     \
base_currency  VARCHAR   \                  base_currency  VARCHAR    \
quote_currency VARCHAR    > composite PK    quote_currency VARCHAR     > composite PK
source         VARCHAR   /                  source         VARCHAR    /
rate           DOUBLE                        reason         VARCHAR
written_at     TIMESTAMP                     http_status    INTEGER
                                            checked_at     TIMESTAMP
```

- `rate` — raw API value, `quote` units per 1 `base`.
- `source` — part of the primary key so multiple sources coexist.
- `unavailable_rates` — rate cells confirmed to have no data (older than the grace
  window), tracked at `(date, base, quote, source)` granularity. `reason` is
  `date_404` (whole date 404 on both hosts), `quote_missing` (a single quote
  absent from an otherwise-200 body), or `persistent_error` (gave up after too
  many transient failures). Lets the scanner stop re-fetching genuinely missing
  cells while still retrying transient failures.
- `fetch_attempts` — internal bookkeeping: consecutive transient-failure count
  per `(date, base, source)`, used to give up on a stuck date after
  `MAX_TRANSIENT_ATTEMPTS`. Reset when the date succeeds or returns a 404.

## Incremental, gap-aware scanning

The scanner resume is **completeness-driven**, not `MAX(date)`-driven:

1. Window: `floor = START_DATE or EARLIEST_AVAILABLE_DATE`, `end = END_DATE or today (UTC)`.
2. Missing work = every `(date, quote)` in the window that is not already present in
   `exchange_rates` and not recorded in `unavailable_rates`, plus the last
   `REFETCH_TAIL_DAYS` to refresh recent corrections.
3. This automatically backfills mid-history holes **and the full history of any newly
   added quote currency**.
4. Permanent gaps older than `UNAVAILABLE_GRACE_DAYS` are recorded in `unavailable_rates`:
   whole-date 404s (`reason='date_404'`) and individual quotes absent from a 200 body
   (`reason='quote_missing'`). Recent gaps are treated as "not published yet" and retried.
5. Transient network/5xx failures are never recorded as unavailable; they are retried on
   the next run and cause the script to exit non-zero so cron alerting fires.
6. To stop a genuinely stuck date (a persistent non-404 denial such as 403/410 or a
   permanent 5xx) from retrying and alerting forever, a per-date counter tracks
   consecutive transient failures. After `MAX_TRANSIENT_ATTEMPTS` (default 5) the date is
   given up on — recorded as a permanent gap (`reason='persistent_error'`) and no longer
   fetched. The counter resets as soon as the date succeeds or returns a definitive 404.

## Quick start

The scanner is installed as the `scan-currencies` Poetry console script:

```shell
# Full incremental scan (resume from existing DB, default currencies)
LOG_LEVEL=info poetry run scan-currencies

# Small test batch — a few days only (fast, deterministic)
LOG_LEVEL=info START_DATE=2026-06-01 END_DATE=2026-06-05 \
  poetry run scan-currencies

# Add more currencies (no schema change; history backfills automatically)
QUOTE_CURRENCIES=eur,gbp,jpy,chf,btc,eth,sol \
  poetry run scan-currencies

# Use a non-USD base
BASE_CURRENCY=eur QUOTE_CURRENCIES=usd,gbp,jpy \
  poetry run scan-currencies

# Equivalent module form (no install needed)
poetry run python -m eth_defi.currency_api.cli
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `warning` | Logging level |
| `DB_PATH` | `~/.tradingstrategy/currency-api/exchange-rates.duckdb` | DuckDB database path |
| `BASE_CURRENCY` | `usd` | Base currency |
| `QUOTE_CURRENCIES` | `eur,gbp,jpy,aud,btc,eth` | Comma-separated quote currencies |
| `START_DATE` | *(resume / earliest)* | `YYYY-MM-DD` lower bound (small-batch testing) |
| `END_DATE` | *(today, UTC)* | `YYYY-MM-DD` upper bound (small-batch testing) |
| `MAX_WORKERS` | `8` | Parallel date fetchers (threaded) |
| `REFETCH_TAIL_DAYS` | `3` | Recent days to always re-fetch for corrections |
| `UNAVAILABLE_GRACE_DAYS` | `2` | Age before a 404 is recorded as permanent |
| `MAX_TRANSIENT_ATTEMPTS` | `5` | Consecutive transient failures per date before giving up |
| `SOURCE` | `fawazahmed0` | Value written to the `source` column |

## Key modules

| Module | Role |
|--------|------|
| `eth_defi/currency_api/constants.py` | API URL templates, source name, default/earliest-date constants, default currency tuple |
| `eth_defi/currency_api/session.py` | `requests.Session` factory — retries/backoff transport |
| `eth_defi/currency_api/client.py` | `fetch_rates_for_date()` — fetch, jsDelivr→pages.dev host fallback, parse, outcome classification |
| `eth_defi/currency_api/database.py` | `CurrencyRateDatabase` — DuckDB storage, upserts, gap tracking |
| `eth_defi/currency_api/scanner.py` | `run_incremental_scan()` — completeness-driven incremental orchestration |
| `eth_defi/currency_api/cli.py` | `main()` — env-driven `scan-currencies` Poetry console entry point |

## Running tests

```shell
source .local-test.env && poetry run pytest tests/currency_api/ -x --timeout=300
```

Testing is **black-box, real-network, no mocks**. Each test runs the real
`run_incremental_scan` against the live API over a small bounded date window
(via `START_DATE`/`END_DATE`) into a temporary DuckDB, then asserts on the real
DB state:

- **Limited end-to-end scan** — a 3-day window × the default currencies; asserts
  rows present, `source = 'fawazahmed0'`, sane value bounds, and idempotent re-runs.
- **Incremental gap-fill + reconfiguration** — a partial window then an extended
  window with an extra currency; asserts new dates and the new currency's history
  backfill while existing rows are not duplicated.
- **Missing quote recorded, not retried** — a nonexistent currency code is
  recorded in `unavailable_rates` as a permanent `quote_missing` gap, not a
  transient failure.
- **Transient-attempt counter** — the per-date give-up bookkeeping persists,
  overwrites, clears, and escalates to a `persistent_error` gap.
- **Present/unavailable disjoint** — upserting data for a cell removes any prior
  gap record so the two tables stay mutually exclusive.
- **Script entry point** — invokes the `scan-currencies` entry point
  (`python -m eth_defi.currency_api.cli`) as a subprocess with env vars set,
  asserting exit code 0 and expected DB rows.

Exact rates are never asserted (the source may revise history); only presence,
bounds, idempotency, and gap-filling.
