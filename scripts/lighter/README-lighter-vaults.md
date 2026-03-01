# Lighter native pool metrics pipeline

## Overview

Scans Lighter DEX native pools (vaults), fetches share price history
from the public REST API, and merges the data into the existing
ERC-4626 vault metrics pipeline so that `vault-analysis-json.py` produces
a single unified JSON with EVM, Hyperliquid, GRVT, and Lighter pools.

Lighter data goes through the same `process_raw_vault_scan_data()` cleaning
pipeline as EVM vaults.

**No authentication is required** — all data comes from public endpoints.

## Architecture

```
Lighter public endpoints               ERC-4626 pipeline
========================               =================
/api/v1/systemConfig                   vault-metadata-db.pickle
  liquidity_pool_index (LLP ID)        vault-prices-1h.parquet (uncleaned)
      |                                        |
      v                                        |
/api/v1/publicPoolsMetadata                    |
  bulk pool listing (~300 pools)               |
  name, APY, sharpe_ratio, TVL                 |
      |                                        |
      v                                        |
/api/v1/account?by=index&value={idx}           |
  per-pool share price history                 |
  pool_info.share_prices (~379 entries)        |
  pool_info.daily_returns                      |
      |                                        |
      v                                        |
lighter-pools.duckdb  ------merge----->  vault-metadata-db.pickle
  pool_metadata table                    vault-prices-1h.parquet (uncleaned)
  pool_daily_prices table                      |
                                               v
                                        process_raw_vault_scan_data()
                                          fix_outlier_share_prices()
                                          calculate_vault_returns()
                                               |
                                               v
                                        cleaned-vault-prices-1h.parquet
                                               |
                                               v
                                        vault-analysis-json.py
                                               |
                                               v
                                        Combined JSON output
                                          EVM + Hyperliquid + GRVT + Lighter
```

### Chain ID

Lighter pools use synthetic chain ID `9998` (constant `LIGHTER_CHAIN_ID`).

### Denomination token

All Lighter pools are denominated in USDC.

### Pool discovery

Pools are discovered via `/api/v1/publicPoolsMetadata`. The LLP
(Lighter Liquidity Pool) is a special system pool **not** listed in
this endpoint — it is fetched separately from `/api/v1/account` using
the `liquidity_pool_index` from `/api/v1/systemConfig`.

Each pool has a single integer identifier:

- **Account index** (e.g. `281474976710654` for LLP) — primary key across all endpoints

Pool addresses in the pipeline are synthetic strings: `lighter-pool-{account_index}`.

### Public API endpoints

Base URL: `https://mainnet.zklighter.elliot.ai`

All endpoints are GET requests. No authentication required.

#### System config (`/api/v1/systemConfig`)

Returns system configuration including the LLP account index.

| Field | Type | Description |
|-------|------|-------------|
| `liquidity_pool_index` | int | Account index of the LLP protocol pool |
| `liquidity_pool_cooldown_period` | int | Withdrawal cooldown in milliseconds (300000 = 5 min) |

#### Pool listing (`/api/v1/publicPoolsMetadata`)

Bulk listing of public user-created pools. Paginated by `index` + `limit`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `filter` | string | `"all"` for all pools |
| `index` | int | Starting account index (count down) |
| `limit` | int | Page size (max 100) |

Response fields per pool in `public_pools[]`:

| Field | Type | Description |
|-------|------|-------------|
| `account_index` | int | Primary identifier |
| `name` | string | Pool display name (e.g. "ETH 3x long") |
| `l1_address` | string | L1 Ethereum address of the pool operator |
| `annual_percentage_yield` | float | Current APY |
| `sharpe_ratio` | string | Risk-adjusted return metric |
| `operator_fee` | string | Fee percentage (e.g. "10" = 10%) |
| `total_asset_value` | string | TVL in USDC |
| `total_shares` | int | Outstanding shares |
| `status` | int | 0 = active |
| `account_type` | int | 2 = pool |
| `master_account_index` | int | Operator's main account |
| `created_at` | int | Unix timestamp |

#### Pool detail (`/api/v1/account`)

Per-pool detailed data including share price history.

| Parameter | Type | Description |
|-----------|------|-------------|
| `by` | string | `"index"` |
| `value` | string | Account index |

Response fields (inside `accounts[0]`):

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Pool name |
| `description` | string | Pool strategy description |
| `total_asset_value` | string | TVL in USDC |
| `pool_info.share_prices` | array | `[{"timestamp": int, "share_price": float}, ...]` |
| `pool_info.daily_returns` | array | `[{"timestamp": int, "daily_return": float}, ...]` |
| `pool_info.operator_fee` | string | Operator fee percentage |
| `pool_info.annual_percentage_yield` | float | Current APY |
| `pool_info.sharpe_ratio` | string | Risk-adjusted return metric |
| `pool_info.total_shares` | int | Outstanding shares |
| `pool_info.operator_shares` | int | Operator's shares |

