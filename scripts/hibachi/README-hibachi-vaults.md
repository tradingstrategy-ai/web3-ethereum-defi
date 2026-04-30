# Hibachi native vault metrics pipeline

This pipeline fetches vault metadata and daily share price history
from the Hibachi public data API, stores it in DuckDB, and merges
the data into the unified ERC-4626 vault pipeline.

## Architecture

```
Hibachi data API → DuckDB → VaultDatabase pickle → uncleaned Parquet → cleaning pipeline → JSON export
```

The pipeline follows the same pattern as GRVT and Lighter native vault integrations.

## API documentation

See [eth_defi/hibachi/README.md](../../eth_defi/hibachi/README.md) for reverse-engineered
API endpoint documentation.

## Running the standalone pipeline

```shell
# Basic usage
poetry run python scripts/hibachi/daily-vault-metrics.py

# With debug logging
LOG_LEVEL=info poetry run python scripts/hibachi/daily-vault-metrics.py

# Scan specific vaults
VAULT_IDS=2,3 poetry run python scripts/hibachi/daily-vault-metrics.py
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `warning` | Logging level |
| `DB_PATH` | `~/.tradingstrategy/vaults/hibachi-vaults.duckdb` | DuckDB database path |
| `VAULT_IDS` | (all) | Comma-separated vault IDs to scan |
| `VAULT_DB_PATH` | `~/.tradingstrategy/vaults/vault-metadata-db.pickle` | VaultDatabase pickle path |
| `PARQUET_PATH` | `~/.tradingstrategy/vaults/vault-prices-1h.parquet` | Uncleaned Parquet path |

## Production usage

In the production scanner (`scan-vaults-all-chains.py`), Hibachi is
enabled via the `SCAN_HIBACHI=true` environment variable. It can also
be scheduled via `SCAN_CYCLES="...,Hibachi=4h"`.

## Known vaults (as of 2026-04-30)

| vaultId | Symbol | Name | Strategy |
|---|---|---|---|
| 2 | GAV | Growi Alpha Vault | Mean-reversion on crypto perps |
| 3 | FLP | Fire Liquidity Provider | Market making across all markets |

Both vaults are denominated in USDT. Chain ID 9997 is a synthetic
in-house identifier (not an EVM JSON-RPC chain ID).
