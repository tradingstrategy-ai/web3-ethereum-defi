# Lighter skin-in-the-game and net-flow metrics plan

**Goal:** Export Lighter’s current operator ownership and historical pool cash
flows into the unified vault metrics JSON for synthetic chain `9998`, so they
can be used as selection priors without inventing missing event data.

**Architecture:** Keep Lighter’s public REST API as the sole source. Extend the
existing `/api/v1/account` snapshot path to retain `pool_info.operator_shares`
and `pool_info.total_shares` in `pool_metadata` and an append-only
`pool_snapshots` table. Extend the existing daily
`/api/v1/pnl` read to retain `pool_total_shares`, `pool_inflow`, and
`pool_outflow`; retain those cumulative counters in Lighter DuckDB and derive
day-to-day USD deltas only for export, then pass them through the existing
native-protocol flow columns and JSON exporter. The exported ownership data
belongs in the existing protocol-specific `other_data["lighter"]` extension;
do not overload Hyperliquid’s `leader_fraction` field.

**Why this is needed:** `fetch_pool_detail()` already parses
`operator_shares`, but `fetch_and_store_pool()` drops it. Likewise,
`fetch_pool_total_shares_history()` reads `/api/v1/pnl` only to compute TVL,
then `build_raw_prices_dataframe()` writes `total_supply=0.0` and no flow
columns. Consequently the chain-9998 records in the metrics feed contain
neither ownership skin nor cash-flow priors.

**Data-contract decisions (locked before implementation):**

- `operator_share_fraction = operator_shares / total_shares` is a scan-time
  snapshot. Append it and the other current account/pool fields to
  `pool_snapshots`; export the latest raw share counts, fraction, and
  `last_updated` provenance timestamp in `other_data["lighter"]`. Use `null`
  when total shares is zero or the source field is absent. Never backfill the
  first observation into dates before snapshot collection started.
- The PnL API reports cumulative `pool_inflow` and `pool_outflow`. Sort and
  de-duplicate by UTC day, retain the last sample per day in DuckDB, and
  calculate daily deposits/withdrawals as consecutive positive deltas at
  export. The first observed day, any delta spanning a missing UTC day, and the
  current incomplete UTC day have unknown flow and must be `null`, not the
  cumulative balance. Retaining raw counters prevents a bounded re-scan from
  replacing previously valid daily flow with a first-row `null`.
- The API does not supply transaction counts. Keep the existing
  `daily_deposit_count` and `daily_withdrawal_count` as `null` for Lighter;
  never substitute the number of days with a non-zero delta. Update the common
  netflow aggregator so amount-only native sources produce correct USD/net-flow
  summaries with `null` counts, while sources that have genuine counts retain
  their existing integers.
- A negative cumulative-counter delta is a reset/corrupt observation, not an
  outflow. Log it and emit `null` for that affected daily amount until the
  source contract is understood. Do not clip it to zero or create a false cash
  flow. A netflow period containing an unknown amount is incomplete: expose
  null monetary totals plus an explicit completeness indicator instead of
  silently reporting a partial sum as a complete period. Genuine event counts
  from other native sources remain independently reportable.
- Store `pool_total_shares` for each daily row and export it as native
  `total_supply`, rather than the current synthetic zero. Align it with share
  prices using the same per-day last-value/forward-fill policy already used for
  TVL. This is supporting accounting data; net flow must still come directly
  from the cumulative inflow/outflow counters, not from TVL or share changes.

---

## 1. Verify and model the public API contract

**Files:**

- Modify: `eth_defi/lighter/vault.py`
- Modify: `tests/lighter/test_lighter_api.py`
- Add/modify: a compact recorded-response fixture under `tests/lighter/` if
  the existing test module has no suitable fixture

- [ ] Inspect a representative public pool and the LLP with `/api/v1/account`
  and `/api/v1/pnl` using the existing `LighterSession`. Confirm that
  `operator_shares`, `total_shares`, `pool_total_shares`, `pool_inflow`, and
  `pool_outflow` have the documented units and whether `ignore_transfers=false`
  is required for the flow counters. Confirm explicitly that values are human
  collateral-currency units (USDC for Ethereum and USDG for Robinhood) rather
  than 6-decimal raw units, with a fixture assertion for that conversion
  decision. Record the relevant field shapes in the Lighter README; do not rely
  on an undocumented response shape.