Share price arrays have different retention depending on pool type
(see [Share price history limitations](#share-price-history-limitations) below).

#### PnL history (`/api/v1/pnl`)

Per-account PnL and balance history with configurable resolution and
time range. This is the **only endpoint with full history** back to
pool inception for all pools.

| Parameter | Type | Description |
|-----------|------|-------------|
| `by` | string | `"index"` |
| `value` | string | Account index |
| `resolution` | string | `"1m"`, `"5m"`, `"15m"`, `"1h"`, `"4h"`, `"1d"` |
| `start_timestamp` | int | Unix timestamp for range start |
| `end_timestamp` | int | Unix timestamp for range end |
| `count_back` | int | `0` |
| `ignore_transfers` | bool | `true` for balance chart, `false` for PnL chart |

Response fields (inside `pnl[]`):

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | int | Unix timestamp |
| `trade_pnl` | float | Cumulative trading PnL (USDC) |
| `trade_spot_pnl` | float | Cumulative spot PnL |
| `pool_pnl` | float | Pool-level PnL |
| `pool_inflow` | float | Cumulative deposit inflow (USDC) |
| `pool_outflow` | float | Cumulative withdrawal outflow (USDC) |
| `pool_total_shares` | int | Total outstanding shares at this point |
| `staking_pnl` | float | LIT staking PnL |
| `staking_inflow` | float | Staking inflow |
| `staking_outflow` | float | Staking outflow |

**Note:** This endpoint does **not** return `share_price`. The
`pool_total_shares` field cannot be trivially combined with PnL fields
to reconstruct share price — the data model is more complex than
`(inflow - outflow + trade_pnl) / shares`. The Lighter website uses
this endpoint for its TVL/balance chart, but uses the separate
`share_prices` array from `/api/v1/account` for the NAV chart.

### Share price history limitations

The `/api/v1/account` endpoint's `share_prices` array has **different
retention depending on pool type**:

| Pool type | History | Entries (as of Mar 2026) |
|-----------|---------|------------------------|
| **LLP (protocol pool)** | Full history from inception (Jan 2025) | ~409 |
| **User-created pools** | Rolling window of ~208 days | ~208 max |

User pools created before the rolling window cutoff (around Aug 2025)
have their earliest share price entries truncated. Pools created within
the window have full history from their creation date.

**Implication for the pipeline:** The pipeline should run at least daily
to capture share prices before they fall off the rolling window for user
pools. The DuckDB database preserves all previously fetched data, so
historical entries are not lost once stored.

The `/api/v1/pnl` endpoint **does** return full history (409+ entries)
for all pools, but only provides `pool_total_shares` and cumulative PnL
fields — not share prices. The Lighter website's "All-time" TVL chart
uses this PnL endpoint (which is why TVL goes back to Jan 2025 for all
pools), while the NAV/share-price chart is limited by the `share_prices`
retention window.

### DuckDB schema

```
pool_metadata                         pool_daily_prices
=============                         =================
account_index    BIGINT PK            account_index  BIGINT  \
name             VARCHAR               date           DATE    > composite PK
description      VARCHAR               share_price    DOUBLE
l1_address       VARCHAR               tvl            DOUBLE
is_llp           BOOLEAN               daily_return   DOUBLE
operator_fee     DOUBLE                annual_percentage_yield DOUBLE
total_asset_value DOUBLE
annual_percentage_yield DOUBLE
sharpe_ratio     DOUBLE
created_at       TIMESTAMP
last_updated     TIMESTAMP
```

### Fees

Lighter pool fees are per-pool `operator_fee` values set by pool operators:

- **Operator fee**: 0–100%, a performance fee deducted from PnL
- **LLP**: 0% operator fee
- **User pools**: Variable (commonly 10–20%)

The fee mode is `internalised_skimming` — the operator fee is already
reflected in the share prices returned by the API. The pipeline treats
share prices as net of fees.

### Share price computation

Share prices come directly from the `/api/v1/account` endpoint's
`pool_info.share_prices` array. These are daily entries with unix
timestamps. We group by date (taking the last price per day) and
compute `daily_return` via `pct_change()`.

### Pool flags

All Lighter pools get the `perp_dex_trading_vault` flag.

### LLP (Lighter Liquidity Pool)

The LLP is the main protocol liquidity pool (~$227M TVL). It is special
because:

- It is **not** listed in `publicPoolsMetadata`
- Its account index comes from `systemConfig.liquidity_pool_index`
- It has 0% operator fee
- It is fetched separately via the `/api/v1/account` endpoint

## Quick start

```shell
# Basic usage with defaults
LOG_LEVEL=info poetry run python scripts/lighter/daily-pool-metrics.py

# Scan specific pools by account index
POOL_INDICES=281474976710654,281474976710653 \
  poetry run python scripts/lighter/daily-pool-metrics.py

# Limit to top pools by TVL
MIN_TVL=10000 MAX_POOLS=20 \
  poetry run python scripts/lighter/daily-pool-metrics.py
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `warning` | Logging level |
| `DB_PATH` | `~/.tradingstrategy/vaults/lighter-pools.duckdb` | DuckDB database path |
| `POOL_INDICES` | *(all pools)* | Comma-separated pool account indices to scan |
| `MIN_TVL` | `1000` | Minimum TVL in USDC to include a pool |
| `MAX_POOLS` | `200` | Maximum number of pools to scan |
| `MAX_WORKERS` | `16` | Number of parallel workers |
| `VAULT_DB_PATH` | `~/.tradingstrategy/vaults/vault-metadata-db.pickle` | VaultDatabase pickle path |
| `PARQUET_PATH` | `~/.tradingstrategy/vaults/vault-prices-1h.parquet` | Uncleaned Parquet path |

## Key modules

| Module | Role |
|--------|------|
| `eth_defi/lighter/vault.py` | Public API client (pool listing, pool detail, share price history) |
| `eth_defi/lighter/daily_metrics.py` | DuckDB storage, daily price pipeline, parallel scanning |
| `eth_defi/lighter/vault_data_export.py` | Bridge to ERC-4626 pipeline (VaultRow builder, Parquet/pickle merge) |
| `eth_defi/lighter/session.py` | Rate-limited HTTP session with retry logic |
| `eth_defi/lighter/constants.py` | Chain ID, API URL, fee constants, lockup period |

## Running tests

```shell
poetry run pytest tests/lighter/ -x --timeout=300
```
