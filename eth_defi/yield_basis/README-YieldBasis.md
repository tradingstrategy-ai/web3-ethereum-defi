# YieldBasis liquidity-provider vaults

This document describes how `eth_defi` represents YieldBasis Earn LT shares as
vault-like investments and integrates their historical performance with the
common vault dataset.

The first integration supports four reviewed unstaked yb-LP products on
Ethereum: WBTC, cbBTC, tBTC and WETH. Staked positions, YB incentives, legacy
markets and Hybrid Vaults are intentionally outside this scope.

For operator commands, environment variables and production paths, see the
[ERC-4626 vault scripts README](../../scripts/erc-4626/README-vault-scripts.md#yieldbasis-leveraged-lt-markets).

## What YieldBasis Earn products are

[YieldBasis](https://yieldbasis.com/earn) creates leveraged liquidity positions
in Curve Cryptoswap pools. A user supplies a volatile asset such as WBTC or
WETH. The protocol borrows crvUSD and manages both assets in the market's Curve
pool through its LEVAMM mechanism. The user receives an LT token, presented by
the application as yb-LP, representing a share of that managed position.

The design aims to preserve exposure to the supplied BTC or ETH asset while
earning the economics of liquidity provision. It is not a stablecoin savings
account. A WBTC, cbBTC or tBTC product can fall when its BTC asset falls against
crvUSD, and the WETH product can fall when ETH falls against crvUSD. See the
[mechanism overview](https://docs.yieldbasis.com/user/overview/how-yieldbasis-works)
for the protocol's description.

Three positions must not be confused:

- **Unstaked yb-LP/LT:** the market share tracked by this integration. Its
  fundamental native-asset value is published by `LT.pricePerShare()`.
- **Staked yb-LP:** a gauge position with separate staking and incentive
  economics. The context table records how much effective LT supply is staked,
  but it does not add gauge-token or YB returns to the unstaked curve.
- **Hybrid Vault:** a user-specific portfolio mechanism rather than the shared
  LT market share. It is not represented by these vault rows.

## Supported products and naming

The Factory is append-only and also contains older markets, so automatic
publication uses a reviewed address allow-list. The supported set is:

| Market | Asset | LT/yb-LP address | Display name |
|-------:|-------|------------------|--------------|
| 7 | WBTC | `0x651D4b8168488FA163D85304662E8278d4c55BAa` | `yb-LP WBTC · market 7` |
| 8 | cbBTC | `0x722FC3640BA007C3E9867CCdB0dCa59F2e2F29F9` | `yb-LP cbBTC · market 8` |
| 9 | tBTC | `0x771F7290428d830ECd41E980745c327e507823Ec` | `yb-LP tBTC · market 9` |
| 10 | WETH | `0x2B9c9f3BdcEb5d8E36a4704F08a78Fca53343cEa` | `yb-LP WETH · market 10` |

The onchain Factory remains the runtime source for the LT, underlying asset,
Curve pool and YieldBasis AMM links. The allow-list is a publication gate, not
a replacement for current Factory validation. A new or changed market is
withheld until its identity and valuation direction have been reviewed.

The BTC products remain distinct. WBTC, cbBTC and tBTC have different issuers,
custody or bridge designs and can trade at different prices. Their histories
must not be merged merely because all three aim to represent Bitcoin.

## Vaults and volatility risk

The deposit asset describes how a user enters; it does not remove continuing
market exposure. After minting yb-LP, the holder owns a share of a leveraged
BTC/crvUSD or ETH/crvUSD liquidity position.

The primary performance curve therefore includes the underlying market move:

```text
YieldBasis native PPS change ───────┐
                                    ├── multiply ──▶ crvUSD share-price change
BTC or ETH price against crvUSD ────┘
```

This distinction matters when reading headline results. If native PPS does not
change and BTC rises 20% against crvUSD, the WBTC product's crvUSD share price
also rises approximately 20%. It must not report zero merely because the LT did
not gain additional WBTC.

The two components compound rather than add:

```text
1 + crvUSD return
    = (1 + native LT PPS return) × (1 + asset/crvUSD return)
```

| Native LT PPS return | Asset/crvUSD return | Combined crvUSD return |
|----------------------|----------------------|------------------------|
| 0% | +20% | +20% |
| +5% | 0% | +5% |
| +5% | +20% | +26% |
| +5% | -20% | -16% |

The native component is useful for separating protocol and liquidity-position
effects from broad BTC or ETH movement. It must still not be labelled
guaranteed yield, fee APR or alpha: `pricePerShare()` can reflect trading
economics, rebalancing and other protocol accounting effects.

Other material risks remain:

- crvUSD can move away from one US dollar, so a crvUSD return is not always an
  exact dollar return;
- WBTC, cbBTC and tBTC can move away from the value of Bitcoin;
- leverage, rebalancing and oracle behaviour can change the LT result;
- Curve pool depth and balance affect executable liquidity;
- a redemption can be below fundamental value because of a Temporary
  Redemption Discount;
- smart-contract, governance, underlying-token and Ethereum execution risks
  remain.

The common `low` technical protocol-risk classification concerns reviewed
contract transparency and maturity. It does not classify an LT investment as
low-volatility, liquid or principal-protected.

## What the performance curves represent

YieldBasis LTs are ERC-20 shares, not ERC-4626 vaults. The integration derives
the common crvUSD share price from two values read at the same Ethereum block:

```text
native asset PPS    = LT.pricePerShare() / 10^18
asset/crvUSD price  = CurvePool.price_oracle() / 10^18
crvUSD share price  = native asset PPS × asset/crvUSD price

effective supply    = LT.updated_balances().supply_tokens / 10^18
total assets        = crvUSD share price × effective supply
```

The common vault row receives:

```text
share_price  = crvUSD share price
total_supply = effective LT supply
total_assets = fundamental LT equity in crvUSD
```

crvUSD is the adapter's real denomination token, resolved from
`Factory.STABLECOIN()` and cross-checked against the reviewed Ethereum crvUSD
contract. USDC is not substituted as a display token. Consumers may group the
series with USD-stablecoin products, but should label it crvUSD and preserve
the stablecoin-basis caveat.

The Curve `price_oracle()` is a smoothed asset/crvUSD oracle, not a trade quote.
It gives one consistent historical conversion direction after the pre-scan has
verified that Curve coin 0 is crvUSD and coin 1 is the volatile asset. It can
lag a fast market move.

## Fundamental and redemption value

`LT.pricePerShare()` is fundamental accounting value. It is not a promise that
one LT can be redeemed immediately for that amount. YieldBasis documents the
difference as
[fundamental value, redemption value and TRD](https://docs.yieldbasis.com/user/protocol/fundamental-value-redemption-value-and-trd).

For each historical observation, the integration optionally asks
`preview_withdraw()` about up to one LT share:

```text
redemption asset per share
    = previewed raw asset / asset decimal scale
      ÷ previewed LT / 10^18

temporary redemption difference
    = redemption asset per share / fundamental native PPS - 1
```

A negative difference means the preview is below fundamental value. A preview
can legitimately revert, so this diagnostic is nullable. Missing redemption
context does not hide an otherwise valid fundamental price observation.

## Integration architecture

```text
YieldBasis Factory at one fixed head
            │
            ▼
reviewed market pre-scan ───────────────▶ common vault metadata pickle
                                                    │
LT.pricePerShare()                                  │ creates YieldBasisVault
LT.updated_balances()                               ▼
Curve price_oracle()                     YieldBasisHistoricalReader
LT.preview_withdraw()                                 │
            │ archive state calls                     │ contextual reads
            ▼                                         │
yield_basis_historical_context table ─────────────────┘
in vault-historical-context.duckdb
            │
            ▼
common historical price writer
            │
            ▼
vault-prices-1h.parquet
            │
            ▼
common cleaning, daily series and lifetime metrics
            │
            ├──▶ primary crvUSD CAGR, volatility and drawdown
            └──▶ YieldBasis examiner: native-token CAGR and TRD
```

The DuckDB file is an input cache, not a second public vault dataset. Raw
YieldBasis observations stay protocol-specific in DuckDB; derived crvUSD rows
use the common Parquet schema and downstream metric code.

## Catalogue pre-scan

[`vault_catalog.py`](vault_catalog.py) reads the Ethereum Factory directly. It
does not rely on the web application or an offchain product API.

Before a reviewed product can be reconciled, one fixed-block pre-scan checks:

1. Ethereum chain ID, Factory bytecode and `Factory.STABLECOIN()`;
2. `market_count()` and whether unreviewed newer IDs exist;
3. the reviewed asset and LT pair returned by `Factory.markets(id)`;
4. the LT's asset, crvUSD, Curve pool and YieldBasis AMM links;
5. Curve coin order and a positive asset/crvUSD oracle; and
6. the AMM's crvUSD, Curve collateral, LT link and kill switch.

A Factory-wide failure leaves all existing YieldBasis metadata untouched and
does not stop unrelated Ethereum vault discovery. A single changed market is
withheld while other validated reviewed markets continue. A killed product
keeps its historical value but is marked closed for new deposits.

[`vault_sync.py`](vault_sync.py) converts validated products into common vault
rows. Every row carries:

- `yield_basis_lt`, selecting the adapter;
- `amm_pool_like`, identifying an AMM liquidity-provider share; and
- `share_price_equivalence`, making the sparse writer treat price rather than
  TVL or supply movement as performance.

Catalogue-owned fields are refreshed while unrelated enrichment is preserved.
The sync also writes the crvUSD denomination, underlying identity, public note,
strategy tags, Curve and AMM addresses, kill state and deposit availability.

## Vault adapter

[`vault.py`](vault.py) contains `YieldBasisVault`, which implements `VaultBase`
directly. It deliberately does not pretend that an LT is ERC-4626.

The adapter provides:

- LT and underlying ERC-20 metadata;
- the verified crvUSD denomination token;
- current fundamental crvUSD share price, supply and total assets;
- the contextual historical reader;
- market-making and liquidity-provision flags and strategy tags; and
- the direct YieldBasis Earn link.

Protocol fees are classified as `internalised_minting`. YieldBasis can allocate
fees inside LT accounting, so fundamental PPS is already net of the allocation
borne by existing unstaked holders. The adapter returns no invented fixed
management or performance percentage, and downstream code must not deduct an
estimated fee a second time.

## Deposits and withdrawals

YieldBasis deposits and withdrawals use protocol-specific leveraged-pool
operations. They are not generic ERC-4626 calls. This first adapter is
read-only and intentionally does not expose the generic deposit manager, flow
manager or reconstructed spot portfolio.

The current AMM kill switch can establish that deposits are closed. An
un-killed market still returns unknown rather than open because an executable
operation depends on a fresh protocol quote, limits, liquidity and transaction
conditions.

The products are classified as permissionless based on the reviewed protocol
design. The exported whitelist note makes clear that this does not prove a
particular account or quote currently satisfies every runtime condition.

## Historical context store

[`historical_context.py`](historical_context.py) stores exact sampled state in:

```text
$PIPELINE_DATA_DIR/vault-historical-context.duckdb
└── yield_basis_historical_context
```

The table contains:

| Column | Purpose |
|--------|---------|
| `chain_id` | Ethereum chain ID |
| `block_number` / `block_timestamp` | Exact source state and time |
| `lt_address` / `asset_address` | Product and volatile asset identity |
| `raw_asset_price_per_share` | Native BTC or ETH fundamental LT PPS |
| `raw_asset_crvusd_price` | Curve asset/crvUSD oracle |
| `raw_effective_supply` / `raw_staked_supply` | Whole-market LT supply context |
| `raw_preview_shares` / `raw_redemption_assets` | Optional redemption preview |
| `redemption_missing_reason` | Concise reason for a missing preview |

Raw uint256 values are stored as decimal strings so they do not overflow a
signed database integer or lose precision through floating-point conversion.
The logical identity is chain, lowercase LT address and block number.

The table deliberately has no DuckDB `PRIMARY KEY` or `UNIQUE` constraint.
Batches use an unconstrained temporary table to reject conflicting payloads and
ignore exact duplicates without creating ART indexes. Repeating the same
backfill is therefore safe. Historical reads are committed in bounded batches,
so a later provider failure retains the completed portion for an idempotent
rerun.

Block timestamps are resolved through the repository's cache-aware Hypersync
timestamp helper. Contract state still comes from the configured Ethereum
archive RPC provider using the configured `MAX_WORKERS` thread limit;
Hypersync is not used to emulate historical EVM state. A deployed LT with zero
effective supply is omitted until it has investment value rather than treated
as a failed observation. Product/block pairs before the reviewed deployment
block are never scheduled, avoiding futile provider retries. A defensive guard
also treats predeployment contract-state errors as unavailable; a
postdeployment RPC error aborts the bounded run so it cannot create a silent
hole.

## Contextual historical reader

`YieldBasisHistoricalReader` implements the common `VaultHistoricalReader`
interface with `uses_contextual_history = True`. It does not construct static
Multicall requests itself. Instead it:

1. selects the LT's exact context rows for the requested half-open range;
2. keeps the latest observation in each common block-frequency bucket;
3. derives crvUSD PPS, effective supply and total assets; and
4. yields ordinary `VaultHistoricalRead` rows to the common writer.

The protocol context remains available to the YieldBasis examiner, which uses
the exact same endpoint blocks for crvUSD and native-token CAGR.

## Scheduled pipeline integration

YieldBasis participates in both phases of
[`scan-vaults-all-chains.py`](../../scripts/erc-4626/scan-vaults-all-chains.py).

The metadata phase is ordered deliberately:

```text
resolve one safe Ethereum head
            │
            ▼
validate Factory and reviewed markets once
            │
            ▼
inspect generic lead-discovery cache
            │
            ├── cache hit ──▶ merge products once ──▶ finish
            │
            └── cache miss ─▶ run generic discovery
                                      │
                                      ▼
                              merge products once
```

The merge ensures non-ERC-4626 LT products exist even though they do not emit
the generic discovery events. On a cache miss it runs after discovery, so it
also repairs any same-address generic candidate written in that cycle. Each
path performs one metadata reconciliation and reuses the already validated
Factory snapshot.

The price phase separately revalidates the catalogue at its own fixed end
block. Invalid YieldBasis rows are removed from that cycle's in-memory price
selection while unrelated vaults continue. Historical rows before the common
writer's current replacement window remain preserved; rows inside that window
follow the common chain-writer replacement policy. The phase then:

1. samples LT and Curve state on the requested hourly or daily block grid;
2. inserts context observations idempotently;
3. invokes `scan_historical_prices_to_parquet()` once for all selected EVM
   vaults; and
4. records the number of new YieldBasis context rows in cycle metrics.

When the common Ethereum price writer has no saved position, the context range
is bounded by `YIELD_BASIS_INITIAL_CONTEXT_LOOKBACK_BLOCKS`, currently 100,000
blocks. Subsequent cycles follow the existing per-chain price-reader position,
as GMX does. YieldBasis contextual readers do not create private reader-state
entries. The manual backfill supplies complete reviewed history without making
every normal Ethereum cycle start from deployment.

## Sparse storage and metrics

YieldBasis context is sampled on a regular block grid, but the common Parquet
writer remains sparse. It retains the first observation and subsequent rows
whose crvUSD share-price change exceeds
`DEFAULT_HISTORICAL_SHARE_PRICE_CHANGE_THRESHOLD`, currently 0.1%.

Because the rows carry `share_price_equivalence`, a change in effective supply
or total assets alone does not create a false performance point. Deposits,
withdrawals and staking movements therefore do not masquerade as profit.

The common metrics builder forward fills sparse daily prices. Endpoint return
and CAGR describe the observed change over the selected bounds. Path-dependent
volatility, Sharpe and drawdown remain approximations: an omitted sub-threshold
move is assigned when the next retained observation appears, and the smoothed
Curve oracle can lag the market.

[`metrics.py`](metrics.py) provides exact `Decimal` helpers for:

- native-asset and crvUSD PPS;
- native-asset and crvUSD endpoint return;
- redemption asset per LT and temporary redemption difference; and
- effective staked-supply ratio.

These helpers do not change the common Parquet schema.

## Manual migration and examination

The manual scripts cover initial metadata, complete historical import and
read-only verification:

| Script | Purpose |
|--------|---------|
| [`migrate-yield-basis-vaults-metadata.py`](../../scripts/erc-4626/migrate-yield-basis-vaults-metadata.py) | Validate and idempotently reconcile the four reviewed metadata rows |
| [`backfill-yield-basis-vault-prices.py`](../../scripts/erc-4626/backfill-yield-basis-vault-prices.py) | Prefill complete hourly context and run the common address-scoped Parquet writer without modifying reader state |
| [`examine-yield-basis-vault-backfill.py`](../../scripts/erc-4626/examine-yield-basis-vault-backfill.py) | Check scope, duplicates, positive values, exact source linkage and the total-assets identity |
| [`examine-yield-basis-performance.py`](../../scripts/erc-4626/examine-yield-basis-performance.py) | Display crvUSD and native-token lifetime and three-month CAGR on identical endpoints, plus TVL, redemption difference and staked ratio |

Both mutating scripts default to `DRY_RUN=true`. The backfill writes an
inspectable temporary copy, scans from the earliest reviewed market launch to
one snapshotted safe head, and limits Parquet replacement to the four reviewed
LT addresses. It hashes the scheduled reader-state pickle before and after the
writer and aborts if it changes.

Run the structural examiner with `REQUIRE_ALL_PRODUCTS=true` only after a full
backfill. Complete commands and production container instructions are in the
[operator documentation](../../scripts/erc-4626/README-vault-scripts.md#yieldbasis-leveraged-lt-markets).

## Test coverage

Focused tests live under [`tests/yield_basis`](../../tests/yield_basis). They
cover:

- Ethereum-only hardcoded routing and the three persistent feature flags;
- protocol name, activity exemption, fee mode, risk and vault flags;
- exact address-level strategy tags and the missing-address result;
- the permissionless classification and its exported caveat;
- reviewed Factory enumeration and catalogue-sync idempotence;
- zero-supply launch handling and threaded context prefill;
- address-scoped withholding and shared chain-state continuation in the common
  scanner;
- DuckDB idempotence, conflict rejection, uint256 preservation, bucketing and
  common-writer consumption of contextual rows;
- predeployment skips and postdeployment provider-error propagation;
- crvUSD/native return decomposition, redemption difference and staking ratio;
  and
- public protocol metadata and logo export.

`test_yield_basis_integration.py` is the minimal real-provider check. It
exercises Factory, LT, Curve and AMM reads and one complete crvUSD valuation at
one safe head. The metadata migration's default dry run additionally checks
ERC-20 metadata without changing the metadata database.

## Limitations

- Only Ethereum markets 7–10 in the reviewed address registry are supported.
- Legacy YieldBasis markets are not backfilled or automatically published.
- Staked gauge positions, YB incentives and Hybrid Vaults are excluded.
- The adapter is read-only and does not construct deposits or withdrawals.
- Generic investor-flow reconstruction and spot-portfolio decomposition are
  unavailable.
- Primary performance is denominated in crvUSD, not USDC and not an exact US
  dollar measure when crvUSD moves away from its peg.
- Fundamental PPS is not an executable redemption quote; the optional preview
  can be lower or unavailable.
- The Curve oracle is smoothed and can lag a fast BTC or ETH move.
- Historical state sampling requires an archive-capable Ethereum provider.
- Sparse, forward-filled daily path metrics are approximations rather than a
  continuously sampled executable NAV.
