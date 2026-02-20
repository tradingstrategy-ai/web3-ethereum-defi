# Hyperliquid native vault metrics pipeline

## Overview

Scans Hyperliquid native (non-EVM) vaults, computes time-weighted share prices,
and merges the data into the existing ERC-4626 vault metrics pipeline so that
`vault-analysis-json.py` produces a single unified JSON with both EVM and
Hyperliquid vaults.

Hypercore data goes through the same `process_raw_vault_scan_data()` cleaning
pipeline as EVM vaults (outlier share price smoothing, return cleaning,
TVL-based filtering, etc.).

## Architecture

```
Hyperliquid API                       ERC-4626 pipeline
==============                        =================

stats-data (bulk GET)                 scan-vaults.py (multi-chain)
  ~9000 vaults                          EVM vaults via HyperSync
      |                                      |
      v                                      v
Filter by TVL & open status           vault-metadata-db.pickle
      |                               vault-prices-1h.parquet (uncleaned)
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
  vault_metadata table                 vault-prices-1h.parquet (uncleaned)
  vault_daily_prices table                   |
                                             v
                                      process_raw_vault_scan_data()
                                        cap_hypercore_share_prices()
                                        fix_outlier_share_prices()
                                        calculate_vault_returns()
                                             |
                                             v
                                      cleaned-vault-prices-1h.parquet
                                             |
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

Hyperliquid native vaults use a synthetic chain ID of `9999` (constant
`HYPERCORE_CHAIN_ID`). 

### Denomination token

All Hyperliquid native vaults are denominated in USDC — the only settlement
currency on the platform. On Hypercore, USDC has token index `0`. Each token's
system address is derived from its index: first byte `0x20`, remaining bytes
all zeros except for the index in big-endian. For USDC (index 0) the system
address is `0x2000000000000000000000000000000000000000`.

See [Asset IDs](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids)
and [HIP-1](https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-1-native-token-standard)
in the Hyperliquid docs.

### DuckDB schema

See `constants.py` for storage.

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

### Fees

Hyperliquid vault fees are defined in `constants.py`:

- **User-created vaults** (`relationship_type="normal"`): 10% performance fee
  (`HYPERLIQUID_VAULT_PERFORMANCE_FEE`), zero management/deposit/withdraw fees
- **Protocol vaults** (HLP parent + children, `relationship_type="parent"` or `"child"`):
  zero fees

The fee mode is `internalised_skimming` — the leader's profit share is deducted
at withdrawal time, and the PnL history from the API already reflects net returns.
This means gross and net returns are identical from the pipeline's perspective.

Source: [Hyperliquid vault docs](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults)

### Lockup periods

Defined in `constants.py`:

- **User-created vaults**: 1-day lockup after deposit (`HYPERLIQUID_USER_VAULT_LOCKUP`)
- **Protocol vaults** (HLP + children): 4-day lockup (`HYPERLIQUID_PROTOCOL_VAULT_LOCKUP`)

Source: [Depositor docs](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors)

### Vault flags

All Hyperliquid vaults get the `perp_dex_trading_vault` flag. HLP child
sub-vaults (internal system vaults not directly investable) additionally get
the `subvault` flag which causes them to be filtered out via `BAD_FLAGS`.

### Vault notes

All Hypercore vaults get a default note via `get_notes(address, chain_id=...)`:
"Profit calculations are cleaned from deposit/redeem net flow and differ from
the account Profit and Loss (PnL) on Hyperliquid website".

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
  DISABLE_CHAINS=Ethereum,Arbitrum,Base,Polygon,Avalanche,Optimism,Binance,Sonic,Berachain,Unichain,Mantle,Mode,Abstract,Celo,Soneium,zkSync,Gnosis,Blast,Zora,Ink,Hemi,Linea,TAC,Plasma,Katana,Monad,Hyperliquid \
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
| `MIN_TVL` | `5000` | Minimum TVL in USD to include a vault |
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
