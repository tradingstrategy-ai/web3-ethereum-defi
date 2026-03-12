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
vault_metadata                        vault_daily_prices
==============                        ==================
vault_address           VARCHAR PK    vault_address            VARCHAR  \
name                    VARCHAR       date                     DATE      > composite PK
leader                  VARCHAR       share_price              DOUBLE
description             VARCHAR       tvl                      DOUBLE
is_closed               BOOLEAN       cumulative_pnl           DOUBLE
allow_deposits          BOOLEAN       daily_pnl                DOUBLE
commission_rate         DOUBLE        daily_return             DOUBLE
follower_count          INTEGER       follower_count           INTEGER
tvl                     DOUBLE        apr                      DOUBLE
apr                     DOUBLE        is_closed                BOOLEAN
create_time             TIMESTAMP     allow_deposits           BOOLEAN
last_updated            TIMESTAMP     leader_fraction          DOUBLE
flow_data_earliest_date DATE          leader_commission        DOUBLE
                                      daily_deposit_count      INTEGER
                                      daily_withdrawal_count   INTEGER
                                      daily_deposit_usd        DOUBLE
                                      daily_withdrawal_usd     DOUBLE
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

### Leader share

Hyperliquid requires vault leaders to maintain a minimum ownership stake of 5%
of total vault capital (verified 2026-03-09). The `vaultDetails` API returns a
`leaderFraction` field representing the leader's current capital share (e.g.
`0.05` = 5% of vault capital is owned by the leader).

We track this as `leader_fraction` in `vault_daily_prices` to monitor how the
leader's skin-in-the-game evolves over time. Only the latest daily row carries
the value; historical rows have `NULL` (we only know the current snapshot).

The API also returns a `leaderCommission` field which we store as
`leader_commission`. The exact semantics of this field are not yet fully
understood — it may represent accumulated commission in USD or an alternative
commission metric distinct from `commissionRate` (the profit-share percentage).

Source: [Vault leader docs (legacy)](https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-leaders-legacy)

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
- `leader_fraction` -- leader's capital share of the vault (e.g. 0.10 = 10%), latest row only
- `leader_commission` -- leader commission value from the API (semantics unclear), latest row only
- `daily_deposit_count` -- number of deposit events on that day
- `daily_withdrawal_count` -- number of withdrawal events on that day
- `daily_deposit_usd` -- total USD deposited on that day
- `daily_withdrawal_usd` -- total USD withdrawn on that day (positive value)

### Deposit/withdrawal netflow metrics

The pipeline tracks daily deposit and withdrawal flows per vault using the
Hyperliquid `userNonFundingLedgerUpdates` API. These are aggregated into
`NetflowMetrics` dataclasses with 1d, 7d, and 30d periods in the JSON output.

Flow data is only fetched for **complete days** (up to yesterday 23:59:59 UTC)
to avoid partial-day artefacts. The backfill window is controlled by the
`FLOW_BACKFILL_DAYS` environment variable (default 7). Each scan fills the
most recent N complete days and uses `COALESCE` upserts so older data is never
overwritten with NULL.

The `flow_data_earliest_date` column in `vault_metadata` tracks how far back
flow data has been backfilled per vault.

Chains that do not support netflow (all EVM chains) will have `null` for the
`netflow` field in the JSON output.

## Backfilling netflow data for existing vaults

If you have an existing DuckDB database and want to backfill deposit/withdrawal
flow data further back than the default 7 days, use `FLOW_BACKFILL_DAYS`:

```shell
# Backfill 90 days of deposit/withdrawal flow data
LOG_LEVEL=info FLOW_BACKFILL_DAYS=90 python scripts/hyperliquid/daily-vault-metrics.py
```

For a specific set of vaults:

```shell
LOG_LEVEL=info FLOW_BACKFILL_DAYS=90 \
  VAULT_ADDRESSES=0xdfc24b077bc1425ad1dea75bcb6f8158e10df303,0x1e37a337ed460039d1b15bd3bc489de789768d5e \
  poetry run python scripts/hyperliquid/daily-vault-metrics.py
```

The backfill is idempotent — running it multiple times will not produce
duplicates, and existing flow data is preserved via `COALESCE` upserts.
There is no known API retention limit for `userNonFundingLedgerUpdates`,
so you can backfill as far as the vault has existed.

After a one-time backfill, set `FLOW_BACKFILL_DAYS` back to 7 (or omit it)
for regular daily runs to avoid unnecessary API calls.

## Healing share price data

The share price computation has evolved over time. If the production DuckDB
contains data computed with older logic, run the combined healer to fix it
**with minimal destruction of historical rows**.

