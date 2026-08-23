# GMX liquidity-provider vaults

This document describes how `eth_defi` represents GMX V2 liquidity-provider
products as vault-like investments and integrates their historical performance
with the common vault dataset.

The integration currently supports GM and GLV products on Arbitrum One and
Avalanche. GMX V1 GLP is intentionally not supported.

For operator commands, environment variables and production paths, see the
[ERC-4626 vault scripts README](../../scripts/erc-4626/README-vault-scripts.md#gmx-v2-liquidity-provider-vaults).

## What GM and GLV products are

GMX is a perpetual futures exchange. Its liquidity providers supply the assets
used to settle swaps and leveraged trading. In return, liquidity providers:

- receive a share of trading, liquidation, borrowing and swap fees;
- benefit when traders lose to the pool;
- bear trader profits and the market risk of the pool's assets.

A [GM market token](https://docs.gmx.io/docs/providing-liquidity/) represents a
pro-rata claim on one GMX market pool. The pool normally contains a long token
and a short token, such as WETH and USDC.

A GLV token represents a pro-rata claim on liquidity allocated across multiple
compatible GM markets. GLV is therefore a multi-market liquidity-provider
product rather than a single GM market.

GM and GLV shares are ERC-20 tokens, but they are not ERC-4626 vaults. They do
not expose the standard ERC-4626 share-price methods used by most EVM vaults,
and deposits and withdrawals are asynchronous GMX ExchangeRouter requests.

## What the performance curve represents

The integration derives a synthetic USD share-price equivalent from GMX's
onchain value-and-supply events:

```text
total assets (USD) = raw GMX value / 10^30
total supply       = raw token supply / 10^18
share price        = total assets / total supply
```

This ratio measures the USD value attributable to one GM or GLV share. It lets
GMX products use the same equity-curve, CAGR, volatility and Sharpe-ratio code
as vaults that publish a conventional share price.

The vault performance approximates the performance of a single-sided USDC deposit.

This is a comparison convention, not a transaction simulator. The curve does
not model the execution fee, price impact, token spread, deposit or withdrawal
fee, or waiting time faced by one particular depositor. These transaction costs
are also not management or performance fees in the `VaultBase` fee interface.
GMX exposes zero for those two manager-level fee fields.

The curve includes changes that affect the value of all existing shares, such
as pool asset prices, trader profit and loss, liquidity-provider fee revenue,
and any fees or rounding that change the value per share.

## Why deposits and withdrawals do not create false profit

A raw TVL curve is unsuitable for performance analysis: a large deposit raises
TVL and a large withdrawal lowers it even when existing liquidity providers
have made no profit or loss. The GMX reader instead divides matched pool value
and token supply from the same protocol event.

GM and GLV have slightly different event ordering:

- `MarketPoolValueInfo` contains GM market value and supply before the related
  mint or burn.
- `GlvValueUpdated` contains GLV value and supply after execution. GLV shares
  are minted or burned using the pre-flow ratio, so value and supply change
  proportionally in the absence of costs.

Consequently, deposit or withdrawal size alone does not mechanically rebase the
equity curve. Fees, rounding and price impact that genuinely change the value
per existing share remain visible.

This is still an event-observed share-price equivalent, not a continuously
sampled canonical NAV. GMX can calculate values in different execution
contexts, and the dataset records the values emitted during actual GM and GLV
operations.

The canonical event definitions are in GMX's
[`MarketEventUtils.sol`](https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/market/MarketEventUtils.sol)
and
[`GlvEventUtils.sol`](https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/glv/GlvEventUtils.sol).

## Integration architecture

```text
GMX Reader and GlvReader contracts
            │
            ▼
Current GM/GLV catalogue ───────────────▶ common vault metadata pickle
                                                    │
GMX EventEmitter                                    │ creates GMXVaultBase
            │                                       ▼
            │ Hypersync                    GMXHistoricalReader
            ▼                                       │
gmx_historical_context table                        │ contextual reads
in vault-historical-context.duckdb ─────────────────┘
            │
            ▼
common historical price writer
            │
            ▼
vault-prices-1h.parquet
            │
            ▼
common cleaning, daily series and lifetime metrics
```

The DuckDB file is an input cache, not a second vault dataset. Source
observations remain protocol-specific in DuckDB; calculated GMX price rows use
the same Parquet schema and downstream processing as other vaults.

## Catalogue enumeration

[`vault_catalog.py`](vault_catalog.py) enumerates the current GMX V2 catalogue
directly from the deployed contracts:

- `SyntheticsReader.getMarkets()` returns GM market definitions;
- `GlvReader.getGlvInfoList()` returns GLV definitions and their GM markets;
- `DataStore` flags determine whether a GM market or GLV market allocation is
  currently enabled;
- normal ERC-20 calls supply the share-token name, symbol and decimals.

REST or frontend listings are not used as product identity sources. Reader
calls are paginated, so the implementation does not assume a fixed product
count. Disabled products are retained because their historical price rows
remain valid.

[`vault_sync.py`](vault_sync.py) converts each product to a common vault
detection row. The persisted features are:

- `gmx_gm` or `gmx_glv`, selecting the correct adapter;
- `share_price_equivalence`, telling the sparse writer that only changes in the
  derived price represent performance.

The row also records component addresses, accepted deposit-token addresses and
the current enabled status. Catalogue updates are merged into the existing
vault database so unrelated rows and better previously collected metadata are
preserved.

## Vault adapters

[`vault.py`](vault.py) contains two concrete adapters:

- `GMXMarketVault` for one GM market token;
- `GMXLiquidityVault` for one GLV token.

Both inherit from `GMXVaultBase`, which in turn implements `VaultBase` directly.
They deliberately do not pretend to be ERC-4626 contracts.

The adapter provides the common read-only metadata surface and declares a
synthetic USD denomination. It also classifies the strategy as market making
and liquidity provision. GMX is classified as low technical protocol risk in
the common risk matrix; this classification does not remove market, oracle or
liquidity risk.

The following operations are intentionally unavailable:

- isolated `fetch_nav()`, `fetch_total_assets()` and `fetch_share_price()`
  calls, because historical valuation requires the matching GMX event context;
- the generic vault flow manager;
- the generic deposit manager, because GMX deposits and withdrawals are
  asynchronous ExchangeRouter requests;
- a generic spot portfolio, because GMX manages the underlying pool inventory.

The adapters are created through the existing
`create_vault_instance()` dispatch in `eth_defi.erc_4626.classification`, so the
rest of the scanner does not need a separate GMX-only execution path.

## Historical observation reader

[`historical_oracle.py`](historical_oracle.py) reads two GMX EventEmitter event
types through Hypersync:

| Product | Event | Value field | Supply field |
|---------|-------|-------------|--------------|
| GM | `MarketPoolValueInfo` | `poolValue` | `marketTokensSupply` |
| GLV | `GlvValueUpdated` | `value` | `supply` |

The Hypersync query filters by EventEmitter address, event-name topic and,
where supplied, the selected product-address topics. It fetches block
timestamps with the logs, decodes only the fields needed for valuation, rejects
non-positive value or supply pairs, and sorts observations by block and log
index.

This reader does not replay GMX's signed price oracle and does not reconstruct
historical GMX state with archive RPC calls. The emitted value-and-supply pair
is the source observation.

## Historical context store

[`historical_context.py`](historical_context.py) stores source observations in:

```text
$PIPELINE_DATA_DIR/vault-historical-context.duckdb
└── gmx_historical_context
```

The DuckDB file may be shared by multiple protocol readers, but each protocol
owns its own table. The minimal GMX table contains:

| Column | Purpose |
|--------|---------|
| `chain_id` | Arbitrum or Avalanche chain ID |
| `sample_block_number` | Block containing the GMX value event |
| `valuation_context` | Constant `lp_share_price` context |
| `source_observation_id` | Stable block, transaction and log identifier |
| `token_coverage_hash` | Product-address lookup key |
| `payload_hash` | Integrity check for immutable source data |
| `schema_version` | Stored payload version |
| `context_json` | Timestamp, product, event, value and supply fields |

The compound primary key makes retries idempotent. Re-inserting the same source
observation is ignored; finding the same key with a different payload raises an
error instead of silently changing history.

Large backfills are split into half-open Hypersync chunks and each chunk is
committed independently. A failed backfill can therefore resume from a later
`OBSERVATION_START_BLOCK` without rebuilding the earlier cache.

## Contextual historical reader

`GMXHistoricalReader` implements the normal `VaultHistoricalReader` interface
but sets `uses_contextual_history` to `True`. It does not construct Multicall
requests. Instead it:

1. selects the product's observations from the GMX DuckDB table;
2. keeps the last event in each common block-frequency bucket;
3. converts each observation to `VaultHistoricalRead` with USD total assets,
   token supply and the derived share price;
4. yields those rows to the common historical writer.

Contextual readers are processed separately from static Multicall readers, but
both produce the same row type. This small extension to
`VaultHistoricalReader` is the only generic reader abstraction GMX needs.

## Scheduled pipeline integration

GMX participates in both phases of
[`scan-vaults-all-chains.py`](../../scripts/erc-4626/scan-vaults-all-chains.py).

During `scan_vaults_for_chain()`:

1. ordinary EVM vault lead discovery runs;
2. on Arbitrum and Avalanche, the current GM/GLV catalogue is fetched;
3. catalogue rows are merged into the common metadata database.

During `scan_prices_for_chain()`:

1. all eligible chain vault rows, including GMX rows, are instantiated;
2. if GMX rows exist, their value-and-supply events are prefetched into the
   context DuckDB;
3. one resolved block is used as the exclusive end for both prefill and price
   scanning, preventing a gap between the two operations;
4. `scan_historical_prices_to_parquet()` processes every instantiated EVM vault
   in one call;
5. static readers use the existing Multicall path and GMX uses its contextual
   reader path;
6. all accepted rows are merged into the common raw Parquet file.

For a scheduled incremental scan, the GMX prefill begins at the existing
per-chain price-reader position. If no reader state exists, it uses
`GMX_INITIAL_CONTEXT_LOOKBACK_BLOCKS`, which defaults to 100,000 blocks. There
is no separate GMX scheduled cursor. A full historical import is performed by
the explicit backfill script instead of making every scheduled scan start from
deployment.

## Sparse storage and metrics

GMX source events are naturally sparse. The context reader first reduces them
to the requested `1h` or `1d` block buckets. The common writer then retains:

- the first observation in a scan cycle; and
- later observations whose share-price change exceeds
  `DEFAULT_HISTORICAL_SHARE_PRICE_CHANGE_THRESHOLD`, currently 0.1%.

Because GMX rows carry `share_price_equivalence`, changes in total assets or
total supply alone do not defeat this filter. This prevents deposits and
withdrawals from creating unnecessary Parquet rows.

The resulting Parquet series remains sparse. Before calculating CAGR,
volatility, Sharpe ratios and period metrics, the common metrics code prepares
one consecutive daily share-price series per vault and reuses it for all
calculations. It does not treat irregular event intervals as daily returns.

## Manual migration and examination

The manual scripts are intended for initial migration, bounded repairs and
local verification:

| Script | Purpose |
|--------|---------|
| [`seed-gmx-vaults.py`](../../scripts/erc-4626/seed-gmx-vaults.py) | Enumerate current GM/GLV products into the common metadata database |
| [`backfill-gmx-vault-prices.py`](../../scripts/erc-4626/backfill-gmx-vault-prices.py) | Prefill a bounded context range and run the common Parquet writer without modifying production reader state |
| [`examine-gmx-vault-backfill.py`](../../scripts/erc-4626/examine-gmx-vault-backfill.py) | Check duplicates, positive values, source linkage, asset identity and sparse-threshold behaviour |
| [`examine-gmx-vault-performance.py`](../../scripts/erc-4626/examine-gmx-vault-performance.py) | Run common lifetime metrics and display TVL, lifetime CAGR, three-month CAGR and three-month Sharpe |

The backfill uses half-open `[START_BLOCK, END_BLOCK)` ranges and limits
Parquet replacement to the selected GMX addresses and block interval. It does
not read or write the scheduled reader-state pickle. Repeating the same range
is safe.

See the [operator documentation](../../scripts/erc-4626/README-vault-scripts.md#gmx-v2-liquidity-provider-vaults)
for complete commands and environment variables.

## Test coverage

Focused tests live under [`tests/gmx`](../../tests/gmx):

- catalogue pagination and product metadata;
- catalogue upsert behaviour and adapter dispatch;
- GM and GLV event decoding and supply-normalised pricing;
- DuckDB idempotence, payload integrity and bucket selection;
- sparse reader behaviour and transaction-interface limitations;
- an integration test covering GMX event ingestion, DuckDB, the common Parquet
  writer and `calculate_lifetime_metrics()`.

## Limitations

- Only GMX V2 GM and GLV on Arbitrum One and Avalanche are supported.
- GMX V1 GLP is not supported.
- The adapter is read-only and does not construct deposit or withdrawal
  transactions.
- The historical curve is event observed rather than a continuous GMX NAV
  oracle replay.
- Synthetic USD is the comparison denomination; it is not an ERC-20 token held
  by the adapter.
- Reported performance is a pool-share approximation and not the realised
  return of an individual deposit request.