- [ ] Replace the shares-only PnL helper with a typed daily-history model (for
  example a slots dataclass containing date, total shares, cumulative inflow,
  and cumulative outflow), or add a new helper alongside it and migrate the
  caller. Keep the API parsing separate from daily-delta derivation so both
  pieces are unit-testable.
- [ ] Use naive UTC conversion via
  `datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).replace(tzinfo=None)`,
  not host-local `fromtimestamp()` or deprecated `utcfromtimestamp()`, for PnL
  and share-price dates. Preserve the existing future-placeholder guard.
- [ ] Preserve graceful pool-level failure: if the PnL request fails or lacks
  the required flow fields, scan/share-price ingestion succeeds but writes
  null Lighter cumulative-counter values and logs the missing capability. The
  export must not turn unknown flows into zeroes.

**Acceptance:** Fixture tests prove exact parsing, optional/missing-field
handling, one record per UTC date, and the selected `ignore_transfers` request
parameter. A focused live API test is diagnostic only and must not hard-code
volatile balances.

## 2. Persist ownership and daily flow accounting in Lighter DuckDB

**Files:**

- Modify: `eth_defi/lighter/daily_metrics.py`
- Modify: `eth_defi/lighter/vault.py`
- Modify: `tests/lighter/test_daily_metrics.py`
- Add: `tests/lighter/test_lighter_flow_metrics.py`

- [ ] Add nullable `operator_shares` and `total_shares` columns to
  `pool_metadata`, and backfill existing database schemas with idempotent
  `ALTER TABLE ADD COLUMN` migrations. Populate both from `LighterPoolDetail`
  on every successful scan.
- [ ] Add an append-only `pool_snapshots` table for current ownership, fees,
  balances, margin requirements, order activity, exposure aggregates, and raw
  current API collections. Existing databases start with no historical
  snapshot rows; earlier joined dates must remain `NULL`/`NaN`.
- [ ] Add nullable `total_shares`, `cumulative_pool_inflow`, and
  `cumulative_pool_outflow` columns to `pool_daily_prices`, including
  idempotent migrations and `ON CONFLICT` update behaviour. Persist source
  counters, never derived daily deltas, and leave count columns absent because
  the API cannot substantiate them.
- [ ] Join PnL history onto the existing share-price dates before
  `upsert_daily_prices()`: forward-fill shares only and retain the raw
  cumulative values from their own dated observations. Do not forward-fill or
  persist flow *deltas*. Document and test the intentional limitation that a
  PnL observation without a retained share-price date cannot reach the current
  price-based export until a matching price observation exists.
- [ ] Make the write tuple/schema explicit (prefer a Lighter daily-row
  dataclass or named tuple over a positional tuple whose length is easy to
  desynchronise) and update the database getters/docstrings accordingly.
- [ ] Test a fresh database and an old-schema database. Cover metadata update,
  overlapping re-scan/upsert retention of raw counters, share history,
  cumulative `100 → 125 → 125` inflow and `0 → 0 → 20` outflow conversion,
  unknown first-day and incomplete-current-day flow, a missing-day gap, missing
  counters, and a decreasing counter. Assert that no test case manufactures
  event counts.

**Acceptance:** An existing `lighter-pools.duckdb` migrates without data loss;
new scans retain the latest ownership snapshot and source cumulative counters
from which the export can correctly derive daily USD flow values.

## 3. Carry Lighter data through the shared raw/cleaned metrics pipeline

**Files:**

- Modify: `eth_defi/lighter/vault_data_export.py`
- Modify: `eth_defi/research/wrangle_vault_prices.py`
- Modify: `eth_defi/research/vault_metrics.py`
- Modify: `tests/lighter/test_lighter_flow_metrics.py`
- Modify: `tests/research/test_clean_prices.py` if the shared-column
  preservation fixture needs the new native field

- [ ] Update `build_raw_prices_dataframe()` to derive daily deposit/withdrawal
  USD values from the stored cumulative counters only when consecutive,
  complete UTC-day observations exist, and export the stored Lighter
  `total_shares` as `total_supply`. Add explicit null-valued daily count
  columns; do not put a zero in any field merely to satisfy a common schema.
- [ ] Keep `operator_share_fraction` out of raw and cleaned price history: do
  **not** forward-fill it across price rows. Retain genuine scan-time history
  in `pool_snapshots`, while the metrics layer reads the latest value from
  Lighter metadata.
