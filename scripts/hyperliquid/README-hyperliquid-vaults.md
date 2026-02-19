# Hyperliquid native vault metrics pipeline

## Overview

Scans Hyperliquid native (non-EVM) vaults, computes time-weighted share prices,
and merges the data into the existing ERC-4626 vault metrics pipeline so that
`vault-analysis-json.py` produces a single unified JSON with both EVM and
Hyperliquid vaults.

## Architecture

```
Hyperliquid API                       ERC-4626 pipeline
==============                        =================

stats-data (bulk GET)                 scan-vaults.py (multi-chain)
  ~9000 vaults                          EVM vaults via HyperSync
      |                                      |
      v                                      v
Filter by TVL & open status           vault-metadata-db.pickle
      |                               cleaned-vault-prices-1h.parquet
      v                                      |
vaultDetails (per-vault POST)                |
  portfolio.allTime history                  |
      |                                      |
      v                                      |
_calculate_share_price()                     |
  from combined_analysis.py                  |
      |                                      |
      v                                      |
daily-metrics.duckdb  ----merge----->  vault-metadata-db.pickle
  vault_metadata table                 cleaned-vault-prices-1h.parquet
  vault_daily_prices table                   |
                                             v
                                      vault-analysis-json.py
                                        calculate_lifetime_metrics()
                                        export_lifetime_row()
                                             |
                                             v
                                      Combined JSON output
                                        EVM + Hyperliquid vaults
```

### Chain ID

Hyperliquid native vaults use a synthetic chain ID of `-999` (constant
`HYPERCORE_CHAIN_ID`). This avoids collision with any real EVM chain ID.
Vault IDs in the pipeline look like `-999-0xabc...`.

### DuckDB schema

Stored at `~/.tradingstrategy/hyperliquid/daily-metrics.duckdb` by default.

```
vault_metadata                     vault_daily_prices
==============                     ==================
vault_address  VARCHAR PK          vault_address  VARCHAR  \
name           VARCHAR             date           DATE      > composite PK
leader         VARCHAR             share_price    DOUBLE
description    VARCHAR             tvl            DOUBLE
is_closed      BOOLEAN             cumulative_pnl DOUBLE
commission_rate DOUBLE             daily_pnl      DOUBLE
follower_count INTEGER             daily_return   DOUBLE
tvl            DOUBLE              follower_count INTEGER
apr            DOUBLE              apr            DOUBLE
create_time    TIMESTAMP
last_updated   TIMESTAMP
```

### Share price computation

Portfolio history from `vaultDetails` gives `account_value_history` and
`pnl_history` as daily `(datetime, Decimal)` tuples. We derive:

```
pnl_update[i]     = pnl_history[i] - pnl_history[i-1]
netflow_update[i] = (account_value[i] - account_value[i-1]) - pnl_update[i]
```

These feed into `_calculate_share_price()` from `combined_analysis.py`,
which uses the proven mint/burn share price logic without needing the
slow per-fill/per-deposit API calls.

### Hypercore-specific columns in price data

The cleaned Parquet gains these extra columns. For EVM vaults they are `NA`:

- `follower_count` -- number of vault depositors
- `apr` -- Hyperliquid's pre-computed annual percentage rate
- `cumulative_pnl` -- cumulative total PnL in USD
- `daily_pnl` -- daily PnL in USD

## Quick start example

Scan HLP and Growi HF vaults, compute metrics, and display the JSON output:

```shell
poetry run python scripts/hyperliquid/example-vault-metrics.py
```

Pick your own vaults by address:

```shell
VAULT_ADDRESSES=0xdfc24b077bc1425ad1dea75bcb6f8158e10df303,0x1e37a337ed460039d1b15bd3bc489de789768d5e \
  poetry run python scripts/hyperliquid/example-vault-metrics.py
```

## Running as part of the all-chains scanner

The multi-chain `scan-vaults-all-chains.py` script can include Hyperliquid vaults
when the `SCAN_HYPERCORE` environment variable is set.

### Scan only Hypercore vaults (skip all EVM chains)

Use `DISABLE_CHAINS` to skip every EVM chain, leaving only Hypercore:

```shell
SCAN_HYPERCORE=true \
  DISABLE_CHAINS=Ethereum,Arbitrum,Base,Polygon,Avalanche,Optimism,Binance,Sonic,Berachain,Unichain,Mantle,Mode,Abstract,Celo,Soneium,zkSync,Gnosis,Blast,Zora,Ink,Hemi,Linea,TAC,Plasma,Katana,Monad,HyperEVM \
  LOG_LEVEL=info \
  poetry run python scripts/erc-4626/scan-vaults-all-chains.py
```

### Scan Hypercore alongside all EVM chains

```shell
source ~/vault-scanner/vault-rpc.env
SCAN_HYPERCORE=true LOG_LEVEL=info \
  poetry run python scripts/erc-4626/scan-vaults-all-chains.py
```

### Docker: scan only Hypercore

```shell
SCAN_HYPERCORE=true \
  DISABLE_CHAINS=Ethereum,Arbitrum,Base,Polygon,Avalanche,Optimism,Binance,Sonic,Berachain,Unichain,Mantle,Mode,Abstract,Celo,Soneium,zkSync,Gnosis,Blast,Zora,Ink,Hemi,Linea,TAC,Plasma,Katana,Monad,HyperEVM \
  LOG_LEVEL=info \
  docker compose run vault-scanner
```