```shell
# Dry run: detect issues without modifying data
DRY_RUN=true poetry run python scripts/hyperliquid/heal-all-share-prices.py

# Heal all vaults (offline recomputation + API re-fetch for stuck ones)
poetry run python scripts/hyperliquid/heal-all-share-prices.py

# Heal and run downstream cleaning pipeline
RUN_PIPELINE=true poetry run python scripts/hyperliquid/heal-all-share-prices.py

# Heal specific vaults only
VAULT_ADDRESSES=0x4dec0a851849056e259128464ef28ce78afa27f6 \
  poetry run python scripts/hyperliquid/heal-all-share-prices.py

# Offline recomputation only (skip API re-fetch)
SKIP_REFETCH=true poetry run python scripts/hyperliquid/heal-all-share-prices.py
```

The script runs these steps automatically:

1. **Detect** — scan for broken vaults (epoch resets, stuck prices, etc.)
2. **Offline recomputation** — recompute share prices from stored
   `tvl` / `daily_pnl` / `cumulative_pnl` without any API calls or data
   deletion. Fixes epoch reset artefacts and populates the `epoch_reset`
   column.
3. **API re-fetch** — for vaults still stuck at share price 1.0 after
   the offline fix, delete and re-fetch from the Hyperliquid API with
   multi-period merge. Only the stuck vaults are re-fetched; all other
   vaults keep their existing data.
4. **Verify** — re-run detection and report remaining issues.
5. **Pipeline** (optional, `RUN_PIPELINE=true`) — push healed data
   through the downstream cleaning pipeline.

### What each step fixes

| Issue | Step 2 (offline) | Step 3 (API re-fetch) |
|-------|------------------|-----------------------|
| Share price jumps to 1.0 at epoch boundaries | Yes | Yes |
| `epoch_reset` column is NULL | Yes | Yes |
| Share price at cap (>= 9,999) | Yes | Yes |
| Share price stuck at 1.0 (daily granularity loss) | No | Yes |
| Missing data points (multi-period merge) | No | Yes |

### Individual scripts

The combined script replaces the individual scripts, which are still
available for targeted use:

- `heal-share-prices-offline.py` — offline recomputation only
- `heal-share-prices.py` — API re-fetch only (for specific vaults)

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

Start `./vault-shell.sh` and then

```shell
SCAN_HYPERCORE=true \
  DISABLE_CHAINS=Ethereum,Arbitrum,Base,Polygon,Avalanche,Optimism,Binance,Sonic,Berachain,Unichain,Mantle,Mode,Abstract,Celo,Soneium,zkSync,Gnosis,Blast,Zora,Ink,Hemi,Linea,TAC,Plasma,Katana,Monad,HyperEVM \
  LOG_LEVEL=info \
  python scripts/erc-4626/scan-vaults-all-chains.py
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
| `FLOW_BACKFILL_DAYS` | `7` | Number of complete days to backfill deposit/withdrawal flow data |
| `VAULT_DB_PATH` | `~/.tradingstrategy/vaults/vault-metadata-db.pickle` | ERC-4626 VaultDatabase pickle to merge into |
| `PARQUET_PATH` | `~/.tradingstrategy/vaults/cleaned-vault-prices-1h.parquet` | Cleaned Parquet to merge into |

## Trade history reconstruction

Separate from the daily metrics pipeline, we can reconstruct per-account
trade history (fills, funding payments, ledger events) and compute
event-accurate share prices.

This uses the `userFillsByTime`, `userFunding`, `userNonFundingLedgerUpdates`,
and `clearinghouseState` API endpoints directly.

### Sync trade history to DuckDB

The `sync-trade-history.py` script fetches fills, funding, and ledger events
for whitelisted accounts into a DuckDB database. Incremental sync accumulates
data beyond the 10K fill API limit by fetching only new records on each run.

```shell
# Sync two specific addresses
ADDRESSES=0x1e37a337ed460039d1b15bd3bc489de789768d5e,0x3df9769bbbb335340872f01d8157c779d73c6ed0 \
  LABELS="Growi HF,IchiV3 LS" \
  LOG_LEVEL=info \
  poetry run python scripts/hyperliquid/sync-trade-history.py

# With custom DuckDB path
ADDRESSES=0x1e37a337ed460039d1b15bd3bc489de789768d5e \
  TRADE_HISTORY_DB_PATH=/tmp/trade-history.duckdb \
  LOG_LEVEL=info \
  poetry run python scripts/hyperliquid/sync-trade-history.py

# Re-run to sync only new data (incremental)
ADDRESSES=0x1e37a337ed460039d1b15bd3bc489de789768d5e \
  LOG_LEVEL=info \
  poetry run python scripts/hyperliquid/sync-trade-history.py