- [ ] Generalise `_calculate_netflow_metrics()` to require the two USD amount
  columns, with count columns optional. Return `None` only when monetary flow
  data is absent; when counts are unavailable, emit `deposit_count=None` and
  `withdrawal_count=None`. For a 1d/7d/30d window containing unknown amounts,
  emit null amount/net-flow fields and `data_complete=False` rather than a
  partial sum. Keep Hyperliquid’s existing complete results unchanged.
- [ ] Extend `NetflowMetrics` type annotations and JSON serialisation coverage
  for nullable counts and amounts plus `data_complete`. Test the same 1d/7d/30d
  boundaries used by current native records.
- [ ] In `calculate_vault_record()`, add a `lighter` object to `other_data`
  for Lighter records only, containing `operator_shares`, `total_shares`,
  `operator_share_fraction`, and `ownership_updated_at`. Read these from
  Lighter metadata (or the latest explicit snapshot), preserving `null` rather
  than coercing unknown values. Do not add Lighter-specific top-level metrics
  fields or change other protocols’ `other_data` payloads.

**Acceptance:** A chain-9998 raw row has real supply and daily USD flow
amounts; its cleaned/lifetime/JSON record has the existing `netflow` structure
with null counts and `other_data.lighter.operator_share_fraction`. EVM and
Hyperliquid rows retain their values and schemas.

## 4. Test the full export boundary and document semantics

**Files:**

- Add/modify: `tests/lighter/test_lighter_flow_metrics.py`
- Modify: `tests/lighter/test_daily_metrics.py`
- Modify: `scripts/lighter/README-lighter-vaults.md`

- [ ] Build a fixture-only Lighter DuckDB with at least three days, ownership
  metadata, shares, and cumulative flow counters. Run
  `build_raw_prices_dataframe()`, `merge_into_uncleaned_parquet()`,
  `generate_cleaned_vault_datasets()`, `calculate_lifetime_metrics()`, and
  `export_lifetime_row()` against temporary files.
- [ ] Assert the exported JSON has: chain `9998`; correct 1d/7d/30d monetary
  net-flow arithmetic for complete windows; null count fields; and exact raw
  ownership/count/fraction values under `other_data.lighter`. Assert unknown
  intervals create `data_complete=False` and null monetary results, while a
  no-flow Lighter fixture gets `netflow=None`, not a zero-flow claim.
- [ ] Add regression assertions that an existing Hyperliquid fixture continues
  to export integer counts and its current `leader_fraction` meaning; this
  protects the amount-only generalisation. Exercise the native-column Parquet
  migration against a copy of the production uncleaned file (or its exact
  schema/data fixture) to prove nullable Lighter count rows do not coerce or
  corrupt existing Hyperliquid count-column types.
- [ ] Update the Lighter architecture diagram, DuckDB schema table, PnL section,
  and export documentation. Explain: ownership is latest-scan data, PnL fields
  are cumulative and differenced, counts are unavailable, first observed day is
  unknown, and counter resets are withheld rather than reported as flow.

**Acceptance:** The focused tests prove end-to-end JSON output without a live
API dependency, while the documentation prevents downstream users from treating
the snapshot as historical ownership or `null` as zero.

## 5. Verification and rollout

- [ ] Ensure `.local-test.env` exists (copy it from the main checkout if this
  worktree lacks it; never edit it), then run:

  ```shell
  source .local-test.env && poetry run pytest tests/lighter/test_lighter_api.py tests/lighter/test_daily_metrics.py tests/lighter/test_lighter_flow_metrics.py tests/research/test_clean_prices.py -q
  ```

  Use a 180-second command timeout.

- [ ] Format only the touched Python files with `poetry run ruff format`, then
  rerun the focused tests.
- [ ] Run the Lighter script against copies of the production DuckDB **and
  uncleaned Parquet** with a temporary output directory first. Inspect schema
  migration, a known pool’s current operator-share fraction, non-null complete
  flow amounts, and unchanged existing native-column types before replacing any
  production-derived artefact.
- [ ] Re-run the usual metrics export and inspect a chain-9998 JSON record.
  Verify no existing raw Parquet rows/columns were discarded; Parquet migration
  errors are hard failures and must not be converted into an empty dataset.

**Out of scope:** Reconstructing individual Lighter deposit/withdrawal events,
backfilling operator ownership before point-in-time collection started,
changing selection policy/UI ranking, or adding onchain/RPC reads. Those
require data the stated public endpoints do not provide.