## Manual testing commands

### Quick smoke test (single vault)

```shell
LOG_LEVEL=info MAX_VAULTS=1 MIN_TVL=1000000 \
  poetry run python scripts/hyperliquid/daily-vault-metrics.py
```

### Scan specific vaults by address

```shell
LOG_LEVEL=info \
  VAULT_ADDRESSES=0xdfc24b077bc1425ad1dea75bcb6f8158e10df303,0x1e37a337ed460039d1b15bd3bc489de789768d5e \
  poetry run python scripts/hyperliquid/daily-vault-metrics.py
```

### Full scan (default settings, ~500 vaults, ~3 min)

```shell
LOG_LEVEL=info \
  poetry run python scripts/hyperliquid/daily-vault-metrics.py
```

### Custom DuckDB and output paths (for testing in isolation)

```shell
LOG_LEVEL=info MAX_VAULTS=5 \
  DB_PATH=/tmp/hl-test.duckdb \
  VAULT_DB_PATH=/tmp/vault-metadata-db.pickle \
  PARQUET_PATH=/tmp/cleaned-vault-prices-1h.parquet \
  poetry run python scripts/hyperliquid/daily-vault-metrics.py
```

### Inspect DuckDB after a scan

```shell
poetry run python -c "
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from pathlib import Path

db = HyperliquidDailyMetricsDatabase(Path('/tmp/hl-test.duckdb'))
print('Vaults:', db.get_vault_count())
print()
print('Metadata:')
print(db.get_all_vault_metadata()[['vault_address', 'name', 'tvl', 'apr']].to_string())
print()
print('Price rows per vault:')
prices = db.get_all_daily_prices()
print(prices.groupby('vault_address').size().to_string())
db.close()
"
```

### Run integration tests

```shell
# Both tests (unified JSON + resume)
source .local-test.env && poetry run pytest \
  tests/hyperliquid/test_daily_metrics_integration.py \
  tests/hyperliquid/test_daily_metrics_resume.py \
  -x --timeout=300

# All Hyperliquid tests
source .local-test.env && poetry run pytest tests/hyperliquid/ -x -n auto --timeout=300
```

### Full end-to-end: scan + generate JSON

```shell
# 1. Scan specific Hyperliquid vaults into DuckDB + merge into pipeline files
LOG_LEVEL=info \
  VAULT_ADDRESSES=0xdfc24b077bc1425ad1dea75bcb6f8158e10df303,0x1e37a337ed460039d1b15bd3bc489de789768d5e \
  DB_PATH=/tmp/hl-e2e.duckdb \
  VAULT_DB_PATH=/tmp/vault-metadata-db.pickle \
  PARQUET_PATH=/tmp/cleaned-vault-prices-1h.parquet \
  poetry run python scripts/hyperliquid/daily-vault-metrics.py

# 2. Generate JSON from the merged pipeline files
VAULT_DB_PATH=/tmp/vault-metadata-db.pickle \
  PARQUET_PATH=/tmp/cleaned-vault-prices-1h.parquet \
  OUTPUT_PATH=/tmp/vault-metrics.json \
  poetry run python scripts/erc-4626/vault-analysis-json.py

# 3. Inspect the JSON
python -c "
import json
with open('/tmp/vault-metrics.json') as f:
    data = json.load(f)
print(f'Total vaults: {len(data[\"vaults\"])}')
for v in data['vaults'][:5]:
    print(f'  {v[\"chain_id\"]:>6}  {v[\"protocol\"]:<15}  {v[\"name\"]}')
"
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `warning` | Logging level (debug, info, warning, error) |
| `DB_PATH` | `~/.tradingstrategy/hyperliquid/daily-metrics.duckdb` | DuckDB database path |
| `VAULT_ADDRESSES` | *(all)* | Comma-separated vault addresses to scan (overrides `MIN_TVL`/`MAX_VAULTS`) |
| `MIN_TVL` | `10000` | Minimum TVL in USD to include a vault |
| `MAX_VAULTS` | `500` | Maximum number of vaults to process |
| `MAX_WORKERS` | `16` | Parallel worker threads for API calls |
| `VAULT_DB_PATH` | `~/.tradingstrategy/vaults/vault-metadata-db.pickle` | ERC-4626 VaultDatabase pickle to merge into |
| `PARQUET_PATH` | `~/.tradingstrategy/vaults/cleaned-vault-prices-1h.parquet` | Cleaned Parquet to merge into |

## Key modules

| Module | Role |
|--------|------|
| `eth_defi/hyperliquid/daily_metrics.py` | DuckDB storage, share price computation, parallel scanning |
| `eth_defi/hyperliquid/vault_data_export.py` | Bridge to ERC-4626 pipeline (VaultRow builder, Parquet/pickle merge) |
| `eth_defi/hyperliquid/combined_analysis.py` | `_calculate_share_price()` -- time-weighted return logic |
| `eth_defi/hyperliquid/vault.py` | API client (`fetch_all_vaults`, `HyperliquidVault`, `VaultInfo`) |
| `eth_defi/hyperliquid/session.py` | Rate-limited HTTP session (`create_hyperliquid_session`) |
