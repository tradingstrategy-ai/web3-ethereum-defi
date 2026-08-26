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
and a short token, such as WETH and USDC. Some markets are single-token pools:
the same asset serves as both the long and short backing token.

A GLV token represents a pro-rata claim on liquidity allocated across multiple
compatible GM markets. GLV is therefore a multi-market liquidity-provider
product rather than a single GM market.

GM and GLV shares are ERC-20 tokens, but they are not ERC-4626 vaults. They do
not expose the standard ERC-4626 share-price methods used by most EVM vaults,
and deposits and withdrawals are asynchronous GMX ExchangeRouter requests.

## History and naming of GMX vaults

GMX V1 used the single GLP liquidity pool. GMX V2 replaced that shared model
with individual, risk-isolated GM pools: a liquidity provider in one market is
exposed to the fees and trader profit and loss of that market, but not to
trading activity in other markets. GMX then expanded V2 from its initial major
markets to synthetic markets and additional index assets. This is why the
catalogue contains many separate GM share tokens that use the same backing
assets. See the [GMX V2 go-to-market plan](https://gov.gmx.io/t/gmx-v2-go-to-market/2474)
and GMX's liquidity documentation linked above.

The display pair in a GM vault name is the long and short backing-token pair,
not necessarily the asset on which traders take positions. A perpetual GM market has
three independent components:

- an index price feed that determines the traded market;
- a long token that backs long-position settlements; and
- a short token that backs short-position settlements.

When a GM pool uses the same asset for both backing roles, its description
reports one shared “Long and short backing token” rather than falsely implying
that the pool contains two different assets.

For example, `ETH/USD [WETH-USDC]` uses the ETH/USD index price feed while
WETH and USDC back the pool. A synthetic DOGE/USD market can also use
WETH-USDC as its backing pair. These are distinct pools with separate market
tokens, open interest, trader profit and loss, fees and performance curves;
they must not be deduplicated merely because their backing pair matches.

GM names use the compact format `GM {index} [{long}-{short}]`, for example
`GM ETH [WETH-USDC]` and `GM DOGE [WETH-USDC]`. A short suffix from the GM
share-token address is added only if the complete compact label still
collides. A swap-only GM pool has no index market and is instead named
`GM swap [{first}-{second}]`; its description explains the two pool-token
roles without incorrectly describing them as long and short backing tokens.
GLV names remain pair-based because a GLV can span several markets; their long
descriptions list every supported index market and both backing-token roles.
For GMX [TradFi markets](https://docs.gmx.io/docs/trading/overview/), including
commodities, equities and equity indices, the description additionally states
that the pool provides synthetic price exposure and does not hold the
referenced real-world asset or financial instrument.

## Vaults and volatility risk

A single-sided USDC deposit describes the entry asset, not the investment's
continuing exposure. After GM or GLV shares are minted, the depositor owns a
pro-rata claim on the whole pool. A typical crypto-USDC pool therefore exposes
the depositor to the crypto backing token, trader profit and loss, and pool
fees even though only USDC was supplied.

GMX pools aim to keep equal USD values of their long and short backing tokens.
Before fees and trader profit and loss, a balanced crypto-USDC pool behaves
approximately like a continuously rebalanced 50/50 portfolio. If the crypto
price changes by a factor `r`, its approximate backing return is
`sqrt(r) - 1`. For example:

| Crypto price change | Approximate crypto-USDC pool change |
|---------------------|-------------------------------------|
| +20% | +9.5% |
| -20% | -10.6% |
| +100% | +41.4% |
| -50% | -29.3% |

For small price movements this is roughly half the volatility of the crypto
asset. Actual volatility can be higher or lower because pools need not remain
perfectly balanced and the GM/GLV price also includes trader profit and loss.
When traders make net profits, the pool pays them; trader losses and the
liquidity provider's share of protocol fees increase pool value.

### Simplified BTC-USDC example

Suppose a depositor supplies 10,000 USDC to a balanced BTC-USDC GM pool and
Bitcoin subsequently rises by 20%. Ignoring trader profit and loss, fees and
price impact, the approximate value of the GM shares becomes:

```text
10,000 USD * sqrt(1.20) = 10,954 USD
```

On withdrawal, the depositor can receive the pool's long and short tokens,
approximately 5,477 USD of WBTC and 5,477 USDC in this simplified balanced
example. A configured withdrawal swap path can instead convert the WBTC output
to USDC, producing approximately 10,954 USDC before withdrawal fees, swap fees,
price impact and the keeper execution fee. Converting the USDC leg to WBTC is
also possible when a suitable path is available.

The realised result need not follow this example. If traders are net long BTC,
a BTC price increase makes them profitable and their profit is paid by the
pool, offsetting some or all of the backing-token gain. Net-short trader losses
benefit the pool, while trading and borrowing fees also increase its value.
The withdrawal quote at execution time determines the actual token amounts.

Impermanent loss is only meaningful relative to holding the same backing
tokens without rebalancing. For a balanced crypto-stablecoin pool, the
approximate relative result is `2 * sqrt(r) / (1 + r) - 1`; a doubling or
halving of the crypto price is about a 5.7% loss relative to an unchanged 50/50
holding. Against simply retaining USDC, however, the pool's crypto-related
drawdown is an investment loss rather than merely impermanent loss.

Different products add different risks:

- A fully backed GM market combines backing-token volatility with the profit
  and loss of traders in that market.
- A synthetic GM market can hold, for example, WETH and USDC while backing
  positions in a different index token. It therefore adds index-trader risk
  that is not visible from the backing pair alone.
- A GLV allocates liquidity across multiple compatible GM markets. This
  diversifies trader exposure but adds allocation, shifting and underlying-GM
  liquidity risks; it does not remove the base crypto exposure.
- A stablecoin-only pool has little ordinary price volatility while its tokens
  hold their pegs, but retains issuer, depeg and, for bridged assets, bridge
  risk.

Deposits and withdrawals also face balance-dependent price impact, execution
fees, token spreads and possible capacity limits. Reserved liquidity or high
pending trader profit can temporarily constrain redemptions. Smart-contract,
oracle, keeper, sequencer and governance risks remain in every product. The
common `low_risk` classification describes GMX's technical protocol risk; it
does not classify an individual GM or GLV investment as low-volatility or
capital-stable.

## What the performance curve represents

The integration derives a synthetic USD share-price equivalent from GMX's
onchain value-and-supply events:

```text
total assets (USD) = raw GMX value / 10^30
total supply       = raw token supply / 10^18
share price        = total assets / total supply
```

This ratio measures the USD value attributable to one GM or GLV share. It lets
GMX products use the same equity-curve, return, CAGR, volatility, Sharpe and
drawdown code as vaults that publish a conventional share price.

The vault performance approximates the single-sided USDC deposit value. GMX
vaults hold exposure to all underlying tokens they market make. The catalogue
uses native USDC as its display denomination for comparison, but this does not
mean every pool accepts USDC, is backed solely by USDC, or has an onchain USDC
NAV. Accepted deposit tokens are product-specific, and some products have no
stablecoin side.

This is a comparison convention, not a transaction simulator. The curve does
not model the execution fee, price impact, token spread, deposit or withdrawal
fee, or waiting time faced by one particular depositor. These transaction costs
are also not management or performance fees in the `VaultBase` fee interface.
GMX exposes zero for those two manager-level fee fields.

The curve includes changes that affect the value of all existing shares, such
as pool asset prices, trader profit and loss and liquidity-provider fee
revenue. It remains an approximation: in particular, GMX notes that a GLV
value can omit shift, deposit or withdrawal fees when a GLV oracle price is
used.

## Why deposits and withdrawals do not create false profit

A raw TVL curve is unsuitable for performance analysis: a large deposit raises
TVL and a large withdrawal lowers it even when existing liquidity providers
have made no profit or loss. The GMX reader instead divides matched pool value
and token supply from the same protocol event.

GM and GLV have different source limitations:

- `MarketPoolValueUpdated` contains GM market value and supply after an
  operation. The curve accepts deposit updates only. GMX values deposits and
  withdrawals with different PnL-factor and maximise/minimise settings, so
  alternating the two contexts would create false returns.
- `GlvValueUpdated` contains GLV value and supply after execution. GLV shares
  are minted or burned using the pre-flow ratio, so value and supply change
  proportionally in the absence of costs.

Consequently, deposit size alone does not mechanically rebase the GM equity
curve, and proportional GLV flows do not mechanically rebase the GLV curve.
The source limitations above mean this remains an approximation rather than a
complete accounting of every flow cost.

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
- `amm_pool_like`, identifying both product types as market-making pool shares;
- `share_price_equivalence`, telling the sparse writer that only changes in the
  derived price represent performance.

The row also records component addresses, accepted deposit-token addresses and
the current enabled status. Catalogue updates refresh scanner-owned fields
while preserving unrelated enrichment fields. Disabled products retain their
history, are marked deposit-closed and are excluded from vault rankings.

## Vault adapters

[`vault.py`](vault.py) contains two concrete adapters:

- `GMXMarketVault` for one GM market token;
- `GMXLiquidityVault` for one GLV token.

Both inherit from `GMXVaultBase`, which in turn implements `VaultBase` directly.
They deliberately do not pretend to be ERC-4626 contracts.

Each adapter's `get_link()` returns the direct GMX pool-details page for that
GM or GLV share token, including its deployment chain and the GMX deposit view.

The adapter provides the common read-only metadata surface and declares a
synthetic USD denomination. It also classifies the strategy as market making
and liquidity provision. GMX is classified as low technical protocol risk in
the common risk matrix; this classification does not remove market, oracle or
liquidity risk.

## Deposits and withdrawals

GM and GLV deposits and withdrawals are asynchronous GMX requests, not
ERC-4626 transactions that complete in the caller's transaction. A request is
submitted first, then a keeper executes it with oracle prices. GMX describes
this execution as typically taking a few seconds, but it has no binding
completion deadline. The catalogue reports a one-minute estimated settlement
time as a conservative user-interface estimate for request inclusion, oracle
handling and keeper execution; it is not a promise that a request will settle
within that time.

The catalogue marks a disabled GM or GLV product as closed for deposits.
Enabled is not an unconditional deposit guarantee: pool caps, PnL-factor
limits, available liquidity, oracle availability and price-impact constraints
can still prevent a particular request from executing. Similarly, a redemption
can be delayed or fail when liquidity is reserved for positions or market
limits apply. See GMX's [architecture documentation](https://docs.gmx.io/docs/api/contracts/architecture/),
the liquidity documentation linked above and [known integration issues](https://docs.gmx.io/docs/api/contracts/known-issues/).

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
| GM | Deposit-context `MarketPoolValueUpdated` | `poolValue` | `marketTokensSupply` |
| GLV | `GlvValueUpdated` | `value` | `supply` |

The Hypersync query filters by EventEmitter address, event-name topic and,
where supplied, the selected product-address topics. It fetches block
timestamps with the logs, decodes only the fields needed for valuation, rejects
non-positive value or supply pairs and non-deposit GM updates, and sorts
observations by block and log index.

GMX uses Hypersync's explicit `get()` pagination rather than its native
streaming engine. Dense full-history streams can retain large native response
buffers, so the reader fetches and releases one server page at a time. This is
slower than concurrent streaming but keeps memory bounded in the production
scanner container. Each page is covered by the shared Hypersync request-rate
limiter.

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
| `block_number` / `block_timestamp` | Location and time of the GMX value event |
| `transaction_hash` / `log_index` | Stable source-event identity |
| `product_address` | GM market token or GLV share token |
| `raw_value` / `raw_supply` | GMX value and matching share supply |
| `event_name` | `MarketPoolValueUpdated` or `GlvValueUpdated` |

The application-level identity is chain, transaction hash and log index.
Re-inserting the same source observation is ignored; conflicting values for the
same source event raise an error. The table deliberately has no DuckDB
`PRIMARY KEY` or `UNIQUE` constraint: DuckDB 1.5.0 ART indexes can corrupt the
native heap on large file-backed databases under Python 3.14; see the related
[DuckDB ART issue](https://github.com/duckdb/duckdb/issues/18190). Batches use
an unconstrained temporary table and hash joins for conflict detection and
deduplication. Existing indexed tables and older development caches using the
generic JSON envelope are migrated transactionally when opened; obsolete rows
from the earlier mixed GM valuation context are not copied.

Large backfills are split into half-open Hypersync chunks, paginated within
each chunk, and committed independently. Repeating a complete chain range is
safe because source observations are inserted idempotently. The script always
fetches the full replacement range from block 1 to one snapshotted safe head;
it does not accept an unverified resume cursor that could leave the rebuilt
Parquet interval incomplete.

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

The resulting Parquet series remains sparse. The common metrics builder forward
fills the last observed share price to a daily index. An event-free day is
therefore assigned a zero return and the complete intervening movement is
assigned to the next observed event day. This is an explicitly accepted
approximation that keeps return, CAGR, volatility, Sharpe and drawdown metrics
available through the common vault interface.

Endpoint return and CAGR continue to describe the observed change over the
selected period. Path-dependent metrics, especially volatility and Sharpe, are
observation-cadence-sensitive approximations rather than statistics derived
from a continuously sampled GMX NAV. They must be interpreted with that
limitation when comparing products with different operation frequency.

## Manual migration and examination

The manual scripts are intended for initial migration, complete historical
imports and local verification:

| Script | Purpose |
|--------|---------|
| [`migrate-gmx-vaults-metadata.py`](../../scripts/erc-4626/migrate-gmx-vaults-metadata.py) | Idempotently refresh the GMX fields maintained by the migration: index-aware GM and backing-pair GLV display names, full LP description with index-market and backing-token roles, USDC display denomination, direct GMX links, performance note and deposit availability |
| [`backfill-gmx-vault-prices.py`](../../scripts/erc-4626/backfill-gmx-vault-prices.py) | Prefill complete Arbitrum and Avalanche context ranges and run the common hourly Parquet writer without modifying production reader state |
| [`examine-gmx-vault-backfill.py`](../../scripts/erc-4626/examine-gmx-vault-backfill.py) | Check duplicates, positive values, source linkage, asset identity and sparse-threshold behaviour |
| [`examine-gmx-vault-performance.py`](../../scripts/erc-4626/examine-gmx-vault-performance.py) | Run common lifetime metrics and display TVL, lifetime CAGR, three-month CAGR, approximate three-month volatility and Sharpe |

The backfill requires no chain, block or frequency parameters. It processes
Arbitrum and Avalanche sequentially with hourly buckets, using the half-open
range from block 1 to a separately snapshotted safe head on each chain. It
rewrites all seeded GMX addresses in those ranges without reading or writing
the scheduled reader-state pickle. Repeating the command is safe.

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
- GM deposit observations use one consistent deposit valuation context;
  withdrawal-context observations are excluded.
- GMX volatility and Sharpe use forward-filled daily prices and are
  observation-cadence-sensitive approximations, not continuous NAV metrics.
- Native USDC is the display denomination. GMX source values remain USD-valued
  pool-share observations, not an onchain USDC NAV.
- Reported performance is a pool-share approximation and not the realised
  return of an individual deposit request.
