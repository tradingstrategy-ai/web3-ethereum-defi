# YieldBasis vault integration

Date: 2026-08-27

## Goal and scope

Add the four reviewed Ethereum YieldBasis Earn LT markets to the common vault
dataset as read-only `VaultBase` products. Follow the GMX pattern for
non-ERC-4626 liquidity-provider shares:

- enumerate products from the protocol Factory;
- mark them as AMM-like share-price equivalents;
- route them to a bespoke `VaultBase` adapter;
- prefill protocol-owned historical context before the common price scan; and
- use the normal Parquet, cleaning and lifetime-metrics pipeline.

The primary denomination is Ethereum crvUSD. The primary curve must include the
underlying BTC or ETH price move against crvUSD. A protocol-specific native
asset curve must use the same endpoint blocks so readers can distinguish LT PPS
performance from the market move.

The supported unstaked products are fixed for this first implementation:

| Market ID | Underlying | LT/yb-LP share token |
|----------:|------------|----------------------|
| 7 | WBTC | `0x651D4b8168488FA163D85304662E8278d4c55BAa` |
| 8 | cbBTC | `0x722FC3640BA007C3E9867CCdB0dCa59F2e2F29F9` |
| 9 | tBTC | `0x771F7290428d830ECd41E980745c327e507823Ec` |
| 10 | WETH | `0x2B9c9f3BdcEb5d8E36a4704F08a78Fca53343cEa` |

The Factory is `0x370a449FeBb9411c95bf897021377fe0B7D100c0`.
The required denomination token is Ethereum crvUSD at
`0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E`.

Do not include staked gauge positions, YB incentives, Hybrid Vaults, legacy
market performance, deposit transactions or flow reconstruction.

## Product model for a general audience

