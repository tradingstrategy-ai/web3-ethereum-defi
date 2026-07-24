# ApeX native vault reader

## Overview

This package reads every native [ApeX Omni](https://www.apex.exchange/) vault
from the exchange's public web API and stores metadata, current observations
and recoverable history in a standalone DuckDB database.

The integration is reader-only:

- no authentication or private account data;
- no deposits, withdrawals or trading;
- no merge into the unified ERC-4626 parquet or metadata pickle;
- no global vault-scanner registration; and
- no assumption that the Ethereum address reported by ApeX uniquely identifies
  a vault.

The reader is designed for a default four-hour observation schedule, but actual
timestamps are stored without buckets. Changing the schedule later requires no
database migration.

## Canonical links

- [ApeX homepage](https://www.apex.exchange/)
- [ApeX Omni application](https://omni.apex.exchange/)
- [ApeX public API documentation](https://api-docs.pro.apex.exchange/)
- [Official Python SDK](https://github.com/ApeX-Protocol/apexpro-openapi)

The two vault web-application endpoints used here are public but are not
currently described in the official OpenAPI documentation or SDK. Their
response shapes were verified directly against the live application API on
2026-07-23 and are captured in fixture-based tests.

## Architecture

```text
ApeX public web API
===================

/api/v3/vault/ranking
  zero-based paginated listing
  metadata + current NAV/TVL/share count
             |
             | two complete membership-stable passes
             v
      ApexVaultSummary records
             |
             +--------------------------+
             |                          |
             v                          v
      vault_metadata              ranking_snapshot
                                      rows

/api/v3/vault/fund-net-values?vaultId=...
  one bounded history response per vault
  exact timestamp + NAV + total value
             |
             | threaded HTTP reads,
             | serial DuckDB writes
             v
      fund_net_values rows
             |
             v
  ~/.tradingstrategy/vaults/apex-vaults.duckdb
      vault_metadata
      vault_prices
      history_sync
```

One command owns both paths. The command schedule controls current ranking
observations, while a persisted independent gate controls history maintenance.
There is no separate high-frequency database or process.

## Synthetic chain and vault identity

ApeX native vaults use synthetic chain ID `9995`
(`APEX_CHAIN_ID`). It is a dataset namespace, not an EVM JSON-RPC chain.

The platform's string `vaultId` is the database identity. Each vault receives
the stable synthetic address:

```text
apex-vault-{vaultId}
```

`vaultEthAddress` is metadata only. Live data shows that multiple platform
vault IDs may share the same reported Ethereum address, so using it as a
database key would merge unrelated vaults.

## Public endpoints

### Ranking

```text
GET https://omni.apex.exchange/api/v3/vault/ranking?page={page}&limit={limit}
```

Pages start at zero. Important response fields are:

| Field | Stored as | Notes |
|-------|-----------|-------|
| `vaultId` | `vault_id` | Stable platform identity |
| `vaultEthAddress` | `reported_ethereum_address` | Non-unique metadata |
| `name` | `name` | Display name |
| `desc` | `description` | Strategy description |
| `status` | `status` | Raw lifecycle status |
| `collectVaultType` | `vault_type` | Raw source type |
| `vaultNetValue` | `current_nav` / `share_price` | `DOUBLE` |
| `tvl` | `current_tvl` / `total_assets` | `DOUBLE` |
| `share` | `current_share_count` / `total_supply` | `DOUBLE` |
| `createdTime` | `created_at` | Milliseconds, naive UTC |
| `updatedTime` | `source_updated_at` | Milliseconds, naive UTC |
| `finishedTime` | `finished_at` | Zero means unavailable |

The reader performs two complete passes before writing. Each pass must have a
stable `totalSize`, the expected row count and no duplicate IDs. Both passes
must have the same ID set. Metric values are taken from the second pass because
they can legitimately change while the listing is read.

This is a stabilised paginated read, not an atomic source snapshot: ApeX does
not expose a snapshot token.

### Fund net values

```text
GET https://omni.apex.exchange/api/v3/vault/fund-net-values?vaultId={vaultId}
```

The response contains one `data.timeValue` array:

| Field | Stored as | Notes |
|-------|-----------|-------|
| `timestamp` | `timestamp` | Exact milliseconds, naive UTC |
| `netValue` | `share_price` | `DOUBLE` |
| `totalValue` | `total_assets` | `DOUBLE` |
| derived | `total_supply` | `totalValue / netValue` when NAV is positive |

The live endpoint exposes no cursor, page, limit, time range or completeness
token. A first scan can therefore recover only the history the endpoint still
returns. `history_sync` records both the latest response bounds and cumulative
retained bounds. Historical rows leave `source_updated_at` null because this
endpoint does not report a separate update time.

Observed spacing is age-adaptive rather than fixed. Recent vault history may be
hourly, while older history may become daily or weekly. The reader never
interpolates, forward-fills, rounds or resamples these source timestamps.

## Status handling

Only the verified `VAULT_FINISHED` status is terminal. Every other value,
including `VAULT_IN_PROCESS`, `VAULT_INITIAL_FAILED`,
`VAULT_PAUSE_PURCHASE` and future unknown values, is treated as non-terminal.
This fail-open classification is limited to data collection: it ensures an
unrecognised status continues to receive observations and history maintenance.

A terminal vault receives one final non-empty history sync. If it later becomes
non-terminal, its terminal generation is cleared; a later finish starts a new
generation. Unfiltered scans similarly track disappeared and reappeared vaults
without deleting their stored history.

## Scheduling and history modes

The default ranking cadence is four hours. The ranking endpoint itself was
observed to refresh approximately every 30 seconds, so a separate high-frequency
reader is unnecessary for the requested dataset. `run_scan()` records a current
observation whenever called; the standalone command owns the interval.

History refresh defaults to 24 hours and supports three modes:

| Mode | Behaviour |
|------|-----------|
| `incremental` | Backfill new vaults and refresh due non-terminal vaults |
| `refresh` | Immediately re-fetch all selected recoverable histories |
| `none` | Store ranking metadata and current observations only |

All history writes are append-and-correct. Returned timestamps replace earlier
values at the same logical key, but a later shortened or empty response never
deletes timestamps that the source omitted.

Durations accept positive decimal seconds, minutes, hours and days, for example
`30s`, `30m`, `1.5h` and `2d`.

## DuckDB storage

The default database path is:

```text
~/.tradingstrategy/vaults/apex-vaults.duckdb
```

| Table | Logical key | Purpose |
|-------|-------------|---------|
| `vault_metadata` | `vault_id` | Current source metadata and lifecycle |
| `vault_prices` | `(vault_id, timestamp)` | Historical and ranking values |
| `history_sync` | `vault_id` | Attempt, retained-range and finalisation state |

The tables deliberately have no `PRIMARY KEY` or `UNIQUE` constraints. DuckDB
1.5.0 ART indexes can corrupt file-backed databases under Python 3.14 on macOS
ARM64. The writer enforces logical keys with staged transactional
`DELETE` + `INSERT`, disables automatic WAL checkpoints and checkpoints once
after a completed scan.

HTTP fetches run in worker threads, but workers never access DuckDB. The
creating thread performs every write, checkpoint and close operation.

## Numeric representation

NAV, TVL, share count and derived supply are parsed as finite Python `float`
values and stored as DuckDB `DOUBLE`. Tests use approximate comparisons to
account for normal binary floating-point rounding.

The units of `purchaseFeeRate` and `shareProfitRatio` are not authoritatively
documented. They remain nullable raw strings and are not exposed as typed
percentages.

## Bounded HTTP behaviour

Every worker owns a private `requests.Session`; sessions share one process-wide
rate limiter. Network reads have:

- finite connect and inactivity timeouts;
- monotonic request and enclosing operation budgets checked between phases and
  streamed chunks;
- explicit bounded retries with capped `Retry-After`;
- a maximum streamed JSON response size; and
- response closure on success and failure.

The synchronous `requests` read timeout is an inactivity timeout, not a hard
wall-clock deadline. A server that continuously drips bytes without completing
a streamed chunk can delay budget detection until the socket read yields; the
finite inactivity timeout remains the outer bound for a stalled read.

History worker sessions are closed after every scan cycle. The calling thread's
ranking session is retained until command shutdown so loop mode does not
accumulate connection pools from completed joblib workers. A session pool
allows only one active scan, and exceptional joblib completion waits for
sibling history workers to leave their scopes before their sessions are closed.

Ranking failures abort before any database mutation. Per-vault history failures
are recorded independently and remain retryable without erasing other vaults.
A stabilised empty ranking is also rejected when the database already contains
vaults, preventing one anomalous response from marking the full universe
missing.

## Quick start

Run an initial all-vault backfill:

```shell
poetry run python scripts/apex/vault-metrics.py
```

Run selected vaults:

```shell
VAULT_IDS=2044287989957394432,1914612863780126720 \
  poetry run python scripts/apex/vault-metrics.py
```

Run continuously at a different cadence:

```shell
LOOP=1 SCAN_INTERVAL=30m HISTORY_REFRESH_INTERVAL=12h \
  poetry run python scripts/apex/vault-metrics.py
```

Force an append-and-correct history refresh:

```shell
HISTORY_MODE=refresh poetry run python scripts/apex/vault-metrics.py
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `info` | Console log level |
| `DB_PATH` | `~/.tradingstrategy/vaults/apex-vaults.duckdb` | DuckDB path |
| `VAULT_IDS` | all vaults | Optional comma-separated target IDs |
| `MAX_WORKERS` | `8` | History reader threads |
| `REQUESTS_PER_SECOND` | `5` | Shared request rate |
| `CONNECT_TIMEOUT` | `10` | Connection timeout in seconds |
| `READ_TIMEOUT` | `30` | Socket inactivity timeout in seconds |
| `REQUEST_DEADLINE` | `60` | One request-attempt deadline |
| `RANKING_DEADLINE` | `300` | Deadline shared by both ranking passes |
| `HISTORY_DEADLINE` | `120` | Per-vault history operation deadline |
| `MAX_RETRY_DELAY` | `10` | Maximum retry delay |
| `MAX_RESPONSE_BYTES` | `16777216` | Largest accepted JSON response |
| `HISTORY_MODE` | `incremental` | `incremental`, `refresh` or `none` |
| `HISTORY_REFRESH_INTERVAL` | `24h` | Independent history cadence |
| `LOOP` | false | Repeat scans sequentially |
| `SCAN_INTERVAL` | `4h` | Ranking cadence in loop mode |

## Key modules

| Module | Role |
|--------|------|
| `eth_defi.apex.constants` | Synthetic chain, API and operational defaults |
| `eth_defi.apex.config` | Strict environment and duration parsing |
| `eth_defi.apex.session` | Worker-local sessions and bounded HTTP policy |
| `eth_defi.apex.vault` | Typed public endpoint parsing and pagination |
| `eth_defi.apex.metrics` | DuckDB lifecycle and scan orchestration |
| `scripts/apex/vault-metrics.py` | Standalone command |

## Running tests

Run the fixture-based suite without contacting ApeX:

```shell
source .local-test.env && poetry run pytest tests/apex
```
