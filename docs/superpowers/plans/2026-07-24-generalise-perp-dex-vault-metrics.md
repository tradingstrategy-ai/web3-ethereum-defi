# Perp DEX vault account and position collection plan

**Goal:** collect the smallest useful set of current, source-level facts from
perp DEX vault accounts and their open positions, then calculate comparable
exposure metrics from those facts. Do not store aggregates that can be
reconstructed from the position rows.

**Initial scope:** all account-based perp DEX vault protocols present or already
researched in this repository:

- Hyperliquid/Hypercore
- Lighter, including its Ethereum and Robinhood deployments
- GRVT
- Hibachi
- ApeX Omni
- Pacifica lakes, which remain unsupported pending native price and timestamp-skew work

ERC-4626 wrappers that happen to trade on a perp DEX are outside this account
adapter. Their existing onchain vault data remains in the ERC-4626 pipeline.

**Explicitly deferred:** margin-account type, cross/isolated/portfolio-margin
mode, collateral, available balance, initial or maintenance margin,
liquidation price, margin utilisation, allocated margin, permitted or effective
leverage, deployed cash, open orders, PnL decomposition and funding. The source
endpoints may return these fields, but the new version 1 collector must discard
them rather than normalise, store or publish them.

This means `max_position_leverage`, `gross_leverage`, `deployed_cash` and
`deployed_cash_fraction` are no longer version 1 metrics. They cannot be
calculated faithfully without collecting the margin information that is now
out of scope.

## Decisions

1. Persist one account observation and one row per non-zero open position.
2. Persist no long, short, gross, net, position-count or concentration
   aggregates.
3. Use a signed position notional as the minimum normalised position value. It
   is positive for long and negative for short.
4. Whitelist only the source fields required for equity, signed notional,
   identity, timestamps and completeness in the trimmed raw payload. Do not
   retain unrelated account or position fields for possible future use.
5. A protocol without public vault positions still produces an account
   observation and an explicit position-data availability state.
6. Never interpret unavailable positions as an empty portfolio.
7. Never use authenticated trader credentials to fill a public-vault data gap
   in version 1.
8. Treat account equity as optional audit context. A missing or independently
   timed equity response must never suppress an otherwise complete position
   observation.
9. Persist the capability registry with the raw Parquet artefact so that
   re-cleaning old data cannot silently apply today's protocol declarations.

## Minimum persisted data

### Account observation

Persist one row for each successful vault account read:

| Field | Purpose |
|---|---|
| `protocol_slug` | Stable protocol identity |
| `deployment_slug` | Distinguishes deployments such as Lighter Ethereum and Robinhood |
| `vault_id` | Native vault/account identifier |
| `dataset_chain_id` | Chain/synthetic-chain key used by the price Parquet |
| `dataset_address` | Canonical address/synthetic-address key used by the price Parquet |
| `snapshot_id` | Unique immutable key for one written bundle, shared only by that account row and its position set |
| `observed_at` | Naive UTC collector time |
| `written_at` | Naive UTC database-write time used to order corrections |
| `equity_effective_at` | Equity source timestamp when available |
| `position_effective_at` | Position-state source timestamp when available; otherwise collector time |
| `total_equity` | Optional current vault/account equity in its declared quote currency |
| `quote_asset` | USDC, USDT, USDG or another exact source denomination |
| `position_data_status` | Source state: `available`, `not_public`, `authentication_required`, `source_error`, or `not_implemented` |
| `position_data_reason` | Short protocol-specific explanation |
| `position_set_complete` | True only after the complete position response has been validated and written |
| `source_endpoint` | Endpoint or endpoint set used for the observation |
| `raw_payload_reference` | SHA-256 key of the trimmed protocol-native payload row |
| `collector_version` | PEP 440 parser/collector version used to order otherwise simultaneous corrections |

`total_equity` is the only normalised account amount in version 1, and it is
nullable because no version 1 exposure metric depends on it. Do not copy
collateral, balances or margin summaries into the common table. Store
`total_equity` physically when the adapter collected it and null when it did
not; do not introduce an indirect native-price reference whose absence could
be confused with a failed equity read. Equity collection failure does not
change an otherwise valid position state to `source_error`.

### Open-position observation

Persist one row for each source position whose source quantity is non-zero:

| Field | Purpose |
|---|---|
| Account observation key | Joins the position to one account snapshot |
| `source_market_id` | Protocol-native market/instrument identifier |
| `signed_notional` | Current quote-asset exposure; positive long, negative short |
| `quote_asset` | Must match the position valuation denomination |
| `valuation_basis` | Source position value, mark price, oracle price or another documented basis |
| `valuation_observed_at` | Timestamp of the position value or price input |
| `source_endpoint` | Endpoint and field path used to construct the value |

This row is the fundamental normalised input for the requested metrics. It is
not an aggregate. If the API reports signed notional directly, retain it. If it
reports a signed/side quantity and a current price, calculate:

```text
signed_notional = signed_quantity × current_valuation_price × contract_multiplier
```

The adapter must document the sign mapping and contract multiplier. Do not use
entry price to value current exposure. Reject the position observation when
the price is absent, non-positive, denominated differently or older than the
adapter's documented `maximum_position_valuation_skew`.

The trimmed raw payload preserves only the source inputs used to construct the
account and position observations: identity, equity, market, sign/quantity,
current position value or current valuation price, timestamps and response
completeness markers. This leaves a recoverable audit trail without collecting
the deferred account and position fields.

