# GMX V2 historical USD equity curves

Date: 2026-08-21

## Goal and scope

Add GMX V2 GM and GLV liquidity-provider tokens on Arbitrum and Avalanche to
the common vault dataset. Reuse the existing metadata database, EVM price scan,
Parquet files, cleaning and lifetime metrics. GMX V1 GLP and deposit/withdrawal
transaction construction are out of scope.

## Valuation model

GMX emits ``MarketPoolValueUpdated`` and ``GlvValueUpdated`` while processing
LP operations. Both events contain a pool value and its corresponding token
supply:

```text
share_price_usd = raw_value / raw_token_supply
total_assets_usd = share_price_usd × total_supply
```

For GM, accept only post-deposit updates. GMX uses different PnL-factor and
maximise/minimise settings for withdrawals, so alternating deposit and
withdrawal observations would create false returns. GLV records value and
supply after execution, and its mint or burn is proportional to the pre-flow
ratio. GMX notes that GLV values may omit shift, deposit or withdrawal fees
when a GLV oracle price is used. The result is an event-observed share-price
approximation rather than a continuous canonical NAV.

The result is a USD-denominated GMX share curve. It approximates a single-sided
USDC deposit only where USDC is accepted; actual deposit tokens are
product-specific. It does not simulate transaction-specific execution fees,
price impact, spreads or deposit/withdrawal fees.

## 1. Enumerate products and collect metadata

``eth_defi/gmx/vault_catalog.py`` enumerates current GM and GLV tokens from the
onchain GMX V2 Reader contracts. ``eth_defi/gmx/vault_sync.py`` upserts them
into the common ``VaultDatabase`` after ordinary lead discovery on Arbitrum and
Avalanche.

Each row records the product type, share token, current component and accepted
deposit tokens, synthetic USD denomination, and these features:

- ``gmx_gm`` or ``gmx_glv``;
- ``share_price_equivalence`` for a vault-like product without a standard
  contract share-price method.

GMX is classified as low technical risk and as market making plus liquidity
provision. The management/performance fee interface is zero. Request execution
costs are not manager fees.

## 2. Historical reader and minimal cache

Use the shared file:

```text
$PIPELINE_DATA_DIR/vault-historical-context.duckdb
```

GMX owns one table so future protocols can choose different observations:

```sql
CREATE TABLE gmx_historical_context (
    chain_id UINTEGER NOT NULL,
    block_number UBIGINT NOT NULL,
    block_timestamp UBIGINT NOT NULL,
    transaction_hash VARCHAR NOT NULL,
    log_index UINTEGER NOT NULL,
    product_address VARCHAR NOT NULL,
    raw_value UHUGEINT NOT NULL,
    raw_supply UHUGEINT NOT NULL,
    event_name VARCHAR NOT NULL,
    PRIMARY KEY (chain_id, transaction_hash, log_index)
);
```

The table stores only the GMX event fields required by the reader. Do not add a
generic payload envelope, calculated NAV, metadata, failure rows or scanner
cursors. Existing development caches using the earlier JSON envelope are
migrated transactionally when opened. Only corrected deposit-context rows are
copied; obsolete mixed-context rows are not copied.

``fetch_and_store_gmx_historical_share_prices()`` streams selected events via
Hypersync, commits each source chunk and inserts idempotently. It does not fetch
keeper oracle bundles or replay historical Reader calldata.

``GMXHistoricalReader`` reads the cache through the existing contextual-reader
hook and emits ordinary ``VaultHistoricalRead`` rows. It keeps the newest event
in each common block bucket. No GMX reader-state entry or archive-state price
call is required.

## 3. Apply the vault-protocol integration workflow

Keep the GMX adapter in the existing ``eth_defi/gmx`` package. Add:

- read-only GM and GLV ``VaultBase`` adapters;
- feature-to-adapter reconstruction and activity-filter exemption;
- protocol, fee, risk, note and strategy-flag classification;
- GMX API documentation and focused catalogue, routing and history tests.

Do not create a parallel ERC-4626 protocol package or duplicate GMX ABIs and
assets.

## 4. Integrate with the all-chain scanner

The Arbitrum and Avalanche price stage is:

```text
ordinary lead scan and GMX catalogue upsert
  → prefill GMX source observations
  → scan_prices_for_chain() once for all eligible EVM vaults
  → common raw Parquet
  → common cleaning and lifetime metrics
```

The contextual reader and static multicall readers share the same sparse row
filter and Parquet write. ``share_price_equivalence`` rows compare only share
price, so TVL/supply changes caused by LP flows do not create observations.
The normal 0.1% share-price threshold removes sub-threshold changes.

Raw observations remain sparse. ``calculate_lifetime_metrics()`` builds one
forward-filled daily series per vault and reuses it for every period. This is
an accepted approximation: unobserved days receive zero returns and the next
event day receives the accumulated movement. It keeps the common return, CAGR,
volatility, Sharpe and drawdown metrics available, while path-dependent metrics
remain operation-cadence-sensitive rather than continuous NAV statistics.

## 5. Migration and examination tools

Add four environment-variable-driven scripts:

1. ``seed-gmx-vaults.py`` enumerates current products and idempotently updates
   the common metadata pickle; dry-run mode does not write.
2. ``backfill-gmx-vault-prices.py`` prefills a bounded event range and invokes
   the common Parquet writer for only GMX addresses. It does not touch reader
   state and is safe to rerun.
3. ``examine-gmx-vault-backfill.py`` checks source linkage, duplicate keys,
   positive values, the asset identity and the common change threshold.
4. ``examine-gmx-vault-performance.py`` runs
   ``calculate_lifetime_metrics()`` and prints name, accepted tokens, TVL,
   lifetime CAGR, 3M CAGR and approximate 3M Sharpe.

No Parquet schema migration or second vault database is needed. Back up the
metadata pickle, raw Parquet and shared context DuckDB before the first
production backfill.

## Tests and acceptance

Unit tests cover catalogue pagination, V1 exclusion, idempotent observation
storage, integrity checking, chunk durability, bucket downsampling, adapter
routing, fee/risk/flag metadata and supply-normalised sparse filtering.

The fixed Arbitrum integration test must execute:

```text
Hypersync value-and-supply event
  → GMX DuckDB table
  → GMXHistoricalReader
  → common raw Parquet
  → endpoint return and CAGR calculation
  → calculate_lifetime_metrics()
```

The first version is complete when both chains enumerate GM/GLV products,
scheduled scans append event-backed USD observations through the ordinary
writer, bounded backfills are restartable, the examination scripts pass and
the focused integration test reaches ``calculate_lifetime_metrics()``.
