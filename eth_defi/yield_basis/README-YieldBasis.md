# YieldBasis liquidity-provider vaults

This document describes how `eth_defi` represents YieldBasis Earn LT shares as
vault-like investments and integrates their historical performance with the
common vault dataset.

The first integration supports four reviewed transferable yb-LP products on
Ethereum: WBTC, cbBTC, tBTC and WETH. Gauge positions, YB incentives, legacy
markets and Hybrid Vaults are outside this scope.

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
account. A WBTC, cbBTC or tBTC product can fall when its BTC asset falls in USD,
and the WETH product can fall when ETH falls in USD. See the
[mechanism overview](https://docs.yieldbasis.com/user/overview/how-yieldbasis-works)
for the protocol's description.

Three positions must not be confused:

- **yb-LP/LT base share:** the market share tracked by this integration. YieldBasis
  publishes fundamental native-asset value through `LT.pricePerShare()`; the
  primary chart instead uses the marginal value returned by `preview_withdraw()`.
- **Staked yb-LP:** a gauge position with separate staking and incentive
  economics. The context table records how much effective LT supply is staked,
  but it does not add gauge-token or YB returns to the base-share curve.
- **Hybrid Vault:** a user-specific portfolio mechanism rather than the shared
  LT market share. It is not represented by these vault rows.

## Supported products and naming

The Factory is append-only and also contains older markets, so automatic
publication uses a reviewed address allow-list. The supported set is:

| Market | Asset | LT/yb-LP address | Display name |
|-------:|-------|------------------|--------------|
| 7 | WBTC | `0x651D4b8168488FA163D85304662E8278d4c55BAa` | `yb-LP WBTC` |
| 8 | cbBTC | `0x722FC3640BA007C3E9867CCdB0dCa59F2e2F29F9` | `yb-LP cbBTC` |
| 9 | tBTC | `0x771F7290428d830ECd41E980745c327e507823Ec` | `yb-LP tBTC` |
| 10 | WETH | `0x2B9c9f3BdcEb5d8E36a4704F08a78Fca53343cEa` | `yb-LP WETH` |

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
BTC or ETH liquidity position whose protocol plumbing includes borrowed crvUSD.

The primary performance curve therefore includes both redemption conditions
and the underlying market move:

```text
fundamental underlying PPS ──▶ apply TRD ──▶ redemption value ──┐
                                                                ├── multiply ──▶ USD share price
BTC or ETH USD proxy price ──────────────────────────────────────┘
```

This is the gross product-value curve. The LT receives its volatile underlying
asset, so a generic USD-stablecoin comparison needs one conversion on entry and
one on exit. The baseline assumes a fixed 10-basis-point cost for each leg:

```text
USD stablecoin ── 10 bps ──▶ WBTC/cbBTC/tBTC/WETH ──▶ yb-LP
yb-LP ── preview_withdraw + TRD ──▶ asset ── 10 bps ──▶ USD stablecoin

1 + net USD return
    = (1 - 0.001)
      × redemption USD share price at exit
        / fundamental USD PPS at entry
      × (1 - 0.001)
```

This is an accounting assumption, not an executable YieldBasis quote. It is
modelled by a Python function of the underlying token address so later evidence
can support token-specific values. The current implementation returns 10 bps
for every reviewed token and excludes price impact, gas and MEV. Fundamental
entry PPS is used because a new deposit mints shares without receiving the
current TRD; the exit value includes TRD through `preview_withdraw()`.

This distinction matters when reading headline results. If native PPS does not
change and BTC rises 20% in USD, the WBTC product's USD share price
also rises approximately 20%. It must not report zero merely because the LT did
not gain additional WBTC.

The three endpoint factors compound rather than add:

```text
1 + USD return
    = fundamental PPS end / start
      × (1 + TRD end) / (1 + TRD start)
      × asset/USD proxy price end / start
```

| Underlying LT PPS return | TRD at start → end | Asset/USD return | Combined USD return |
|----------------------|--------------------|----------------------|------------------------|
| 0% | 0% → 0% | +20% | +20% |
| +5% | 0% → 0% | 0% | +5% |
| +5% | 0% → -4% | +20% | about +21% |
| +5% | -4% → 0% | -20% | about -12% |

The native component is useful for separating protocol and liquidity-position
effects from broad BTC or ETH movement. It must still not be labelled
guaranteed yield, fee APR or alpha: `pricePerShare()` can reflect trading
economics, rebalancing and other protocol accounting effects.

Other material risks remain:

- WBTC, cbBTC and tBTC can move away from the value of Bitcoin;
- leverage, rebalancing and oracle behaviour can change the LT result;
- Curve pool depth and balance affect executable liquidity;
- the primary curve includes a marginal Temporary Redemption Discount, which
  can change materially over time and with exit size;
- smart-contract, governance, underlying-token and Ethereum execution risks
  remain.

The common `low` technical protocol-risk classification concerns reviewed
contract transparency and maturity. It does not classify an LT investment as
low-volatility, liquid or principal-protected.

## What the performance curves represent

YieldBasis LTs are ERC-20 shares, not ERC-4626 vaults. The integration derives
the common USD share price from a marginal redemption preview and oracle read
at the same Ethereum block:

```text
preview LT shares           = min(1 whole LT, effective supply)
redemption asset per share  = preview_withdraw(preview LT shares)
                              / underlying decimal scale
                              / (preview LT shares / 10^18)
asset/USD proxy price        = CurvePool.price_oracle() / 10^18
USD share price              = redemption asset per share × asset/USD proxy price

effective supply             = LT.updated_balances().supply_tokens / 10^18
total assets equivalent      = USD share price × effective supply
```

The share price and TVL above are gross of the investor's endpoint conversions.
This is intentional: an entry or exit cost belongs once at an investment
period's endpoint, not at every point in an equity curve. The standard vault
fee fields expose the fixed 10-bps assumption as both deposit and withdrawal
cost. The YieldBasis examiner also changes the entry basis from redemption
value to fundamental PPS, matching how newly deposited shares mint.

The common vault row receives:

```text
share_price  = USD share price
total_supply = effective LT supply
total_assets = marginal redemption-value-equivalent equity in USD
```

The primary share price is equivalently fundamental USD PPS multiplied by
`1 + TRD`. TRD is therefore already included and must not be deducted again.
Applying a one-share marginal preview to all effective supply creates a useful
comparable TVL; it does not claim the whole market can liquidate at that price.

The adapter uses synthetic USD as its denomination because the investor
comparison is not tied to one stablecoin ERC-20. crvUSD remains an internal
YieldBasis and Curve component, and the pre-scan still cross-checks its address
and coin order. The raw context field retains the name
`raw_asset_crvusd_price` to describe its actual onchain source.

The Curve `price_oracle()` is a smoothed asset/crvUSD oracle, not a trade quote.
It gives one consistent historical conversion direction after the pre-scan has
verified that Curve coin 0 is crvUSD and coin 1 is the volatile asset. It can
lag a fast market move.

## USD stablecoin entry and exit cost

The official LT interface takes `assets` and `debt`; it pulls the volatile
asset from the user while LEVAMM supplies the crvUSD debt side. The integration
does not prescribe a particular external stablecoin venue or route. Instead it
uses a simple comparison baseline for converting a generic USD stablecoin to
the LT's underlying token and back.

The integration therefore:

1. passes the reviewed underlying token address to
   `estimate_usd_stablecoin_swap_cost()`;
2. currently receives the same fixed 10-bps value for every token;
3. publishes that value as both `deposit` and `withdraw` fee;
4. keeps the historical product share price gross of these endpoint costs; and
5. applies one entry and one exit cost only when calculating net USD return.

This split prevents an execution assumption from becoming recurring product
performance. The equity curve captures what changes with protocol and market
state, including TRD; the fixed cost belongs only at the investor's entry and
exit. The estimate excludes price impact, gas and MEV.

## Fundamental and redemption value

`LT.pricePerShare()` is fundamental accounting value. It is not a promise that
one LT can be redeemed immediately for that amount. YieldBasis documents the
difference as
[fundamental value, redemption value and TRD](https://docs.yieldbasis.com/user/protocol/fundamental-value-redemption-value-and-trd).
New deposits mint shares at fundamental value, so TRD does not discount entry.
Withdrawals execute at redemption value, which means a negative TRD can reduce
an immediate exit. The historical curve consistently uses redemption value so
it does not present the books-only fundamental PPS as realisable proceeds.

For each historical observation, the integration asks `preview_withdraw()`
about up to one LT share:

```text
redemption asset per share
    = previewed raw asset / asset decimal scale
      ÷ previewed LT / 10^18

Temporary Redemption Discount (TRD)
    = redemption asset per share / fundamental native PPS - 1
```

A negative TRD means the preview is below fundamental value. The context row
stores both bases so reports can explain the gap. If the Curve pool
deterministically rejects a historical preview, that sample is logged and
omitted. Transient provider errors still abort the manual bounded backfill so a
rerun can resume after completed batches. The reader never substitutes
fundamental PPS because mixing bases would create a misleading equity curve.

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
            ├──▶ redemption-basis gross USD CAGR, volatility and drawdown
            └──▶ examiner: mint-to-redemption net USD CAGR, underlying CAGR, cost and TRD
```

The DuckDB file is an input cache, not a second public vault dataset. Raw
fundamental, preview, precision and oracle observations stay protocol-specific
in DuckDB; derived redemption-value USD rows use the common Parquet schema
and downstream metric code.

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
The sync also writes the synthetic USD denomination, underlying identity, public note,
strategy tags, Curve and AMM addresses, kill state and deposit availability.

## Vault adapter

[`vault.py`](vault.py) contains `YieldBasisVault`, which implements `VaultBase`
directly. It deliberately does not pretend that an LT is ERC-4626.

The adapter provides:

- LT and underlying ERC-20 metadata;
- the synthetic USD accounting denomination;
- current marginal redemption USD share price, supply and total-assets
  equivalent, plus fundamental-value diagnostics;
- the fixed 10-bps generic stablecoin conversion estimate as both entry and
  exit cost, without price impact;
- the contextual historical reader;
- market-making and liquidity-provision flags and strategy tags; and
- the direct YieldBasis Earn link.

Protocol fees are classified as `internalised_minting`. YieldBasis can allocate
fees inside LT accounting, so fundamental PPS—and the redemption preview
derived from it—already reflects the allocation borne by existing holders.
The adapter returns no invented fixed management or performance percentage.
Protocol fees and TRD must not be deducted again downstream.

## Deposits and withdrawals

YieldBasis deposits and withdrawals use protocol-specific leveraged-pool
operations. They are not generic ERC-4626 calls. This first adapter is
read-only and intentionally does not expose the generic deposit manager, flow
manager or reconstructed spot portfolio.

For the generic USD comparison, the modelled path is
`USD stablecoin → market asset → LT` on entry and
`LT → market asset → USD stablecoin` on exit. The first and last arrows each
carry the fixed 10-bps assumption. Supplying the volatile asset directly avoids
the entry conversion, but that is not the basis of the USD net-return
comparison.

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
| `asset_decimals` | Precision needed to interpret raw redemption assets without an external token lookup |
| `raw_asset_price_per_share` | Native BTC or ETH fundamental LT PPS |
| `raw_asset_crvusd_price` | Curve asset/crvUSD oracle |
| `raw_effective_supply` / `raw_staked_supply` | Whole-market LT supply context |
| `raw_preview_shares` / `raw_redemption_assets` | Marginal redemption preview used by the primary price |

Raw uint256 values are stored as decimal strings so they do not overflow a
signed database integer or lose precision through floating-point conversion.
The logical identity is chain, lowercase LT address and block number.

The table deliberately has no DuckDB `PRIMARY KEY` or `UNIQUE` constraint.
Batches use an unconstrained temporary table to reject conflicting payloads and
ignore exact duplicates without creating ART indexes. Repeating the same
backfill is therefore safe. Historical reads are committed in bounded batches,
so a later provider failure retains the completed portion for an idempotent
rerun.

The first open after this accounting upgrade fills `asset_decimals` only for
reviewed LT/asset pairs. For the currently reviewed products, it logs and
removes legacy context rows that have no redemption preview, because those rows
cannot reproduce TRD. Rows belonging to products outside the current allow-list
are left untouched. Run the full YieldBasis backfill to reconstruct the removed
inputs and replace the earlier YieldBasis history.

Block timestamps are resolved through the repository's cache-aware Hypersync
timestamp helper. Contract state still comes from the configured Ethereum
archive RPC provider using the configured `MAX_WORKERS` thread limit;
Hypersync is not used to emulate historical EVM state. A deployed LT with zero
effective supply is omitted until it has investment value rather than treated
as a failed observation. Product/block pairs before the reviewed deployment
block are never scheduled, avoiding futile provider retries. A defensive guard
also treats predeployment contract-state errors as unavailable; a
postdeployment RPC error aborts the manual bounded run so it cannot create a
silent hole. During the recurring all-chain scan, a context-prefill error
withholds YieldBasis for that cycle while unrelated Ethereum vaults continue.
A deterministic `preview_withdraw()` contract revert instead creates a logged
missing sample because retrying the same block cannot change its result.

## Contextual historical reader

`YieldBasisHistoricalReader` implements the common `VaultHistoricalReader`
interface with `uses_contextual_history = True`. It does not construct static
Multicall requests itself. Instead it:

1. selects the LT's exact context rows for the requested half-open range;
2. keeps the latest observation in each common block-frequency bucket;
3. derives marginal redemption USD PPS, effective supply and total-assets
   equivalent; and
4. yields ordinary `VaultHistoricalRead` rows to the common writer.

The protocol context remains available to the YieldBasis examiner, which uses
the exact same endpoint blocks for USD and underlying-token CAGR.

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

If context prefill fails, the same cycle withholds every YieldBasis adapter
before invoking the common writer. This prevents a protocol-specific archive
or context error from advancing YieldBasis reader state or stopping unrelated
Ethereum vault pricing.

When the common Ethereum price writer has no saved position, the context range
is bounded by `YIELD_BASIS_INITIAL_CONTEXT_LOOKBACK_BLOCKS`, currently 100,000
blocks. Subsequent cycles follow the existing per-chain price-reader position,
as GMX does. YieldBasis contextual readers do not create private reader-state
entries. The manual backfill supplies complete reviewed history without making
every normal Ethereum cycle start from deployment.

## Sparse storage and metrics

YieldBasis context is sampled on a regular block grid, but the common Parquet
writer remains sparse. It retains the first observation and subsequent rows
whose marginal-redemption USD share-price change exceeds
`DEFAULT_HISTORICAL_SHARE_PRICE_CHANGE_THRESHOLD`, currently 0.1%.

Because the rows carry `share_price_equivalence`, a change in effective supply
or total assets alone does not create a false performance point. Deposits,
withdrawals and staking movements therefore do not masquerade as profit.

The common metrics builder forward fills sparse daily prices. Endpoint return
and CAGR describe the observed change over the selected bounds. Path-dependent
volatility, Sharpe and drawdown remain approximations: an omitted sub-threshold
move is assigned when the next retained observation appears, and the smoothed
Curve oracle can lag the market.

The common gross CAGR follows the sparse TRD-inclusive product-value curve.
Common net metrics apply the standard deposit and withdrawal costs once to
that consistent redemption-basis curve without altering intermediate
observations. The YieldBasis examiner separately reports a depositor's
mint-to-redemption net CAGR, using fundamental PPS at entry and redemption
value at exit.

[`metrics.py`](metrics.py) provides exact `Decimal` helpers for:

- fundamental native-asset PPS;
- marginal redemption underlying-asset and USD PPS;
- fundamental native-asset endpoint return;
- redemption asset per LT and TRD;
- fixed token-based stablecoin cost and fee-adjusted USD endpoint return; and
- effective staked-supply ratio.

These helpers do not change the common Parquet schema.

## Manual migration and examination

The manual scripts cover initial metadata, complete historical import and
read-only verification:

| Script | Purpose |
|--------|---------|
| [`migrate-yield-basis-vaults-metadata.py`](../../scripts/erc-4626/migrate-yield-basis-vaults-metadata.py) | Validate and idempotently reconcile the four reviewed metadata rows |
| [`backfill-yield-basis-vault-prices.py`](../../scripts/erc-4626/backfill-yield-basis-vault-prices.py) | Prefill complete hourly context and run the common address-scoped Parquet writer without modifying reader state |
| [`examine-yield-basis-vault-backfill.py`](../../scripts/erc-4626/examine-yield-basis-vault-backfill.py) | Check scope, duplicates, positive values, exact source linkage, the gross redemption share-price formula and the total-assets identity |
| [`examine-yield-basis-performance.py`](../../scripts/erc-4626/examine-yield-basis-performance.py) | Display redemption-basis gross USD CAGR, mint-to-redemption net USD CAGR, fundamental underlying-token CAGR and the fixed conversion/round-trip cost on identical blocks, plus TVL, TRD and staked ratio |

Both mutating scripts default to `DRY_RUN=true`. The backfill writes an
inspectable temporary copy, scans from the earliest reviewed market launch to
one snapshotted safe head, and limits Parquet replacement to the four reviewed
LT addresses. It hashes the scheduled reader-state pickle before and after the
writer and aborts if it changes. Dry and persistent runs both reuse the
canonical dense block-timestamp cache; `TIMESTAMP_CACHE` can select an
equivalent prepared cache without forcing sparse Hypersync reconstruction.

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
- DuckDB migration, idempotence, conflict rejection, uint256 preservation,
  bucketing and common-writer consumption of contextual rows;
- predeployment skips, deterministic preview gaps and postdeployment
  provider-error propagation;
- USD/underlying return decomposition, redemption value, fixed entry/exit
  costs, TRD and staking ratio;
  and
- public protocol metadata and logo export.

`test_yield_basis_integration.py` is the minimal real-provider check. It
exercises Factory, LT, Curve and AMM reads and complete 8-decimal WBTC and
18-decimal WETH valuations at one safe head. The metadata migration's default
dry run additionally checks ERC-20 metadata without changing the metadata
database.

## Limitations

- Only Ethereum markets 7–10 in the reviewed address registry are supported.
- Legacy YieldBasis markets are not backfilled or automatically published.
- Staked gauge positions, YB incentives and Hybrid Vaults are excluded.
- The adapter is read-only and does not construct deposits or withdrawals.
- Generic investor-flow reconstruction and spot-portfolio decomposition are
  unavailable.
- Primary performance uses a synthetic USD accounting denomination; it does
  not designate a particular stablecoin as the required settlement token.
- The primary curve uses a one-share marginal preview. It is not a whole-market
  liquidation quote. A deterministic preview revert leaves a logged sample
  gap; provider failures abort a manual backfill for retry.
- Net USD CAGR uses fundamental PPS at entry, redemption value at exit and the
  assumed 10-bps generic stablecoin conversion once at each endpoint. It
  excludes price impact, gas and MEV.
- The Curve oracle is smoothed and can lag a fast BTC or ETH move.
- Historical state sampling requires an archive-capable Ethereum provider.
- Sparse, forward-filled daily path metrics are approximations rather than a
  continuously sampled executable NAV.
