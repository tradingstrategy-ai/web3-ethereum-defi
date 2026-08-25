# Rysk Premium vaults

This package integrates Rysk Premium option-writing pools with the common
vault analytics pipeline. Rysk Premium pools issue ERC-20 LP shares, but they
are neither ERC-4626 vaults nor legally structured tokenised funds.

## What is Rysk

[Rysk](https://docs.rysk.finance/) is an onchain options protocol. Option
writers receive premium and provide full collateral: cash for puts and the
underlying asset for calls. The official
[protocol overview](https://docs.rysk.finance/getting-started/protocol-and-product/how-it-works)
describes the option lifecycle.

Rysk Premium packages option writing into curator-managed liquidity pools. A
curator chooses the options the pool may write, while liquidity providers
supply collateral and receive pool shares. The
[Rysk Premium explainer](https://docs.rysk.finance/rysk-premium/rysk-premium-explainer)
is the canonical description of epochs, valuation, withdrawals, fees and
risks.

## What are Rysk vaults

A Rysk Premium pool is both the collateral-accounting contract and its ERC-20
LP share token. It queues deposits and withdrawal requests, allocates
collateral to options, and settles accounting at discrete epoch boundaries.

The scanner does not need a registry or application API to find a pool. It
streams the pool-specific
`EpochPriceSet(uint256,uint256,uint256)` event and confirms candidates through the
`collateralAllocated()` and `collateralAsset()` contract methods. This covers
Ethereum and HyperEVM without maintaining an address allowlist.

The [public pools endpoint](https://premium.rysk.finance/api/pools) is still
used by the manual examination script to enumerate the products currently
shown in the application. It is not a source of scanner metadata, migration
scope or historical prices. Rysk also publishes code through its
[GitHub organisation](https://github.com/rysk-finance).

A representative Ethereum deployment is the
[KPK WETH put pool](https://etherscan.io/address/0x1195826418541cb3e80a22ef5736a6794393c91a),
whose verified proxy implementation exposes the discovery, accounting and
epoch events used by this integration. Addresses are examples, not a static
statement of complete coverage.

## How Rysk vaults differ from ERC-4626 vaults

| Behaviour | ERC-4626 vault | Rysk Premium pool |
|---|---|---|
| Entry | `deposit()` or `mint()` normally returns shares at the current conversion rate | Collateral is queued and shares are minted when an epoch executes |
| Exit | `withdraw()` or `redeem()` normally settles synchronously | A user initiates a withdrawal and completes it after epoch processing |
| Pricing | `convertToAssets()` and `totalAssets()` expose standardised conversion inputs | Deposit and withdrawal prices are proposed for an epoch and become final only when that epoch executes |
| Price timing | Usually readable at arbitrary blocks | Sparse, discrete finalisation events |
| Valuation | Often derivable from onchain assets and share supply | Epoch NAV includes a mark for open option liabilities |
| Events | Standard ERC-4626 `Deposit` and `Withdraw` | Protocol-specific deposit, withdrawal and epoch events |

The contract's `getTVL()` returns free plus allocated collateral. It is useful
for reporting pool size, but it is not full marked option-book NAV. Dividing
this value by share supply would therefore manufacture an incorrect share
price.

## What users should expect

Liquidity providers should expect discrete pricing, queued entry, a multi-step
exit, option losses as well as premium income, and curator and valuation risk.
An epoch price may be disputed before execution, so a price proposal is not a
final observation.

Users of this Python integration should expect:

- A sparse collateral-denominated source curve with at most one point per
  finalised epoch whose proposal is inside the discovery range. The common
  writer may omit a consecutive epoch when its price is economically unchanged;
  this is not a continuously marked options portfolio.
- The final withdrawal price as the share-price equivalent. The deposit price
  is retained for audit but is not used as an exit-value curve.
- `total_assets` and `total_supply` to remain empty on historical observations;
  the integration will not mislabel collateral-only TVL as NAV.
- Read-only vault support. Generic deposit, redemption and investor-flow
  adapters raise `NotImplementedError` because Rysk uses a queued lifecycle.
- No `tokenised_fund` flag. Rysk Premium is a DeFi protocol and does not have
  the legal fund structure that flag represents.

## Share-price equivalent

The equity curve uses the redemption price made final by epoch execution:

```text
share_price_equivalent = final withdrawalPps / 10**collateral_token.decimals
```

`EpochPriceSet` proposes `depositPps` and `withdrawalPps` for an epoch.
`EpochPriceDisputed` can replace that proposal during the dispute window.
`epochExecuted(newEpoch)` is the finalisation boundary for `newEpoch - 1`.
The collector therefore chooses the latest price update for that epoch that
precedes its execution event, and records the execution block and timestamp.

This is an exit-value curve in the pool's collateral token. It is not a USD
curve unless that collateral itself tracks USD, and it does not include an
intraday mark between epoch executions. The common post-processing pipeline
may forward-fill sparse observations for daily analytics; it does not invent
new source prices.

## How the pipeline handles Rysk

The scheduled EVM pipeline follows the same discovery path as other vaults:

1. Hypersync streams the Rysk `EpochPriceSet` lead event on Ethereum and
   HyperEVM. This protocol-specific topic avoids unrelated MasterChef deposit
   events and places discovery at the start of a finalisation sequence.
2. The common multicall classifier confirms the Rysk accounting surface and
   assigns `rysk_premium_like` plus `share_price_equivalence`.
3. `RyskVault` reads the LP token identity and `collateralAsset()` onchain.
4. Before a price scan, the Rysk context collector streams `EpochPriceSet`,
   `EpochPriceDisputed` and `epochExecuted` through Hypersync.
5. Execution timestamps are resolved through the shared cache under
   `~/.tradingstrategy/block-timestamp`, then `RyskHistoricalContextStore`
   persists only reconstructed final executions in the shared
   `vault-historical-context.duckdb` file.
6. `RyskPremiumHistoricalReader` scales the final withdrawal price and emits
   sparse `VaultHistoricalRead` rows to the common Parquet writer.

No JSON-RPC `eth_getLogs`, application metadata scan, hardcoded pool catalogue
or application snapshot feed is used by the scheduled path. Operational test
pools whose onchain names begin with `Rysk Internal` are rejected by the
classifier.

## Manual backfill and examination

[`migrate-rysk-vaults.py`](../../../../scripts/erc-4626/migrate-rysk-vaults.py)
contains both functions of the one-off migration for the eight public pools
reviewed on 2026-08-25. It first repairs the common metadata pickle using fixed
Ethereum and HyperEVM addresses and archive-verified deployment blocks,
rebuilding each row through the normal onchain adapter. It then reconstructs
finalised epochs from onchain events using the same fixed scope. A new context
begins at each pool's reviewed deployment block; an existing context resumes
from its stored per-pool boundary.

The single entry point defaults to `DRY_RUN=true`, which performs real reads
for both functions using temporary storage and makes no persistent changes.
`DRY_RUN=false` is its only migration-specific operator choice. The persistent
run holds one shared writer lock while updating the selected metadata and
address-scoped historical rows; it does not alter reader state. Adding the
script to the repository does not execute the production migration.

[`examine-rysk-vault-performance.py`](../../../../scripts/erc-4626/examine-rysk-vault-performance.py)
reports each current public product's name, chain, collateral-only reported
TVL, latest final price and collateral-denominated CAGR. CAGR is omitted when
there are fewer than two final epochs or less than three days of history.

The scheduled integration lives in
[`scan_all_chains.py`](../../../vault/scan_all_chains.py). The Rysk collector
runs only for metadata rows already classified as Rysk. Each pool begins at
its own discovery block, then resumes by replaying its last stored execution
block. Already stored execution logs are ignored during that replay, while a
later next-epoch price update in the same block remains visible.

## Python APIs

- [`RyskVault`](vault.py) provides the read-only common vault adapter.
- [`get_rysk_premium_discovery_events()`](../../discovery_base.py) defines the
  lead event used by the common scanner.
- [`fetch_rysk_finalised_epoch_prices()`](historical_context.py) reconstructs
  final prices from onchain events.
- [`RyskHistoricalContextStore`](historical_context.py) persists final epoch
  provenance and scales prices using collateral precision.
- [`RyskPremiumHistoricalReader`](historical.py) exposes stored observations
  through the common historical-reader API.
- [`fetch_rysk_premium_pools()`](api.py) reads the application catalogue for
  examination tools only.
- [`migration.py`](migration.py) fixes the reviewed one-off migration scope;
  it is not imported by scheduled discovery.

The shared adapter surface is defined by
[`VaultBase`](../../../vault/base.py), while classification and construction
are wired through [`classification.py`](../../classification.py). The Sphinx
API entry is under
[`docs/source/api/rysk`](../../../../docs/source/api/rysk/index.rst).

## Application API stability

The pools endpoint is an unauthenticated application endpoint rather than a
versioned developer API. `api.py` validates its response and raises
`RyskPremiumAPIError` for transport or schema failures. Such a failure can
affect the manual operator scripts, but not scheduled onchain discovery or
pricing.

The focused live endpoint check is opt-in so ordinary unit tests do not depend
on application availability:

```shell
source .local-test.env && RUN_RYSK_API_INTEGRATION=true poetry run pytest tests/erc_4626/vault_protocol/test_rysk_api_integration.py -q
```
