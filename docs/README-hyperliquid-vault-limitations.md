# Hyperliquid vault data limitations

Known issues and artefacts in the Hyperliquid native vault metrics pipeline.

## Share price resolution artefact

### Symptom

Some vaults show a dramatic share price spike (e.g. 6x) while TVL stays roughly flat.
This is inconsistent: if share_price = total_assets / total_supply, a spike in share price
with stable TVL implies total_supply collapsed, which would mean massive withdrawals —
but the TVL staying flat contradicts that.

### Example: Goon Edging (0x2431edfcb662e6ff6deab113cc91878a0b53fb0f)

- Stored data shows share_price peaking at **7.32** (2026-02-05)
- Live API recomputation gives max share_price of **2.64** — a 2.77x discrepancy
- TVL values match between both computations (~$70–100K range)
- Only the share price diverges

### Root cause

The Hyperliquid `vaultDetails` API returns portfolio history
(`account_value_history` and `pnl_history`) at different resolutions depending
on the time period requested:

- `day`: hourly resolution
- `week`: ~2-hour resolution
- `month`: daily resolution
- `allTime`: weekly resolution

As time passes, data points roll out of higher-resolution windows into
lower-resolution ones. A date that was in the `month` window (daily resolution)
when the pipeline originally ran may later only appear in `allTime`
(weekly resolution).

The share price calculation in `eth_defi/hyperliquid/combined_analysis.py`
(`_calculate_share_price()`) derives netflows from portfolio history:

```
netflow_update[i] = (account_value[i] - account_value[i-1]) - pnl_update[i]
```

It then iterates through time steps, minting/burning shares at the current
share price. This makes the calculation **path-dependent on data resolution**:

1. **At daily resolution**: Large PnL gains and withdrawals happening on
   different days create a compounding spiral:
   - Day N: PnL gain raises share_price
   - Day N+1: Withdrawals burn shares at the now-higher price (fewer shares burned)
   - Day N+2: More PnL raises share_price further on smaller total_supply
   - This feedback loop can amplify share price changes dramatically

2. **At weekly resolution**: The same PnL and withdrawal events are lumped
   into a single time step. The compounding spiral does not occur, producing
   a much more modest share price change.

### Affected period example (Goon Edging, Jan 29 – Feb 5 2026)

| Source | total_supply at Feb 4 | share_price at Feb 4 |
|--------|----------------------|---------------------|
| Stored (daily resolution) | ~13,900 | 5.65 |
| Live API (weekly resolution) | ~32,934 | 2.38 |

Same total_assets (~$78,500) but wildly different share prices because the
inferred netflow decomposition differs with resolution.

Once the spike is baked into stored data, all subsequent share prices remain
elevated because total_supply never recovers to the "correct" level.

### Impact

- Share price charts show false spikes that do not reflect real vault performance
- Return calculations derived from share price are inflated
- The artefact is permanent in stored data unless share prices are recomputed

### Potential mitigations

1. **Use ledger events instead of derived netflows**: The
   `userNonFundingLedgerUpdates` API provides actual `vaultDeposit` and
   `vaultWithdraw` events with exact amounts and timestamps. Using these
   instead of deriving netflows from portfolio history deltas would eliminate
   the resolution sensitivity. See `eth_defi/hyperliquid/deposit.py`.

2. **Periodic recomputation**: Re-run `_calculate_share_price()` from the
   current API data to correct historical drift. The pipeline already has
   `recompute_vault_share_prices()` in `daily_metrics.py` for this purpose,
   but it uses stored data rather than re-fetching from the API.

3. **Anomaly detection**: Flag vaults where share price change is inconsistent
   with TVL change. A heuristic: if `share_price_change_pct > N * tvl_change_pct`
   over a window, the data is likely artefacted.

4. **Vault notes**: Flag known affected vaults with irregular share price
   action notes (similar to JPYC vaults flagged in PR #827).

### Date discovered

2026-03-12, investigating vault
[goon-edging-2](https://tradingstrategy.ai/trading-view/vaults/goon-edging-2).
