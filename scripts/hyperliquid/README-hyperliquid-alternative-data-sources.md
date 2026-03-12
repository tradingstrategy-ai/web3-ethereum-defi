# Alternative data sources for Hyperliquid vault historical data

Research conducted 2026-03-11.

## Problem

Hyperliquid vaults have sparse historical data because the native API's `allTime` period
provides only **weekly snapshots** for data older than 30 days. For example, the NEET vault
(`0x4cb5f4d145cd16460932bbb9b871bb6fd5db97e3`) has 60 rows stored but should have 112
(one per day). The first 2 months are weekly-only, creating a jigsaw share price curve.

## What we need for share price calculation

Two time series with synchronised timestamps:

- **`account_value_history`** — total vault NAV (TVL) at each point
- **`pnl_history`** — cumulative PnL at each point

From these we derive `netflow_update = (TVL[i] - TVL[i-1]) - (PnL[i] - PnL[i-1])`
and compute share prices via the ERC-4626 mint/burn model in `_calculate_share_price()`.

## Hyperliquid native API endpoints

| Endpoint | Data | Resolution | History depth | Used? |
|----------|------|-----------|---------------|-------|
| `vaultDetails` → `portfolio.allTime` | TVL + PnL snapshots | ~weekly (degrades) | Full lifetime | Yes |
| `vaultDetails` → `portfolio.month` | TVL + PnL snapshots | ~daily (~24h) | Last 30 days | Yes |
| `vaultDetails` → `portfolio.week` | TVL + PnL snapshots | ~2.5h | Last 7 days | Yes |
| `vaultDetails` → `portfolio.day` | TVL + PnL snapshots | ~30 min | Last 24 hours | Yes |
| `userNonFundingLedgerUpdates` | Deposit/withdrawal events | Individual events (ms) | **Full history**, paginated | Partially (last 7 days) |
| `stats-data.hyperliquid.xyz/vaults` | Bulk vault listing | Current snapshot | Current only | Yes |

### Notes on the `vaultDetails` portfolio periods

- Data is "sampled every 15 minutes plus on deposit/withdrawal events" server-side
- For `allTime`, older data is progressively down-sampled (weekly for data > ~2 months old)
- No custom date range queries — periods are fixed sliding windows
- `perp*` variants (`perpDay`, `perpWeek`, `perpMonth`, `perpAllTime`) contain identical data
  for perp-only vaults and are not currently used

### The `userNonFundingLedgerUpdates` opportunity

This endpoint returns **every deposit/withdrawal event** since vault creation with
no known retention limit. Events include:

- `vaultCreate`, `vaultDeposit`, `vaultWithdraw`, `vaultDistribution`, `vaultLeaderCommission`
- Each event has: exact timestamp (ms), USDC amount, user address, transaction hash
- Paginated at 2000 records per request via `startTime`/`endTime`

We already use this in `eth_defi/hyperliquid/deposit.py` but only backfill the last 7 days
(`flow_backfill_days=7`). Full historical backfill is possible and could be combined with
weekly `allTime` snapshots to produce flow-aware daily interpolation.

## External sources investigated

| Source | Per-vault data? | Notes |
|--------|----------------|-------|
| DefiLlama | HLP only | `GET https://api.llama.fi/protocol/hyperliquid-hlp` — daily TVL for HLP vault only, not individual vaults |
| Dune Analytics | No | Protocol-level bridge TVL and aggregate volume only. Community dashboards at `dune.com/x3research/hyperliquid` |
| Allium | No | Raw tables (`orders`, `fills`, `misc_events`) but no vault-level TVL/PnL. Enterprise access (Snowflake/BigQuery) |
| Artemis Analytics | No | Protocol-level `ez_metrics` only. Open-source data starts August 2025 |
| HyperEVM indexers (Covalent/GoldRush) | No | Hyperliquid native vaults operate on **Hypercore (the L1)**, not HyperEVM. EVM indexers cannot see vault state |
| ASXN dashboard | Visual only | `hyperliquid.asxn.xyz/vault_metrics` — 22+ vault metrics but no public API |
| Arkham Intelligence | No | Bridge-level entity tracking only. Paid API |
| `thunderhead-labs/hyperliquid-stats` | Possible | Open-source, powers `stats.hyperliquid.xyz`. Caches ledger into PostgreSQL. Would require running own instance |

## Hyperliquid S3 archive (requester-pays)

