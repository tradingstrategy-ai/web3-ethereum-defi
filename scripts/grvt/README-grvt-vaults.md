# GRVT native vault metrics pipeline

## Overview

Scans GRVT (Gravity Markets) native vaults, fetches share price history
from the public market data API, and merges the data into the existing
ERC-4626 vault metrics pipeline so that `vault-analysis-json.py` produces
a single unified JSON with EVM, Hyperliquid, and GRVT vaults.

GRVT data goes through the same `process_raw_vault_scan_data()` cleaning
pipeline as EVM vaults.

**No authentication is required** — all data comes from public endpoints.

## Architecture

```
GRVT public endpoints                ERC-4626 pipeline
======================                =================
strategies page (Next.js SSR)         vault-metadata-db.pickle
  __NEXT_DATA__ JSON                  vault-prices-1h.parquet (uncleaned)
      |                                      |
      v                                      |
fetch_vault_listing()                        |
  vault discovery (~14 vaults)               |
      |                                      |
      v                                      |
market-data.grvt.io                          |
  /full/v1/vault_detail (TVL)                |
  /full/v1/vault_summary_history             |
    share price time series                  |
      |                                      |
      v                                      |
grvt-vaults.duckdb  ------merge----->  vault-metadata-db.pickle
  vault_metadata table                 vault-prices-1h.parquet (uncleaned)
  vault_daily_prices table                   |
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
                                        EVM + Hyperliquid + GRVT vaults
```

### Chain ID

GRVT vaults use chain ID `325` (constant `GRVT_CHAIN_ID`).

### Denomination token

All GRVT vaults are denominated in USDT.

### Vault discovery

Vaults are discovered dynamically by scraping the GRVT strategies page
(`https://grvt.io/exchange/strategies`), which embeds all vault metadata
via Next.js server-side rendering in a `__NEXT_DATA__` JSON block.

Each vault has two IDs:

- **String ID** (e.g. `VLT:34dTZyg6LhkGM49Je5AABi9tEbW`) — from the strategies page
- **Numeric chain vault ID** (e.g. `1463215095`) — used by the market data API

### Public API endpoints

All endpoints are on `https://market-data.grvt.io` and require no authentication.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/full/v1/vault_detail` | POST | TVL (`total_equity`), share price, valuation cap |
| `/full/v1/vault_performance` | POST | APR, 30d/90d/YTD returns, cumulative PnL |
| `/full/v1/vault_risk_metric` | POST | Sharpe ratio, Sortino ratio, max drawdown |
| `/full/v1/vault_summary_history` | POST | Share price time series (~8-hourly intervals) |

Batch endpoints accept `{"vault_i_ds": ["chainVaultID1", ...]}`.
History endpoint accepts `{"vault_id": "chainVaultID"}`.

### DuckDB schema

```
vault_metadata                     vault_daily_prices
==============                     ==================
vault_id       VARCHAR PK          vault_id       VARCHAR  \
chain_vault_id INTEGER             date           DATE      > composite PK
name           VARCHAR             share_price    DOUBLE
description    VARCHAR             tvl            DOUBLE
vault_type     VARCHAR             daily_return   DOUBLE
manager_name   VARCHAR
tvl            DOUBLE
share_price    DOUBLE
investor_count INTEGER
last_updated   TIMESTAMP
```

### Fees

GRVT vault fees vary per vault (unlike Hyperliquid's fixed 10%):

- **Management fee**: 0-4% annually, charged daily based on AUM
- **Performance fee**: 0-40%, charged on realised profits at redemption

The fee mode is `internalised_skimming` — fees are embedded in the LP token
price, so gross and net returns are identical from the pipeline's perspective.
Fee percentages are not returned by the public API, so they are set to zero
in the pipeline.

Source: [GRVT strategies core concepts](https://help.grvt.io/en/articles/11424466-grvt-strategies-core-concepts)

### Share price computation

Share prices come directly from the `vault_summary_history` market data
endpoint, which provides share prices at ~8-hour intervals. We resample
to daily frequency (taking the last price of each day).

### Vault flags

All GRVT vaults get the `perp_dex_trading_vault` flag.

## Quick start

```shell
# Scan all discoverable vaults (snapshot)
LOG_LEVEL=info poetry run python scripts/grvt/scan-vaults.py

# Daily metrics pipeline (scan + merge + clean)
LOG_LEVEL=info poetry run python scripts/grvt/daily-vault-metrics.py

# Scan specific vaults by string ID
VAULT_IDS=VLT:34dTZyg6LhkGM49Je5AABi9tEbW \
  poetry run python scripts/grvt/daily-vault-metrics.py
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `warning` | Logging level |
| `DB_PATH` | `~/.tradingstrategy/vaults/grvt-vaults.duckdb` | DuckDB database path |
| `VAULT_IDS` | *(all discoverable)* | Comma-separated vault string IDs to scan |
| `VAULT_DB_PATH` | `~/.tradingstrategy/vaults/vault-metadata-db.pickle` | VaultDatabase pickle path |
| `PARQUET_PATH` | `~/.tradingstrategy/vaults/vault-prices-1h.parquet` | Uncleaned Parquet path |

## Key modules

| Module | Role |
|--------|------|
| `eth_defi/grvt/vault.py` | Public API client (listing, details, performance, history) |
| `eth_defi/grvt/daily_metrics.py` | DuckDB storage, daily price pipeline |
| `eth_defi/grvt/vault_data_export.py` | Bridge to ERC-4626 pipeline (VaultRow builder, Parquet/pickle merge) |
| `eth_defi/grvt/vault_scanner.py` | Point-in-time vault snapshots in DuckDB |
| `eth_defi/grvt/constants.py` | Chain ID, API URLs, fee constants |

## Running tests

```shell
poetry run pytest tests/grvt/ -x --timeout=300
```