Store each trimmed payload once in `perp_vault_source_payloads`, keyed by its
SHA-256 hash. The row contains protocol/deployment identity, capture time,
canonical JSON payload, payload schema version and collector version.
`raw_payload_reference` is a foreign key to this table. The common writer
inserts or deduplicates the payload and writes its observation bundle in one
transaction; dangling references are forbidden.

## Derived metrics

Calculate these at export/query time from all available position rows belonging
to one completed account observation:

```text
long_notional = sum(signed_notional where signed_notional > 0)
short_notional = sum(abs(signed_notional) where signed_notional < 0)
gross_notional = long_notional + short_notional
net_notional = long_notional - short_notional
open_position_count = count(position rows)
largest_position_notional = max(abs(signed_notional))
largest_position_fraction = largest_position_notional / gross_notional
```

`largest_position_fraction` is null when gross notional is zero. All monetary
results remain in the declared quote asset. An optional USD presentation layer
is separate and must record its conversion rate, source and timestamp.

Do not persist `long_notional`, `short_notional`, `gross_notional`,
`net_notional`, `open_position_count`, `largest_position_notional` or
`largest_position_fraction`. Storing the position rows makes all of them
deterministically reproducible.

## Live API capability audit

The following public responses were checked on 2026-07-24. The check used the
repository's current endpoint configuration and representative live vaults.
Official documentation is linked where it describes the relevant API. Web-app
endpoints without authoritative field documentation must be labelled
`observed`, covered by captured fixtures and monitored for schema drift.