The S3 archive at `s3://hyperliquid-archive/` contains **significantly more data than documented**.
The official docs only mention L2 book snapshots and asset contexts, but the actual bucket
(as revealed by the [hyperliquid-dex/hyperliquid-stats](https://github.com/hyperliquid-dex/hyperliquid-stats)
repo) contains 9 data types in LZ4-compressed CSV format (`{type}/{YYYYMMDD}.csv.lz4`).

**Access**: Requester-pays — requires AWS credentials and `--request-payer requester`.

### S3 data types

| S3 prefix | Schema | Vault-relevant? |
|-----------|--------|-----------------|
| **`account_values/`** | `time, user, is_vault, account_value, cum_vlm, cum_ledger` | **YES — primary source for daily vault TVL backfill** |
| **`ledger_updates/`** | `time, user, delta_usd` | **YES — deposit/withdrawal deltas per address** |
| `non_mm_trades/` | `time, user, coin, side, px, sz, crossed, special_trade_type` | No |
| `liquidations/` | `time, user, liquidated_ntl_pos, liquidated_account_value, leverage_type` | No |
| `funding/` | `time, coin, funding, premium` | No |
| `asset_ctxs/` | `time, coin, funding, open_interest, oracle_px, mark_px, ...` | No |
| `market_data/` | L2 order book snapshots (hourly, per coin) | No |
| `hlp_positions/` | Daily HLP position data per coin | HLP only |
| `total_accrued_fees/` | Total accrued fee data | No |

### `account_values` — the key table

Daily snapshots for **every address** on Hyperliquid, including vaults:

```
time            | user       | is_vault | account_value | cum_vlm    | cum_ledger
2026-01-15 ...  | 0x4cb5...  | true     | 402758.60     | 123456.78  | 85000.00
```

- **`account_value`** = total vault NAV (our `tvl` / `cumulative_account_value`)
- **`cum_ledger`** = cumulative net deposits (deposits minus withdrawals from inception)
- **Derivable PnL**: `cumulative_pnl = account_value - cum_ledger`
- **`is_vault`** = boolean flag to filter vault addresses

This provides **both fields** needed for share price calculation at **daily resolution**
going back to the archive's start date — filling the exact gap we have.

### `ledger_updates` — deposit/withdrawal events

Per-address deposit/withdrawal deltas with exact timestamps:

```
time            | user       | delta_usd
2026-01-15 ...  | 0x4cb5...  | 50000.00
```

Positive = deposit, negative = withdrawal. Can be used to reconstruct exact daily netflow.

### Other S3 buckets

| Bucket | Relevant data |
|--------|---------------|
| `hl-mainnet-node-data/replica_cmds/` | Full L1 transaction stream — includes `VaultDeposit`, `VaultWithdraw`, `VaultCreate`, `VaultDistribution` events with full detail. Heavy to process |
| `artemis-hyperliquid-data/raw/perp_and_spot_balances/` | Daily JSONL snapshots of all addresses with full position data (from Aug 2025). Third-party (Artemis) |
| `hl-mainnet-evm-blocks/` | Raw HyperEVM block data. Not relevant (vaults are on Hypercore) |

## Conclusion

**The `hyperliquid-archive/account_values/` S3 prefix is the best source for backfilling
daily vault data.** It provides daily `account_value` and `cum_ledger` for every vault,
from which we can derive `cumulative_pnl` and compute share prices at daily resolution.

Combined with `ledger_updates/` for exact deposit/withdrawal timing, this eliminates
the need for interpolation — we get **real daily data** instead of estimates.

## What can be done

### 1. Ensure daily capture going forward (already done)

The daily scan pipeline (`daily-vault-metrics.py`) captures the `month` period's daily-resolution
data before it ages out of the 30-day window. Running this daily as a cron job means all vaults
will accumulate daily data going forward. This is already implemented and running.

### 2. Flow-aware daily interpolation for historical gaps

Between two weekly `allTime` snapshots at `t0` and `t7`:
- We know `TVL(t0)`, `TVL(t7)`, `cumPnL(t0)`, `cumPnL(t7)` from the API
- We can get every deposit/withdrawal event in `[t0, t7]` from `userNonFundingLedgerUpdates`
- Weekly PnL: `weekPnL = cumPnL(t7) - cumPnL(t0)`
- Weekly netflow: `weekNetflow = sum(deposits) - sum(withdrawals)`

Distribute PnL linearly across days, apply flows at their exact dates:

```
For each day d in [t0+1, t7]:
    daily_pnl_estimate = weekPnL / 7
    daily_netflow = sum(events on day d)
    TVL(d) = TVL(d-1) + daily_pnl_estimate + daily_netflow
```

This gives daily resolution that:
- Matches weekly API snapshots exactly at boundaries
- Places deposit/withdrawal impacts on the correct days
- Spreads PnL evenly (best available without intraday PnL data)
- Can be flagged as `interpolated=TRUE` to distinguish from real data

### 3. Simple linear interpolation (fallback)

For vaults with no deposit/withdrawal events in gap periods, linearly interpolate
share price between weekly points. Less accurate but still smoother than weekly jumps.

## Data availability summary by age

| Data age | Best available resolution | Source |
|----------|--------------------------|--------|
| 0–24 hours | ~30 min | `day` period (API) |
| 1–7 days | ~2.5 hours | `week` period (API) |
| 7–30 days | ~daily | `month` period (API) |
| 30+ days | **daily** | `account_values` (S3 archive) |
| 30+ days (fallback) | ~weekly | `allTime` period (API) + interpolation |
