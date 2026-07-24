# Lighter native pool metrics pipeline

## Overview

Scans the Ethereum and Robinhood Chain Lighter DEX deployments, fetches
native pool (vault) share price history from their public REST APIs, and merges the data into the existing
ERC-4626 vault metrics pipeline so that `vault-analysis-json.py` produces
a single unified JSON with EVM, Hyperliquid, GRVT, and Lighter pools.

Lighter data goes through the same `process_raw_vault_scan_data()` cleaning
pipeline as EVM vaults.

**No authentication is required** — all data comes from public endpoints.

### Perp account metric alignment

The scanner stores each public pool's current non-zero positions through the
shared perp DEX observation tables. Lighter price history uses UTC-midnight
daily timestamps, while positions are measured later during the scan. After
the ordinary backward as-of join, the shared bounded alignment helper overlays
only the newest eligible observation on that pool's latest price row. Older
price rows never receive the newer observation.

`perp_metrics_observed_at` retains the actual collection time at one-second
resolution in raw Parquet, cleaned Parquet and `other_data.perp_dex`. Stale
values are retained with this timestamp so consumers can calculate their age.
No Lighter-specific Parquet or JSON transformation is used.

## Lighter canonical documentation

- [Lighter homepage](https://lighter.xyz)
- [Public pools app](https://app.lighter.xyz/public-pools)
- [Robinhood Lighter app](https://robinhoodchain.lighter.xyz/public-pools)
- [Public pools documentation](https://docs.lighter.xyz/trading/public-pools)
- [Fees documentation](https://docs.lighter.xyz/trading/trading-fees)
- [API reference (Swagger)](https://apidocs.lighter.xyz/)
- [DeFi Llama](https://defillama.com/protocol/lighter-perps)

## Architecture

```
Lighter public endpoints               ERC-4626 pipeline
========================               =================
/api/v1/systemConfig                   vault-metadata-db.pickle
  reported liquidity_pool_index        vault-prices-1h.parquet (uncleaned)
      |                                        |
      v                                        |
/api/v1/publicPoolsMetadata                    |
  bulk pool listing                            |
  name, APY, sharpe_ratio, TVL                 |
      |                                        |
      v                                        |
/api/v1/account?by=index&value={idx}           |
  per-pool share price history                 |
  pool_info.share_prices                       |
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

### Deployments and chain IDs

Lighter pools use a shared synthetic chain ID because they are native pools
rather than EVM ERC-4626 contracts. A second chain ID records the EVM chain
associated with each deployment:

| Deployment | API | Synthetic chain ID | Associated EVM chain ID | Denomination | Address format |
|------------|-----|--------------------|-------------------------|--------------|----------------|
| Ethereum | `https://mainnet.zklighter.elliot.ai` | `9998` | `1` | USDC | `lighter-pool-{account_index}` |
| Robinhood Chain | `https://api.rh.lighter.xyz` | `9998` | `4663` | USDG | `lighter-pool-robinhood-{account_index}` |

Both deployments intentionally share synthetic chain `9998` because it is the
native Lighter dataset namespace. Their deployment-specific synthetic address
prefixes provide VaultSpec and price-series uniqueness even when Lighter reuses
the same account index on both deployments.

These settings cover native pool discovery, metrics storage and vault-dataset
export. The Lagoon custody and Guard helpers remain Ethereum-only because they
target the verified Ethereum `ZkLighter` proxy and its USDC asset index.
Robinhood product documentation describes deposits to a Lighter Relayer smart
contract, but this repository does not yet configure or verify that contract's
address and ABI for custody operations.

### Lifetime-metrics chain identity

Lighter needs two chain identities in `calculate_lifetime_metrics()` output:

- `chain_id` is the shared synthetic native-pool dataset identity (`9998`).
  It keys price partitions and `VaultSpec` records and must not be replaced by
  an EVM chain ID.
- `deployment_chain_id` is the associated EVM deployment chain (`1` for
  Ethereum or `4663` for Robinhood Chain).
- `deployment` is the stable deployment slug (`ethereum` or `robinhood`).

For now the two additive deployment fields exist specifically to support
Lighter on Robinhood in lifetime-metrics and JSON consumers. They are carried
through Lighter `VaultRow` metadata; existing non-Lighter vault rows export
`None`. These export fields do not add columns to the price Parquet files.

The underlying Lighter storage migration is automatic. Opening an older
`lighter-pools.duckdb` transactionally adds the `deployment` column and changes
the primary keys to `(deployment, account_index)` and
`(deployment, account_index, date)`, labelling existing rows as `ethereum`.
This assumes the database predates Robinhood support, as production databases
do. If a development database was created by an intermediate, unmerged version
of the Robinhood work, delete that development database and rescan it.
The metadata merge refreshes existing pickle rows with deployment identity.
The price merge removes the short-lived Robinhood synthetic-chain `9996`
partition only after fresh Robinhood data is available to replace it.

### Denomination token

Ethereum Lighter pools are denominated in USDC. Robinhood Lighter pools are
denominated in USDG.

### Pool discovery

Pools are discovered via `/api/v1/publicPoolsMetadata`. The canonical LLP
(Lighter Liquidity Pool) is identified by an exact deployment-local account
index. Ethereum uses the `liquidity_pool_index` reported by
`/api/v1/systemConfig`; if that pool is absent from the listing, it is fetched
separately from `/api/v1/account`.

For now Robinhood uses the configured account-index override
`281474976710654`, because its live `systemConfig` response reports the
uninitialised account `281474976710655`. The scanner does not identify LLP from
`account_type == 3`: Ethereum also exposes XLP with type `3`, so that fallback
would misclassify a second protocol pool as LLP.

Each pool has a deployment-local integer identifier:

- **Account index** (e.g. `281474976710654` for LLP) — unique only within one deployment

Account indexes overlap between deployments: `281474976710654` is used by
both Ethereum and Robinhood Lighter. DuckDB therefore keys rows by
`(deployment, account_index)` and price rows by
`(deployment, account_index, date)`.

### Robinhood pool availability

As checked against the live API on 2026-07-22, Robinhood Lighter exposes one
active pool through `publicPoolsMetadata`:

- Account index `281474976710654`
- Account type `3`, the protocol-operated LLP/insurance pool
- USDG denomination and 0% operator fee
- Daily share-price and total-share history available from the standard
  `account` and `pnl` endpoints

The Robinhood API currently leaves the pool name and description empty, so the
scanner supplies the `Lighter Liquidity Provider (LLP)` label. Its
`systemConfig.liquidity_pool_index` points to `281474976710655`, which was not
an initialised account when checked. The deployment configuration therefore
identifies the live LLP by its exact account index, not by account type. TVL
and APY are deliberately read live rather than documented as fixed values.

Robinhood's product documentation confirms that the deployment uses USDG on
Robinhood Chain and that liquidation fees and bankrupt positions flow to the
LLP insurance fund: <https://robinhood.com/us/en/support/articles/robinhood-wallet-perpetual-futures/>.

### Public API endpoints

Base URLs are `https://mainnet.zklighter.elliot.ai` for Ethereum and
`https://api.rh.lighter.xyz` for Robinhood.

All endpoints are GET requests. No authentication required.

#### System config (`/api/v1/systemConfig`)

Returns system configuration including the LLP account index.

| Field | Type | Description |
|-------|------|-------------|
| `liquidity_pool_index` | int | Reported LLP account index. The Robinhood value was stale when checked on 2026-07-22, so its deployment config supplies an explicit override. |
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
| `l1_address` | string | Operator address reported by the API; the legacy field name is retained across deployments |
| `annual_percentage_yield` | float | Current APY |
| `sharpe_ratio` | string | Risk-adjusted return metric |
| `operator_fee` | string | Fee percentage (e.g. "10" = 10%) |
| `total_asset_value` | string | TVL in the deployment's collateral currency |
| `total_shares` | int | Outstanding shares |
| `status` | int | 0 = active |
| `account_type` | int | 2 = public pool; 3 = protocol liquidity/insurance pool |
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
| `total_asset_value` | string | TVL in the deployment's collateral currency |
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
| `trade_pnl` | float | Cumulative trading PnL in the deployment's collateral currency |
| `trade_spot_pnl` | float | Cumulative spot PnL |
| `pool_pnl` | float | Pool-level PnL |
| `pool_inflow` | float | Cumulative deposit inflow in the deployment's collateral currency |
| `pool_outflow` | float | Cumulative withdrawal outflow in the deployment's collateral currency |
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

The `/api/v1/account` endpoint's `share_prices` array has shown different
retention depending on pool and deployment. Protocol pools may expose longer
histories, while older user pools can return a rolling window. The API does not
promise a fixed entry count, so consumers must not rely on the historical
counts observed during development.

**Implication for the pipeline:** The pipeline should run at least daily
to capture share prices before they fall off the rolling window for user
pools. The DuckDB database preserves all previously fetched data, so
historical entries are not lost once stored.

The `/api/v1/pnl` endpoint can expose a longer history, but it only provides
`pool_total_shares` and cumulative PnL fields — not share prices. It therefore
cannot fill missing NAV/share-price observations without a separate, verified
reconstruction model.

### DuckDB schema

```
pool_metadata                         pool_daily_prices
=============                         =================
deployment       VARCHAR \            deployment     VARCHAR \
account_index    BIGINT   > PK         account_index  BIGINT  > composite PK
name             VARCHAR               date           DATE   /
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

Opening a legacy database performs a transactional migration. All existing
rows are retained and labelled `ethereum`; no rescan is needed.

### Fees

Lighter pool fees are per-pool `operator_fee` values set by pool operators:

- **Operator fee**: 0–100%, a performance fee deducted from PnL
- **LLP**: 0% operator fee
- **User pools**: Variable, as reported by each pool

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

The LLP is the protocol-operated liquidity and insurance pool. Its TVL is read
live. Discovery differs by deployment:

- Ethereum obtains the canonical account index from
  `systemConfig.liquidity_pool_index` and fetches the account separately when
  it is absent from `publicPoolsMetadata`.
- For now Robinhood uses the explicit live account index
  `281474976710654`, because its reported system-config index is uninitialised.
  The pool is present in `publicPoolsMetadata`.
- LLP identity is based on the exact deployment-local account index, not the
  non-unique account type.

## Quick start

```shell
# Legacy standalone Ethereum scan with defaults
LOG_LEVEL=info poetry run python scripts/lighter/daily-pool-metrics.py

# Scan specific pools by account index
POOL_INDICES=281474976710654,281474976710653 \
  poetry run python scripts/lighter/daily-pool-metrics.py

# Limit to top pools by TVL
MIN_TVL=10000 MAX_POOLS=20 \
  poetry run python scripts/lighter/daily-pool-metrics.py
```

## Environment variables

These variables configure the standalone Ethereum scanner. Use
`scan-vaults-all-chains.py` with `SCAN_LIGHTER=true` for the supported
two-deployment scan.

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `warning` | Logging level |
| `DB_PATH` | `~/.tradingstrategy/vaults/lighter-pools.duckdb` | DuckDB database path |
| `POOL_INDICES` | *(all pools)* | Comma-separated pool account indices to scan |
| `MIN_TVL` | `1000` | Minimum TVL in the deployment's collateral currency |
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

## Running the full pipeline (Lighter only)

To run the full pipeline scanning both Lighter deployments (no EVM chains),
disable all other chains and set `SCAN_LIGHTER=true`:

```shell
source .local-test.env && \
SCAN_LIGHTER=true \
DISABLE_CHAINS=Sonic,Monad,Hyperliquid,Base,Arbitrum,Ethereum,Linea,Gnosis,Zora,Polygon,Avalanche,Berachain,Unichain,Hemi,Plasma,Binance,Mantle,Katana,Ink,Blast,Soneium,Optimism \
MAX_WORKERS=20 \
LOG_LEVEL=info \
poetry run python scripts/erc-4626/scan-vaults-all-chains.py
```

This runs through the following stages:

1. **Lighter scans** — independently discovers Ethereum and Robinhood pools from their public APIs, fetches share price
   history and total shares, stores in `lighter-pools.duckdb`
2. **Merge vault metadata** — upserts Lighter VaultRows into `vault-metadata-db.pickle`
3. **Merge prices** — appends Lighter daily prices into `vault-prices-1h.parquet`
   (uncleaned), replacing only the deployment address namespaces present in
   the fresh export. A failed or omitted deployment retains its prior rows.
4. **Clean prices** — runs `process_raw_vault_scan_data()` (outlier detection,
   return calculation) producing `cleaned-vault-prices-1h.parquet`
5. **Export** — generates sparklines, protocol metadata, and uploads to R2

The standalone command below is retained for backwards-compatible Ethereum
operations. It scans Ethereum, merges metadata and prices, and runs cleaning,
but does not scan Robinhood or upload exports:

```shell
LOG_LEVEL=info poetry run python scripts/lighter/daily-pool-metrics.py
```

## Purging and rescanning

If Lighter price data is stale or incorrect (e.g. after a bug fix
that changes how TVL or share prices are computed), purge the old data
and rescan from scratch.

### Step 1: Delete the Lighter DuckDB

```shell
rm ~/.tradingstrategy/vaults/lighter-pools.duckdb
```

This removes the intermediate DuckDB that holds pool metadata and
daily prices before they are merged into the unified pipeline.

### Step 2: Purge Lighter rows from the uncleaned Parquet

Use `purge-price-data.py` with the shared Lighter synthetic chain ID to strip
Lighter rows from the shared Parquet file:

```shell
CHAIN_ID=9998 poetry run python scripts/erc-4626/purge-price-data.py
```

The pipeline automatically removes any rows written under the legacy
Robinhood-only synthetic chain `9996` during the next successful Lighter price
merge; operators do not need to purge that temporary partition manually.

### Step 3: Rescan

Run the Lighter-only full pipeline to rebuild from scratch:

```shell
SCAN_LIGHTER=true \
DISABLE_CHAINS=Sonic,Monad,Hyperliquid,Base,Arbitrum,Ethereum,Linea,Gnosis,Zora,Polygon,Avalanche,Berachain,Unichain,Hemi,Plasma,Binance,Mantle,Katana,Ink,Blast,Soneium,Optimism \
MAX_WORKERS=20 \
LOG_LEVEL=info \
poetry run python scripts/erc-4626/scan-vaults-all-chains.py
```

## Running Lighter-specific tests

```shell
source .local-test.env && poetry run pytest tests/lighter/ -x --timeout=300
```