| Protocol | Public account/equity source | Public open-position source | Minimum usable inputs | Version 1 result |
|---|---|---|---|---|
| Hyperliquid | Vault listing `summary.tvl`; `vaultDetails` for identity. The official [`clearinghouseState`](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals) also reports account state, but its margin fields are ignored. | `clearinghouseState.assetPositions[].position` | `coin`, signed `szi`, current `positionValue`; live response also offered entry price, PnL, margin, liquidation and leverage fields, which are excluded | Account and positions |
| Lighter | [`publicPoolsMetadata`](https://apidocs.lighter.xyz/reference/publicpoolsmetadata) and [`account`](https://apidocs.lighter.xyz/reference/account-1): `total_asset_value` | Public pool `account.positions`; the documented all-position channel is also public for a public pool | `market_id`/`symbol`, `sign`, `position`, `position_value`; margin, liquidation, leverage and order fields are excluded | Account and positions |
| GRVT | Public GraphQL vault listing plus `POST /full/v1/vault_detail`: `total_equity`, share price and supply | GRVT's official [`full/v1/positions`](https://api-docs.grvt.io/trading_api/) returns signed `size` and signed `notional`, but requires a sub-account ID and authenticated trading-account headers | Public vault detail supplies equity only; no public mapping from a vault listing to an anonymously readable position set was found | Account only; positions `authentication_required` |
| Hibachi | Observed public `GET /vault/info`: per-share price and outstanding shares, from which the existing reader obtains current TVL | No public vault-position endpoint was found. Hibachi's own documentation states that [positions stay private](https://docs.hibachi.xyz/hibachi-docs/about-hibachi) | Vault ID, share price and supply; `marginingAssetId` is not normalised as account-margin data | Account only; positions `not_public` |
| ApeX Omni | Observed public `GET /api/v3/vault/ranking`: `tvl`, `vaultNetValue`, shares and update time | The official [`GET /v3/account`](https://api-docs.pro.apex.exchange/) returns positions only for the authenticated user. The public vault ranking/history endpoints expose no current vault positions | Vault ID, TVL, NAV, shares and source time | Account only; positions `authentication_required` |
| Pacifica | Public `GET /lake/list` and `GET /account`: `last_checked_equity`/`account_equity` | Public `GET /positions`; `GET /info/prices` supplies current marks | Position `symbol`, `side`, `amount` plus same-snapshot mark price and timestamp. Margin, isolation and liquidation fields are excluded | Unsupported until the native reader and timestamp-skew TODO are complete |

### Live response notes

- Hyperliquid returned a representative vault with 37 open positions. A
  position contained `coin`, signed `szi` and positive `positionValue`. The
  adapter signs `positionValue` from `szi`; it must not treat the always-positive
  value as long.
- Lighter's Ethereum LLP returned 189 position slots, including 186 non-zero
  positions in the sampled response. Empty placeholders must be filtered by
  source position quantity. The adapter signs the absolute position value from
  `sign`, after fixture tests verify the sign enumeration.
- GRVT `vault_detail` returned `total_equity`, `share_price`,
  `total_supply_lp_tokens` and `unrealized_pnl`. The PnL field remains native
  payload only. The authenticated position schema would otherwise be an ideal
  fit because its notional is already signed.
- Hibachi returned three vaults with share price and outstanding supply, but no
  position collection route. Its privacy model makes this an expected
  limitation rather than a temporary zero-position state.
- ApeX ranking returned TVL, vault NAV, shares and an update timestamp. Its
  `fund-net-values` endpoint is history only, not an account-position source.
- Pacifica returned 195 lakes. A sampled active lake exposed 15 public
  positions, and the price endpoint exposed a timestamped mark for each market.
  Because notionals require a second response, the adapter must enforce a
  maximum price/position timestamp skew.

## Per-protocol end-to-end paths

Every row below uses the same observation dataclasses, three common DuckDB
tables, derivation helper, temporal join and seven cleaned-Parquet columns. The
only protocol-specific code is a small API source reader and its declarative
identity/sign mapping.

Current account equity already reaches the cleaned Parquet through the existing
native price export as `total_assets`; do not add a duplicate perp-equity
column. The new generic path below adds position availability and the four
position-summary basis values. The account observation's equity remains the
optional audit value from its own timestamped response; only the
position/valuation skew rule gates exposure publication.

| Protocol | Collect in source reader | Store | Pass to cleaned Parquet |
|---|---|---|---|
| Hyperliquid | Read vault identity/equity and `clearinghouseState`; map `coin`, sign of `szi`, absolute `positionValue` and response time | Common tables in `HyperliquidDailyMetricsDatabase`; this is the sole observation store even when HF price rows are enabled | Join common summaries to the already combined daily/HF frame produced by `build_hypercore_prices_dataframe()` using chain `9999` and lower-case vault address |
| Lighter | Read the public-pool `/account` response; map account index/deployment, `total_asset_value`, market, `sign`, non-zero `position` and absolute `position_value` | Common tables in `LighterDailyMetricsDatabase` | Join to `build_lighter_prices_dataframe()` rows using shared chain `9998` and the deployment-specific synthetic address generated by the source reader |
| Pacifica | Unsupported; retain parser groundwork behind a TODO until mark/position timestamp skew is validated | No production observation store | Not routed to raw Parquet, cleaned Parquet or JSON |
| GRVT | Read public vault detail for equity; emit `authentication_required` and no positions | Common tables in `GRVTDailyMetricsDatabase` | Join status and snapshot time to `build_grvt_prices_dataframe()` using chain `325` and lower-case vault ID; numeric position columns remain null |
| Hibachi | Read `/vault/info` equity inputs; emit `not_public` and no positions | Common tables in `HibachiDailyMetricsDatabase` | Join status and snapshot time to `build_hibachi_prices_dataframe()` using chain `9997` and `hibachi-vault-{id}`; numeric position columns remain null |
| ApeX | Read public vault ranking equity/NAV; emit `authentication_required` and no positions | Common tables in `ApexMetricsDatabase` | Join status and snapshot time to `build_apex_prices_dataframe()` using chain `9995` and the existing synthetic ApeX address; numeric position columns remain null |

The source reader must produce `dataset_chain_id` and `dataset_address` using the
same shared identity helper as the protocol's price exporter. Do not duplicate
address formatting in the collector and exporter. Move existing synthetic
address constructors into reusable protocol identity functions where needed.

Adding another perp DEX requires:

1. a source reader returning the common observation bundle;
2. a protocol capability/identity declaration;
3. the shared schema helper called from its existing/new metrics database; and
4. its native price DataFrame registered with `merge_native_protocols()`.

It must not require new exposure columns, aggregation code, temporal-join code,
cleaning branches or final JSON branches.

## Recent Lighter PR analysis

The recent Lighter metrics PR is the reference for append-only snapshots and
correct missing-data semantics, but not for the breadth of the common schema.
Its principal implementation commits replace shares-only reads with typed PnL
history, add a rich account snapshot and expose current operator ownership.

### What the PR did

1. Parsed daily PnL history and retained source cumulative counters for shares,
   flows, PnL and volume.
2. Added `LighterPoolSnapshot` from `/api/v1/account`, including account values,
   positions, exposure aggregates, margin data, PnL, funding, assets, strategies
   and pending unlocks.
3. Stored latest total and operator shares as a collection-time ownership
   snapshot rather than backfilling it through history.
4. Derived daily deposits and withdrawals only from consecutive completed
   UTC-day counter deltas.
5. Added fixture, migration, storage, flow, export and regression coverage.

### What to retain

- Append-only point-in-time observations and auditability through deliberately
  trimmed source-input retention.
- First observations, gaps, source nulls and counter resets remain unknown
  rather than becoming zero.
- Snapshot-only facts are never backfilled over historical price rows.
- A partial or shortened response never deletes previous valid history.
- Existing protocol-native fields are not silently deleted during migration,
  but the new common collector does not add more of them.

### What to change

The PR's wide Lighter snapshot is not the cross-protocol storage contract.
Normalise only `total_equity` and one signed notional per non-zero position.
Do not copy the PR's gross/net/long/short/largest-position aggregates into the
shared store. Do not copy its collateral, balance, margin, leverage or
liquidation fields into version 1.

The Lighter-specific table may remain for backwards compatibility if the PR is
merged. The common adapter reads the two minimum facts from it or from the same
source response, and its own trimmed payload uses the version 1 whitelist.

## Common contract

Add an `eth_defi.perp_dex` package containing typed shared definitions and
calculation helpers. Protocol HTTP and parsing code stays in each protocol
package.

Suggested types:

- `PerpVaultIdentity`
- `PerpVaultAccountObservation`
- `PerpVaultPositionObservation`
- `SourcePositionDataStatus`
- `PerpParquetDataStatus`
- `PositionValuationBasis`
- `DerivedPerpVaultExposure`

Use `dataclass(slots=True)`, `Decimal` for source monetary parsing and naive UTC
datetimes. Every network read function uses the `fetch_` prefix.

Keep source and pipeline states separate. `SourcePositionDataStatus` contains
only `available`, `not_public`, `authentication_required`, `source_error` and
`not_implemented`; adapters and DuckDB may write only these values.
`PerpParquetDataStatus` additionally contains `not_collected`,
`not_applicable` and `stale`, which may be materialised only by the shared
Parquet finaliser. Storage validation rejects pipeline-only states in source
observations.

The common validator must enforce:

- one account identity, bounded collection bundle and position-effective time
  per position set, where the bound applies to position and valuation inputs
  rather than optional equity;
- unique source market IDs within one position set;
- no zero signed notionals in stored position rows;
- finite numeric values and positive valuation prices;
- one quote asset per comparable position set;
- documented sign conversion and valuation basis;
- no derived exposure when position data is unavailable or incomplete;
- empty available position arrays mean a genuine zero-position account only
  after the adapter has validated response completeness.

An account can legitimately have equity while its position metrics are
unavailable. The JSON output must keep those two availability decisions
separate.

## Storage and export design

The single data path is:

```text
protocol API
  -> protocol source reader
  -> common account + position observations
  -> common DuckDB tables in the protocol database
  -> common exposure derivation
  -> generic bounded latest-row alignment and backward as-of join
  -> vault-prices-1h.parquet
  -> ordinary cleaning pipeline
  -> cleaned-vault-prices-1h.parquet
  -> generic final JSON derivation
```

Protocol-specific code ends at the source reader. Storage, aggregation, temporal
joining, Parquet columns and final JSON calculation are shared.

### Generic helpers

Add these modules under `eth_defi.perp_dex`:

| Module | Shared responsibility |
|---|---|
| `adapter.py` | Declarative capability registry and reproducible Parquet metadata |
| `metrics.py` | Typed observations, validation and pure exposure derivation |
| `storage.py` | Common DuckDB schema, migrations, atomic bundle writes and reads |
| `parquet.py` | Canonical Parquet columns, defaults and the bounded temporal join |
| `export.py` | Build `other_data.perp_dex` from the latest cleaned row |

Define the seven Arrow fields once as `PERP_VAULT_PARQUET_FIELDS` and reuse
that definition in the raw schema migration, DataFrame normalisation and
cleaned writer. `normalise_perp_metric_parquet_dtypes()` must preserve
`perp_open_position_count` as nullable `int64` rather than allowing Pandas to
promote it to float, and must truncate `perp_metrics_observed_at` explicitly to
whole seconds represented by Arrow `timestamp[ms]`. Parquet does not round-trip
`timestamp[s]`, but one-second value resolution is sufficient for account-level
metrics and avoids treating ordinary collector microseconds as a schema error.
It rejects timezone-aware values, NaN/infinite monetary values and statuses outside
`PerpParquetDataStatus`; Pandas' inferred datetime unit is never the contract.

Each small protocol reader returns
`PerpVaultObservationBundle(account, positions)` and owns only source endpoint
calls, source field/sign conversion and identity mapping. Hyperliquid and
Lighter use the same orchestration shape: `joblib.Parallel` with the threading
backend performs network reads, then the database owner thread validates and
atomically writes every completed bundle through
`write_perp_vault_observation_bundle()`. A failed or shortened position read
must not commit an `available` bundle. The native scanners expose
`MAX_WORKERS`; storage, validation, correction handling, aggregation, Parquet
joining and JSON export remain generic.

Each declarative capability record supplies its dataset chain/address mapping,
price-row quote asset, collection cadence, position/valuation skew and
public-position support. The temporal join validates the observation quote
asset against this declaration; it does not rely on protocol branches or infer
denomination from symbol names. Freshness uses the version-wide global maximum
age, not a per-protocol override.

`PerpDexCapabilityRegistry` serialises these declarations as canonical JSON
with a schema version and SHA-256 hash. `merge_native_protocols()` embeds the
exact registry JSON, version and hash in raw Parquet schema metadata; the
cleaned writer copies them unchanged. Cleaning loads the embedded registry and
must not import the current global registry to reinterpret an existing raw
artefact. A legacy raw Parquet without this metadata requires an explicit,
atomic migration that stamps a chosen registry version before cleaning;
ordinary cleaning aborts instead of selecting today's registry implicitly.
Adding a protocol creates a newly merged raw artefact carrying the updated
registry. Version 1 uses one global `PERP_METRICS_MAX_AGE`, with no
protocol-specific freshness override, so post-clean `forward_fill_vault()`
can enforce freshness deterministically without reopening registry state.

Most bundles use more than one HTTP response. Position publication is a
bounded position/valuation bundle, not an assertion that equity and positions
came from one response:

- `equity_effective_at` records the equity endpoint's source time;
- `position_effective_at` records the position endpoint's source time and is
  the time used for exposure joining;
- each position retains its own `valuation_observed_at`;
- `observed_at` records completion of the collector bundle;
- the position response and every separate valuation response must fall within
  the capability record's `maximum_position_valuation_skew`, measured using
  source timestamps where present and collector receipt times otherwise.

If the position/valuation bundle exceeds this skew, write
`source_error`/incomplete availability and no position metrics. An optional
equity response is stored with its independent timestamp but is excluded from
this gate. Hyperliquid listing TVL may therefore lag `clearinghouseState`
without suppressing positions, and Pacifica `/account` may lag while
`/positions` and `/info/prices` still form a valid exposure snapshot. Neither
is misrepresented as one atomic API response.

`storage.py` idempotently creates the following tables inside each protocol's
existing DuckDB database:

```text
perp_vault_source_payloads
perp_vault_account_observations
perp_vault_position_observations
```

Key account observations by their unique `snapshot_id`; key position rows by
`snapshot_id` and source market ID. A snapshot ID is generated once per
written bundle and is never reused, including by a correction. Use append-only
writes. The correction-selection identity is protocol, deployment, dataset
chain/address and `position_effective_at`, explicitly excluding `snapshot_id`.
A correction repeats that effective-time identity with a new snapshot ID.
Before derivation, select the greatest `written_at`; break a `written_at` tie
by the highest PEP 440 `collector_version`. Rows still tied at that rank must
have identical semantic account, position and payload content after excluding
write-identity fields, or the pipeline aborts as ambiguous. Only after this
correction selection does duplicate
identity/effective-time validation run. Derivation left-joins positions solely
through the selected account row's snapshot ID, so removed or re-valued
positions from an older bundle cannot leak into its correction. A correction
never deletes unrelated history.

An available empty portfolio is represented by a complete account bundle and
zero position rows. An unavailable or incomplete portfolio has a
non-available source status and no derived metrics. Derivation is driven by
the selected account observations and left-joins their position rows; it must
not group from the position table. Therefore each complete `available`
account produces exactly one metric snapshot, including a real all-zero
snapshot when no position rows exist.

Do not create one central production database. Each native protocol scanner
keeps its existing DuckDB/artefact boundary and uses the shared table/schema
helper.

### Cleaned price Parquet contract

Add the following canonical nullable columns to
`RawVaultPriceRow`, `VaultHistoricalRead.to_pyarrow_schema()`,
`CleanedVaultPriceRow` and `VAULT_STATE_COLUMNS`:

| Cleaned column | Arrow type | Meaning |
|---|---|---|
| `perp_long_notional` | `float64` | Sum of positive position notionals in `perp_quote_asset` |
| `perp_short_notional` | `float64` | Sum of absolute negative position notionals |
| `perp_open_position_count` | nullable `int64` | Count of non-zero source market positions |
| `perp_largest_position_notional` | `float64` | Largest absolute position notional |
| `perp_quote_asset` | `string` | Exact valuation denomination |
| `perp_position_data_status` | `string` | Availability/completeness state |
| `perp_metrics_observed_at` | `timestamp[ms]`, whole-second values | Actual position measurement time at one-second resolution; retained with aligned and stale values |

Do not add gross notional, net notional or concentration columns. Consumers
derive them from the four numeric basis columns:

```text
gross_notional = perp_long_notional + perp_short_notional
net_notional = perp_long_notional - perp_short_notional
largest_position_fraction =
    perp_largest_position_notional / gross_notional
```

For a validated empty portfolio, long, short, count and largest position are
genuine zeroes and concentration is null because gross notional is zero. For
`not_public`, `authentication_required`, `source_error`, `not_implemented` or
`not_collected`, all four numeric columns are null. A stale available
observation retains its numeric values and original
`perp_metrics_observed_at`, allowing consumers to apply their own age policy.
Registered perp vault rows before their first observation use `not_collected`;
non-perp vault rows use `not_applicable`.

These Parquet columns are a reproducible materialised analytics view, not new
source facts. The minimum-storage rule applies to the DuckDB observation
tables; the scalar Parquet values exist so users do not need to read
variable-length position tables.

### Generic temporal join

Add one `derive_perp_vault_metric_snapshots()` helper that starts from
correction-selected account observations and left-joins positions by snapshot.
It emits one row for every complete `available` account, including an empty
account with four genuine zero basis values, and emits an unavailable status
with null basis values for every non-available account. Add one
`attach_perp_metrics_to_price_rows()` helper that:

1. concatenates metric snapshots from all enabled protocol DuckDB files;
2. applies the correction precedence and only then rejects remaining
   duplicate identity/effective-time snapshots;
3. stably sorts price rows by timestamp then chain/address, and snapshots by
   `position_effective_at` then chain/address, as required by the supported
   Pandas `merge_asof` implementation;
4. performs a backward `merge_asof`, with
   `by=["chain", "address"]`, from each price timestamp to the
   latest position-effective timestamp with no age tolerance;
5. runs a bounded generic latest-row overlay after the ordinary join: when one
   or more account observations are later than every price row for that
   account, attach only the newest eligible observation to the latest price
   row if the gap is no more than 48 hours; Lighter's daily API may still
   report the previous UTC-midnight row on the following day. A transient
   `source_error` has no
   measured position state and is not aligned backwards over a valid as-of
   observation;
6. retains the actual `perp_metrics_observed_at` when the overlay is used, so
   Lighter and other delayed/daily feeds expose the measurement time rather
   than pretending the observation happened at UTC midnight;
7. relies on common bundle validation to require one matching account/position
   quote asset, while the embedded capability registry records the expected
   protocol denomination;
8. restores the original price-row order after the left-cardinality join;
9. leaves status and metric values null only when no eligible observation
   exists, rather than deciding default status or backfilling.

The overlay never spreads a newer observation across older history: only the
latest price row for the same account can receive it. If several observations
arrived after that latest price row, the newest eligible non-`source_error`
observation wins. The join is otherwise age-blind and preserves the latest
candidate's values, quote asset and actual observation time. It never applies
the maximum-age policy.
`finalise_perp_metric_columns()` is the sole freshness owner and converts an
old `available` candidate to `stale`.

Finalisation uses the one global `PERP_METRICS_MAX_AGE`. For each row it
calculates age as:

```text
price_row_timestamp - perp_metrics_observed_at
```

and marks an `available` value stale only when this row-relative age is
strictly greater than the maximum. It never compares with process wall-clock
time. A negative age is permitted only within the 48-hour delayed-feed
alignment window; observations further in the future abort the pipeline. The
attached observation timestamp makes any allowed negative age explicit. The
boundary is shared by raw-to-cleaned processing and `forward_fill_vault()`.

The production all-chain scheduler runs Hyperliquid and both Lighter
deployments every four hours. Their capability records therefore declare
`collection_cadence_seconds=14_400`; the global six-hour freshness boundary
allows one delayed cycle without treating older observations as fresh.

Static capability states `not_public` and `authentication_required` do not
become `stale`: they contain no time-varying position values. Their
`perp_metrics_observed_at` records the latest API-capability verification
observation.

Call this helper once in `merge_native_protocols()` after all enabled native
price replacement DataFrames and all generic observation snapshots have been
loaded, but before `_write_native_partitions_to_uncleaned_parquet()`. Do not add
metric-column construction to each protocol's
`build_raw_prices_dataframe()`.

The ordinary `process_raw_vault_scan_data()` path then carries these canonical
columns and the embedded capability registry through cleaning.
`ensure_vault_state_columns()` supplies typed null defaults. After every
cleaning operation that can forward-fill state, call
`finalise_perp_metric_columns(frame, embedded_registry)` exactly once before
writing the cleaned Parquet. It is the sole owner of default status:

- preserve explicit joined statuses;
- assign `not_collected` to a registered perp row with no matching
  observation;
- assign `not_applicable` to a non-perp row;
- for an `available` row older than the global maximum age, change status to
  `stale` while retaining long, short, count and largest position;
- retain `perp_quote_asset` and `perp_metrics_observed_at` on stale rows so
  consumers can diagnose denomination and age and choose their own freshness
  threshold.

`forward_fill_vault()` is used after the cleaned Parquet is loaded by lifetime
metrics and sparkline paths. It must call the same
`finalise_perp_metric_columns()` after its hourly resample/forward-fill, so
display/JSON code cannot extend an available observation beyond its maximum
age. At this stage statuses are already non-null; the helper uses only the
global age rule and aborts if classification would require a missing registry.
No protocol-specific cleaning algorithm is needed.

If the latest account snapshot is newer than every available price row, the
bounded alignment may attach it only to the latest row for the same account.
It must not appear on any earlier row. This makes current Lighter daily data
available without backfilling future information through historical/backtest
rows, and keeps the real measurement time visible to consumers.

### Final JSON path

At final export:

1. Read only the latest cleaned price row already selected by the generic
   lifetime-metrics pipeline.
2. Pass its seven `perp_*` columns to `build_perp_dex_other_data()`.
3. Derive gross, net and concentration from long, short and largest position.
4. Serialise the additive result under `other_data.perp_dex`.
5. Never reopen a protocol DuckDB or call a protocol API from JSON export.
6. Leave any existing source-specific output under `other_data.lighter` and
   equivalent namespaces unchanged for compatibility. Do not add deferred
   account or margin fields to new protocol outputs.

Do not put `total_equity` inside `other_data.perp_dex`: the existing cleaned
`total_assets` column is the price-series account-equity value and retains its
own price-row timestamp. The observation-table `total_equity` is a
same-snapshot source fact used for validation/audit and is not silently
substituted for `total_assets`. Version 1 calculates no equity-denominated perp
ratio, so it does not need to claim that the price row and position snapshot
are simultaneous.

JSON monetary and ratio values are finite JSON numbers, never decimal strings;
`open_position_count` and `schema_version` are JSON integers. Convert the
canonical Parquet `float64` values to Python floats and use the JSON encoder's
shortest round-trip representation without additional display rounding. Emit
unavailable values as JSON `null`, and reject NaN or infinity rather than
producing non-standard JSON. `observed_at` is a whole-second naive UTC ISO-8601
string. These rules belong to the generic exporter and apply equally to every
protocol.

Example common output:

```json
{
  "schema_version": 1,
  "observed_at": "2026-07-24T12:00:00",
  "quote_asset": "USDC",
  "position_data_status": "available",
  "long_notional": 800000.0,
  "short_notional": 600000.0,
  "gross_notional": 1400000.0,
  "net_notional": 200000.0,
  "open_position_count": 12,
  "largest_position_fraction": 0.25
}
```

For an account-only source, all derived position metrics are null and
`position_data_status` explains why. They must not be emitted as zero.

## Implementation phases

### Phase 0: freeze fixtures and the contract

- Capture redacted live fixtures for an active and empty vault for
  Hyperliquid and Lighter. Keep Pacifica parser fixtures as unsupported
  groundwork only.
- Capture account-only fixtures for GRVT, Hibachi and ApeX.
- Record endpoint, field path, unit, sign convention, valuation basis,
  timestamp and completeness behaviour in an adapter capability declaration.
- Generate the documentation capability matrix from these declarations so code
  and docs cannot silently diverge.
- Add contract tests for duplicate markets, zero placeholders, sign mapping,
  mixed quote assets, stale price joins and unavailable position sets.
- Freeze the seven cleaned-Parquet column names/types and the generic backward
  join, bounded latest-row alignment and maximum-age policy.
- Freeze the source-status and Parquet-status enums, canonical capability
  registry serialisation, correction ordering, payload hash format and numeric
  JSON representation.

**Gate:** approve the version 1 decisions, cleaned-Parquet schema and
exact JSON names before adding migrations.

### Phase 1: shared observations and derivation

- Add `eth_defi/perp_dex/{adapter,metrics,storage,parquet,export}.py` and its
  Sphinx API stub.
- Add the shared dataclasses, two status enums, capability registry, validators,
  three tables, payload store, atomic bundle writer and idempotent DuckDB
  migrations.
- Add pure derivation tests covering long-only, short-only, hedged, flat,
  concentrated and unavailable accounts.
- Add account-led derivation and correction-precedence tests, including a
  validated empty portfolio with zero position rows, legitimate corrections
  and ambiguous equal-rank corrections that must abort.
- Add the seven canonical columns to `RawVaultPriceRow`,
  `VaultHistoricalRead.to_pyarrow_schema()`, `CleanedVaultPriceRow` and
  `VAULT_STATE_COLUMNS`.
- Add the common metric-snapshot reader and
  `attach_perp_metrics_to_price_rows()` call once in
  `merge_native_protocols()`.
- Add `finalise_perp_metric_columns()` once at the end of cleaned-data
  processing and after `forward_fill_vault()` resampling. This helper alone
  assigns default/stale statuses; stale position values remain attached to
  their original measurement timestamp.
- Embed the canonical capability registry in raw/cleaned Parquet metadata and
  add the explicit migration gate for legacy raw artefacts without it.
- Add `build_perp_dex_other_data()` to the lifetime-metrics/JSON export path;
  it reads only cleaned columns and does not branch on protocol.
- Use typed null defaults for all Parquet schema additions. A migration/cast
  failure aborts and never resets existing Parquet or DuckDB data.

**Gate:** fixture-only tests prove that every published aggregate is
reproduced from source position rows, only the declared materialised basis
columns enter the cleaned Parquet, and final JSON never reads protocol storage.

### Phase 2: Lighter reference adapter

- Map `total_asset_value` to the account observation.
- Filter `positions` using non-zero source quantity.
- Map market ID/symbol and signed current `position_value`.
- Ignore account trading mode, balances, collateral, margin requirements,
  allocated margin, liquidation, leverage and orders in the common adapter.
- If the PR is merged, leave its richer native snapshot intact for
  compatibility, but whitelist only version 1 inputs in the new common
  collector payload.
- Write through the common bundle helper; do not add Lighter-specific
  aggregation, Parquet-column or JSON-export code.
- Test both Lighter deployments and the Robinhood LLP-index override.

### Phase 3: Hyperliquid adapter

- Reuse vault listing/details for identity and current equity.
- Fetch `clearinghouseState` for the vault address and collect only `coin`,
  `szi`, `positionValue` and the response time needed by the common adapter.
- Ignore `marginSummary`, `crossMarginSummary`, `marginUsed`, leverage and
  liquidation fields.
- Sign absolute position value from `szi`, and filter zero sizes.
- Write through the common bundle helper; do not add Hyperliquid-specific
  aggregation, Parquet-column or JSON-export code.
- Keep existing deposits, PnL reconstruction and high-frequency price
  pipelines unchanged.

### Phase 4: Pacifica unsupported groundwork

- Mark Pacifica unsupported in its module, protocol README, shared capability
  documentation and vault-script README.
- Keep the public `/lake/list`, `/account`, `/positions` and `/info/prices`
  parser groundwork and sign-conversion fixture for future implementation.
- Leave a TODO requiring a native price reader, DuckDB integration,
  all-chain scheduling and enforced mark/position timestamp-skew validation.
- Do not register Pacifica in the production capability registry or route its
  observations to raw Parquet, cleaned Parquet or JSON.

### Phase 5: account-only adapters

- GRVT: store public `total_equity`; publish positions as
  `authentication_required`. Do not call the authenticated trading API.
- Hibachi: reuse current TVL/equity calculation; publish positions as
  `not_public`.
- ApeX: store ranking TVL and source timestamp; publish positions as
  `authentication_required`.
- Write all three account-only states through the same common bundle helper;
  do not add protocol-specific null columns or cleaning branches.
- Add explicit tests that these states do not yield zero exposure or zero open
  positions.
- Document each limitation alongside its reader:
  - `scripts/grvt/README-grvt-vaults.md`
  - `scripts/hibachi/README-hibachi-vaults.md`
  - `eth_defi/hibachi/README.md`
  - `scripts/apex/README-apex-vaults.md`
  - `eth_defi/apex/README-apex.md`

If a protocol later exposes public vault positions, add the position mapping
behind a new fixture and capability update; no common schema migration should
be required.

### Phase 6: documentation and rollout

- Add `docs/source/vaults/perp-dex-account-metrics.rst` and an index entry.
- Document formulas, units, valuation basis, timestamps, availability
  semantics, endpoint authentication, the API-to-DuckDB-to-raw-to-cleaned data
  flow, the seven cleaned columns and known protocol gaps.
- Update each protocol README with its exact source fields and fields
  intentionally excluded. Link to the shared document for storage, join,
  Parquet and JSON behaviour instead of duplicating generic implementation
  details in six READMEs.
- Add a native-protocol capability table to
  `scripts/erc-4626/README-vault-scripts.md`. It must show account-equity and
  open-position support separately, including GRVT and ApeX as
  `authentication_required` and Hibachi as `not_public`.
- Document in `scripts/erc-4626/README-vault-scripts.md` that every supported
  protocol follows the common observation tables and temporal join, and list
  the seven resulting `cleaned-vault-prices-1h.parquet` columns.
- In every GRVT, Hibachi and ApeX README listed in phase 5, state:
  - which public vault endpoints are used for equity/NAV;
  - that the protocol does not provide anonymously readable current vault
    positions through the integrated API;
  - whether the limitation is authentication (`GRVT`, `ApeX`) or protocol
    privacy (`Hibachi`);
  - that exposure, open-position count and concentration are therefore
    unavailable/null, never zero;
  - that margin-account endpoints or trader credentials are intentionally out
    of scope;
  - the verification date and links to the relevant official API/privacy
    documentation.
- Roll out Lighter, Hyperliquid, GRVT, Hibachi and ApeX. Pacifica remains
  unsupported until the phase 4 TODO is completed.
- Validate each change against copied production DuckDB and Parquet artefacts
  before normal scans publish the cleaned columns and additive JSON.

## Verification

- Ensure `.local-test.env` exists or copy it from the main checkout; never edit
  it.
- Run focused tests through `source .local-test.env && poetry run pytest ...`
  with a 180-second timeout.
- Test fresh and legacy DuckDB migrations, repeated scans, partial responses,
  source errors, empty position arrays, sign conventions, quote-asset
  mismatches, price skew, raw-payload references and JSON compatibility.
- Test payload hash deduplication, transaction rollback and rejection of a
  dangling `raw_payload_reference`.
- Test separate equity/position/valuation timestamps at, below and above the
  declared maximum position/valuation skew; an over-skew
  position/valuation bundle must not publish exposure, while arbitrarily old
  or missing optional equity must not suppress a valid position bundle.
- Test source tables reject `not_collected`, `not_applicable` and `stale`, and
  the Parquet finaliser rejects unknown source or pipeline states.
- Test the generic raw-to-cleaned path for all three capability classes:
  positions available, validated empty portfolio and positions unavailable.
- Assert an empty `available` account survives account-led derivation with
  zero long, short, count and largest values even though its snapshot has no
  position rows.
- Test correction ranking by `written_at` and PEP 440 `collector_version`
  before duplicate validation, including equal-rank identical idempotent rows
  and conflicting equal-rank rows that must abort. Assert a corrected bundle
  that removes or re-values a market reads positions only from its new
  snapshot ID and cannot inherit rows from the superseded bundle.
- Test exact-time and backward as-of joins, bounded alignment of a newer
  snapshot to only the latest delayed-feed row, the maximum-age boundary,
  stale value retention, rows before the first snapshot and mixed
  protocol/deployment identities.
- Shuffle both inputs and assert the generic `merge_asof` sorting/restoration
  path produces identical results and row order without cross-vault matches.
- Assert the age-blind join retains an old candidate and its timestamps, while
  only `finalise_perp_metric_columns()` changes it from `available` to `stale`.
- Freeze time in tests and prove freshness depends only on
  `price_row_timestamp - perp_metrics_observed_at`, not the current clock.
- Test that both raw-to-cleaned forward fills and
  `forward_fill_vault()` hourly resampling reapply the same freshness rule;
  stale rows retain quote asset, observation time and position numbers.
- Test that only `finalise_perp_metric_columns()` assigns `not_collected` and
  `not_applicable`. Prove an old raw file re-cleans identically from its
  embedded capability registry after the code registry changes; missing or
  hash-invalid metadata must abort until an explicit migration stamps it.
- Test `normalise_perp_metric_parquet_dtypes()` produces exact Arrow
  `timestamp[ms]` with whole-second values and nullable `int64` fields from
  supported Pandas/Arrow inputs, truncates collector microseconds and rejects
  timezone-aware timestamps.
- Assert old raw/cleaned Parquet files gain typed null columns without changing
  row count or unrelated values, and partial native scans preserve untouched
  protocol/deployment partitions.
- Assert final JSON values equal calculations from the latest cleaned row and
  that JSON export performs no API or DuckDB reads. Assert finite monetary and
  ratio fields are JSON numbers, counts are integers, unavailable values are
  null, timestamps have second precision and NaN/infinity are rejected.
- Format touched Python with Poetry Ruff and build the relevant Sphinx
  documentation.
- Test every schema migration against a copy of the production Parquet data.
- Treat source-schema drift, unexplained position-count collapse or migration
  failure as a release blocker.

## Later extensions

Only after the account/position collection is stable should a separate plan
consider:

- base quantity, mark/oracle price and entry price as first-class common facts;
- unrealised/realised PnL and funding;
- cross, isolated and portfolio-margin semantics;
- leverage, liquidation distance and deployed capital;
- open orders and order-adjusted exposure;
- historical position snapshots and turnover;
- source-reported versus locally calculated performance/risk statistics.

These additions must remain additive. They must not change the meaning of
version 1 signed notional or reinterpret unavailable historical observations.