```

| Variable | Default | Description |
|----------|---------|-------------|
| `ADDRESSES` | *(none)* | Comma-separated addresses to add and sync |
| `LABELS` | *(none)* | Comma-separated labels matching `ADDRESSES` |
| `TRADE_HISTORY_DB_PATH` | `~/.tradingstrategy/hyperliquid/trade-history.duckdb` | DuckDB path |
| `MAX_WORKERS` | `1` | Parallel workers (DuckDB single-writer) |
| `LOG_LEVEL` | `warning` | Logging level |

### Display trade history for a single account

The `vault-trade-history.py` script reconstructs and displays:

1. Account overview (value, margin, raw USD)
2. Current open positions from clearinghouse state
3. Open round-trip trades with funding costs
4. Closed round-trip trades with PnL breakdown
5. Summary totals (realised, funding, fees, net, unrealised)
6. Event-accurate share price history (using actual deposit/withdrawal
   events instead of resolution-dependent portfolio history)

```shell
# Basic usage
ADDRESS=0x3df9769bbbb335340872f01d8157c779d73c6ed0 \
  poetry run python scripts/hyperliquid/vault-trade-history.py

# With longer history and logging
ADDRESS=0x1e37a337ed460039d1b15bd3bc489de789768d5e \
  DAYS=60 LOG_LEVEL=info \
  poetry run python scripts/hyperliquid/vault-trade-history.py

# With DuckDB persistence (sync first, then display)
ADDRESS=0x1e37a337ed460039d1b15bd3bc489de789768d5e \
  TRADE_HISTORY_DB_PATH=/tmp/trade-history.duckdb \
  LOG_LEVEL=info \
  poetry run python scripts/hyperliquid/vault-trade-history.py
```

| Variable | Default | Description |
|----------|---------|-------------|
| `ADDRESS` | *(required)* | Account address |
| `DAYS` | `30` | Days of history to fetch |
| `TRADE_HISTORY_DB_PATH` | *(none)* | Optional DuckDB path for persistent storage |
| `LOG_LEVEL` | `warning` | Logging level |

### Trade history DuckDB schema

Stored in a separate database from daily metrics (default:
`~/.tradingstrategy/hyperliquid/trade-history.duckdb`).

```
accounts                              fills
========                              =====
address            VARCHAR PK         address         VARCHAR  \
label              VARCHAR            trade_id        BIGINT    > composite PK
is_vault           BOOLEAN            ts              BIGINT
added_at           BIGINT             coin            VARCHAR
                                      side            TINYINT (0=buy, 1=sell)
funding                               sz              FLOAT
=======                               px              FLOAT
address            VARCHAR  \         closed_pnl      FLOAT
ts                 BIGINT    > PK     start_position  FLOAT
coin               VARCHAR  /         fee             FLOAT
usdc               FLOAT              oid             BIGINT
sz                 FLOAT
rate               FLOAT           ledger
                                   ======
sync_state                         address         VARCHAR  \
==========                         ts              BIGINT    > PK
address            VARCHAR  \      event_type      VARCHAR  /
data_type          VARCHAR  > PK   usdc            FLOAT
oldest_ts          BIGINT          vault           VARCHAR
newest_ts          BIGINT
row_count          INTEGER
last_synced        BIGINT
```

### Trade history tests

```shell
# Unit tests (no network)
poetry run pytest tests/hyperliquid/test_trade_history.py -x --timeout=300

# Integration tests (requires network)
source .local-test.env && poetry run pytest \
  tests/hyperliquid/test_trade_history_integration.py \
  -x --timeout=300 --log-cli-level=info
```

## Key modules

| Module | Role |
|--------|------|
| `eth_defi/hyperliquid/daily_metrics.py` | DuckDB storage, share price computation, parallel scanning |
| `eth_defi/hyperliquid/deposit.py` | Deposit/withdrawal event fetching and daily flow aggregation |
| `eth_defi/hyperliquid/vault_data_export.py` | Bridge to ERC-4626 pipeline (VaultRow builder, Parquet/pickle merge) |
| `eth_defi/hyperliquid/combined_analysis.py` | `_calculate_share_price()` -- time-weighted return logic |
| `eth_defi/hyperliquid/vault.py` | API client (`fetch_all_vaults`, `HyperliquidVault`, `VaultInfo`) |
| `eth_defi/hyperliquid/session.py` | Rate-limited HTTP session (`create_hyperliquid_session`) |
| `eth_defi/hyperliquid/trade_history.py` | Trade history reconstruction, round-trip trades, event-accurate share prices |
| `eth_defi/hyperliquid/trade_history_db.py` | DuckDB persistence for fills, funding, ledger with incremental sync |