[YieldBasis](https://yieldbasis.com/earn) accepts a volatile asset such as BTC
or ETH, borrows crvUSD and manages both sides in a Curve Cryptoswap pool through
its LEVAMM mechanism. An LT, displayed as yb-LP, represents a share of that
managed liquidity position.

The public description must be prescriptive about the following points:

- yb-LP is a leveraged liquidity-provider share, not a stablecoin account;
- BTC and ETH price moves remain part of the primary result;
- crvUSD denomination is not an exact US dollar measure during a crvUSD depeg;
- native-asset CAGR is a diagnostic, not guaranteed yield or fee APR;
- fundamental PPS is not an executable redemption quote; and
- a technical protocol-risk label does not imply low investment volatility.

WBTC, cbBTC and tBTC remain separate products because each wrapper has distinct
issuer, custody or bridge risk.

## Accounting decisions

Read all values for one observation at the same Ethereum block. Use named
18-decimal scale constants:

```text
native_asset_pps    = LT.pricePerShare() / PPS_SCALE
asset_crvusd_price  = CurvePool.price_oracle() / ORACLE_SCALE
crvusd_share_price  = native_asset_pps × asset_crvusd_price
effective_supply    = LT.updated_balances().supply_tokens / LT_SHARE_SCALE
total_assets_crvusd = crvusd_share_price × effective_supply
```

Write the common historical row as:

```text
share_price  = crvusd_share_price
total_supply = effective_supply
total_assets = total_assets_crvusd
```

Resolve the denomination token from both the Factory and LT and require it to
match reviewed crvUSD. Export `_synthetic_usd_denomination = False`; do not use
USDC as a substitute.

The primary and native returns have this exact relationship:

```text
1 + crvUSD return
    = (1 + native LT PPS return) × (1 + asset/crvUSD return)
```

Persist only context required by the common reader and the requested
YieldBasis-specific analysis:

- native LT PPS;
- Curve asset/crvUSD oracle;
- effective and staked LT supply;
- previewed share and underlying amounts; and
- a concise reason when the optional redemption preview is unavailable.

Calculate native return/CAGR, temporary redemption difference and staked ratio
from these raw values. Keep the common Parquet schema unchanged.

Classify protocol fees as `VaultFeeMode.internalised_minting`. Return `None`
for fixed management and performance percentages because LT accounting already
reflects the allocation borne by existing unstaked holders.

## 1. Add the protocol package and interfaces

Create `eth_defi/yield_basis/` with:

- `addresses.py` for reviewed deployment and market identity;
- `contracts.py` for minimal ABI-backed Factory, LT, AMM and Curve bindings;
- `vault_catalog.py` for the safe pre-scan;
- `vault_sync.py` for common metadata reconciliation;
- `historical_context.py` for raw sampled state;
- `metrics.py` for pure native/crvUSD and redemption calculations;
- `vault.py` for `YieldBasisVault` and its contextual reader;
- `tags.py` for address-level strategy classification; and
- `README-YieldBasis.md` for product and pipeline documentation.

Store minimal read-only ABIs under `eth_defi/abi/yield_basis/` and document the
pinned canonical yb-core source in its README. Include only methods the adapter
calls.

Use frozen, slotted dataclasses for reviewed market identity, runtime products,
pre-scan results, context observations and operation summaries. Document every
dataclass field with a Sphinx `#:` member comment.

## 2. Run a special pre-scan before generic discovery

`fetch_yield_basis_scan_preparation()` must read one fixed block and:

1. require Ethereum chain ID 1 and deployed Factory bytecode;
2. require `Factory.STABLECOIN()` to equal reviewed crvUSD;
3. inspect `market_count()` and report newer unreviewed market IDs;
4. require each reviewed Factory asset/LT pair to match the allow-list;
5. validate the LT's asset, stablecoin, Curve pool and AMM links;
6. require Curve coin 0 to be crvUSD, coin 1 to be the reviewed asset and
   `price_oracle()` to be positive;
7. validate the AMM's crvUSD, Curve collateral and LT links; and
8. read the AMM kill switch.

Return individually valid products plus concise review messages. A Factory-wide
failure must leave existing YieldBasis rows unchanged and must not halt
unrelated Ethereum scanning. A changed product must be withheld without
deleting its old history.

After the lead-cache decision, reconcile the validated snapshot exactly once:
immediately on a cache hit, or after generic discovery on a cache miss. The
post-discovery merge restores a same-address LT that was recorded as a broken
generic candidate without duplicating metadata reads and database writes.

The price phase runs its own pre-scan at the price range's fixed end block.
Remove unvalidated YieldBasis products from that cycle's in-memory selection
while allowing unrelated vaults to continue.

## 3. Add VaultBase routing and AMM flags

Add `ERC4626Feature.yield_basis_lt`. Every reviewed row receives:

- `yield_basis_lt`;
- `amm_pool_like`; and
- `share_price_equivalence`.

Map the feature to protocol name `YieldBasis`, exempt it from the generic
deposit-count activity filter and route it from `create_vault_instance()` to
`YieldBasisVault`. Use hardcoded address routing only for the four reviewed
Ethereum LTs; Factory reconciliation remains the discovery source of truth.

`YieldBasisVault` must:

- implement `VaultBase` directly;
- return real crvUSD `TokenDetails` as its denomination;
- expose current fundamental PPS, supply, total assets and contextual history;
- return market-making and liquidity-provision flags;
- resolve maintained strategy tags by lowercase address;
- return `None` for an unmapped address;
- reject generic flow and deposit managers; and
- report deposits closed only when the AMM kill switch proves closure,
  otherwise unknown.

Classify the reviewed products as permissionless with an explicit exported
caveat that runtime limits and quotes still apply.

## 4. Store and read historical context

Use one protocol-owned table in
`$PIPELINE_DATA_DIR/vault-historical-context.duckdb`:

```text
yield_basis_historical_context
```

The logical key is `(chain_id, lowercase LT address, block_number)`. Store raw
uint256 values as decimal strings. Avoid DuckDB `PRIMARY KEY` and `UNIQUE`
constraints; use an unconstrained temporary batch table to reject conflicting
payloads and ignore exact duplicates.

Sample each selected product on the common hourly or daily block grid. Resolve
timestamps with the cache-aware Hypersync helper and read contract state from
the configured archive RPC provider using a bounded threaded worker pool.
Product/block pairs before the reviewed deployment block are not scheduled.
The defensive read guard skips provider or contract-state failures only before
that block; postdeployment RPC failures abort the bounded run instead of
leaving a silent gap. A deployed zero-supply LT is omitted until it has
investment value. Invalid decoded values propagate as errors. A reverting
`preview_withdraw()` produces nullable diagnostic fields.

Insert each prefill in bounded resumable batches rather than creating a DuckDB
temporary table for every product/block pair or retaining a full backfill only
in memory.

`YieldBasisHistoricalReader` must set `uses_contextual_history = True`, select
the latest source row in each requested bucket, derive the common crvUSD values
and yield normal `VaultHistoricalRead` objects.

Scheduled scans use `YIELD_BASIS_INITIAL_CONTEXT_LOOKBACK_BLOCKS`, defaulting to
100,000 blocks, only when neither an explicit start nor a shared per-chain
price-reader position is available. Later cycles follow that common writer
position; contextual readers do not own separate reader-state entries. Full
history belongs in the manual backfill.

## 5. Add migration, backfill and examination scripts

Follow `scripts/erc-4626/README-vault-scripts.md` and use environment variables
rather than command-line parsers.

Add four scripts:

| Script | Required behaviour |
|--------|--------------------|
| `migrate-yield-basis-vaults-metadata.py` | Default dry run; validate and reconcile exactly four rows; never touch price or reader state |
| `backfill-yield-basis-vault-prices.py` | Scan hourly from the earliest reviewed launch to one safe head; replace only four address histories; never pass or mutate reader state |
| `examine-yield-basis-vault-backfill.py` | Check scope, duplicates, positive values, exact context linkage and `assets = price × supply` |
| `examine-yield-basis-performance.py` | Show lifetime and three-month crvUSD and native CAGR on identical endpoint blocks, plus TVL, redemption difference and staked ratio |

Both mutating scripts default to `DRY_RUN=true`. The price backfill retains an
inspectable temporary directory and hashes the scheduled reader-state pickle
before and after the common writer.

Use `tabulate` for terminal tables, normal console logging for long work and the
repository's timestamp cache path. Do not add a parallel worker abstraction to
the four-product state-coupled catalogue pre-scan.

## 6. Add public descriptions and documentation

Create protocol metadata, feed metadata, original logo provenance and formatted
light/dark/generic logos. Public metadata must describe the YieldBasis product,
not scanner implementation.

Add Sphinx vault and API pages and include them in both indexes. Add a
prescriptive protocol note explaining:

- crvUSD denomination and depeg basis;
- BTC/ETH market exposure;
- the purpose of native-token CAGR;
- fundamental versus executable redemption value;
- internalised fee treatment; and
- the read-only unstaked-LT scope.

Write `eth_defi/yield_basis/README-YieldBasis.md` with the same reader journey as
the GMX vault README: product model, volatility risk, accounting, architecture,
catalogue, adapter, historical storage, scheduled pipeline, scripts, tests and
limitations. Include text charts for return decomposition, overall architecture
and scanner ordering.

## 7. Tests and acceptance

Add focused no-RPC coverage for:

- Ethereum-only hardcoded routing and exact features;
- protocol name and activity-filter exemption;
- fee, risk and vault flags;
- permissionless classification and its caveat;
- exact strategy tags and an unmapped address returning `None`;
- context idempotence, conflict rejection, bucketing and absence of ART
  constraints;
- zero-supply launch handling and threaded context prefill;
- reviewed Factory enumeration and catalogue reconciliation;
- contextual-reader conversion to common vault rows;
- native/crvUSD return decomposition, redemption difference and staked ratio;
  and
- metadata and logos.

Add a guarded real-provider test that exercises Factory, LT, Curve and AMM
reads plus one complete crvUSD valuation at one safe head. The default-dry-run
metadata migration additionally checks ERC-20 metadata without writing the
metadata database.

Acceptance criteria:

- exactly four reviewed Ethereum rows are published;
- every row uses `YieldBasisVault` and the three required features;
- every row resolves real crvUSD as denomination;
- a 10% asset/crvUSD move with unchanged native PPS produces about a 10%
  primary move;
- the examiner reports crvUSD and native CAGR on identical endpoint blocks;
- supply-only movement cannot create false performance;
- Factory or product validation failure cannot damage unrelated scans;
- the backfill leaves reader state unchanged; and
- focused tests, Ruff and `git diff --check` pass.

## Explicit follow-ups and non-goals

Do not add these to the first implementation:

- automatic publication of new Factory markets;
- legacy market histories;
- gauge-token adapters or YB incentive valuation;
- Hybrid Vault account aggregation;
- transaction-building deposit or withdrawal managers;
- investor flow reconstruction;
- USDC conversion or an external dollar feed;
- Factory event replay, per-market scan cursors or gap-state tables; or
- secondary lending-oracle history not consumed by the requested metrics.

## Production rollout

1. Stop `vault-scanner-looped` so it cannot overwrite metadata or price files.
2. Run the metadata migration with `DRY_RUN=true`; inspect the four products.
3. Apply the metadata migration with `DRY_RUN=false`.
4. Run the historical backfill with `DRY_RUN=true` and keep its temporary
   directory.
5. Run both examiners against the dry-run files.
6. Run the historical backfill with `DRY_RUN=false`.
7. Run both examiners against persistent files with
   `REQUIRE_ALL_PRODUCTS=true` for the structural check.
8. Restart the looped scanner and confirm its next Ethereum cycle reports four
   YieldBasis products and no review-required messages.

Keep the mounted production pipeline directory, token cache, metadata pickle,
reader-state pickle and dense Ethereum timestamp cache. Do not use an unmounted
container or rebuild shared state from an empty home directory.
