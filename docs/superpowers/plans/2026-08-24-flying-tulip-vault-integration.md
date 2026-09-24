# Flying Tulip sftUSD vault integration

Date: 2026-08-24

## Goal and scope

Integrate Flying Tulip's Staked Flying Tulip USD (`sftUSD`) contracts with the
common vault scanner on Ethereum, Sonic and BNB Chain. The integration must:

- classify the official `sftUSD` deployments as Flying Tulip vaults;
- preserve their contractual 1 sftUSD = 1 ftUSD redemption price;
- collect `EpochSettled` rewards from onchain history with Hypersync;
- value each FT distribution from an onchain FT/ftUSD market;
- publish a GMX-style, distribution-adjusted share-price equivalent for charts
  and return metrics;
- report principal, backing and redemption-liquidity facts without treating
  the fixed ERC-4626 conversion as proof of solvency; and
- leave deposits and redemptions unsupported until a queue-aware transaction
  manager is implemented and tested.

The first implementation covers the three contracts in Flying Tulip's
[official deployment registry](https://api.flyingtulip.com/ftusd/contracts/all):

| Chain | Chain ID | `sftUSD` proxy |
|---|---:|---|
| Ethereum | 1 | `0xeb48218a4c35C814C7678cBcae88C6Ee037F7625` |
| Sonic | 146 | `0xD1E5A86f1005F6356Bd022C587dE0f430CD2aeb1` |
| BNB Chain | 56 | `0xe1716796d6Bf37e4049bdb6e1150Cb713800FeEe` |

BNB Chain is included in classification and history collection even when it
has no settled epochs. An empty deployment must produce no invented return.
Supporting ftUSD minting/redemption, valuing the complete ftUSD collateral
portfolio, automatically claiming FT and building a transaction simulator are
out of scope for the first historical-performance pull request.

## Prerequisite

Rebase the implementation branch on `origin/master` before writing code. The
current research branch predates the GMX contextual-history work introduced by
commits `3efd926b2`, `377161af0` and `8c911dd71`. Flying Tulip must extend the
resulting interfaces rather than recreating them locally.

The implementation should start from the findings in
`docs/protocol-research/flying-tulip-0xeb48218a4c35c814c7678cbcae88c6ee037f7625.md`.
Before adding or loading contract interfaces, follow `eth_defi/abi/README.md`
and record the verified proxy-to-implementation sources.

## Accounting decisions

### Model sftUSD as the vault

`sftUSD` is the ERC-4626 vault and ftUSD is its denomination asset. The base
ftUSD token and its `MintAndRedeem` contract are not vault shares.

The staking contract deliberately fixes `convertToAssets()`,
`convertToShares()` and `totalAssets() / totalSupply()` at 1:1. Yield is paid
separately as claimable FT through `EpochSettled` distributions. Therefore:

```text
redemption share price = 1 ftUSD per sftUSD
principal assets       = sftUSD total supply in ftUSD units
investment return      = principal return plus separately distributed FT
```

The historical scanner should model this as a GMX-style
`share_price_equivalence`, rather than a direct ERC-4626 share price. Add
`ERC4626Feature.share_price_equivalence` alongside
`ERC4626Feature.flying_tulip_like` to every Flying Tulip deployment. This
reuses the common sparse filter and the existing downstream return, chart and
lifetime-metric path without adding a new Parquet field or a new cleaner branch.

The historical `share_price` is a reward-adjusted ftUSD share-price equivalent,
not a claim about `convertToAssets()` or an executable redemption quote. The
adapter's live ERC-4626 methods continue to expose the actual 1:1 contractual
conversion, while documentation, API metadata and examination output label the
historical curve as `share_price_equivalence`.

For Flying Tulip raw rows:

```text
share_price  = compounded FT distribution-adjusted share-price equivalent
total_supply = reconstructed sftUSD supply
total_assets = share_price * total_supply (synthetic performance value)
```

The synthetic `total_assets` maintains the common share-price identity for
historical rows. It is not the contractual `totalAssets()` value and must never
be used for a redemption quote or an economic TVL sum. The live adapter exposes
contractual principal assets separately; metadata and documentation must make
the distinction explicit.

`VaultHistoricalReader.uses_share_price_equivalence` already causes the common
sparse filter to compare only `share_price`, so a principal deposit or
withdrawal cannot create a performance row. Keep the existing GMX behaviour
and add Flying Tulip coverage to it. Audit API serialisation and public
descriptions so no consumer calls the equivalent price an ERC-4626 redemption
price.

### Calculate the epoch return from stake-seconds

For each settled epoch `e`:

```text
duration_e = settlement_timestamp_e - settlement_timestamp_(e-1)
average_stake_e = stakeTime_raw_e / 10^asset_decimals / duration_e
reward_ft_e = rewardAmount_raw_e / 10^reward_token_decimals
reward_value_ftusd_e = reward_ft_e * FT_price_in_ftUSD_e
epoch_return_e = reward_value_ftusd_e / average_stake_e
share_price_equivalent_e = share_price_equivalent_(e-1) * (1 + epoch_return_e)
```

Start performance tracking only after the Ethereum Curve FT/ftUSD pool became
the canonical market at block `25,531,725`, timestamp `1,784,042,255`. Retain
earlier mint and burn events only to reconstruct supply. Exclude all earlier
rewards, and use the first settlement on or after the boundary as the 1.0
baseline without compounding that settlement's reward, because its accrual
interval crosses the unsupported pre-market period. This produces a clean,
fully valued suffix instead of inventing pre-market FT prices.

Read the share/asset decimals from the contract and token metadata; do not
encode `10^6` merely because current ftUSD uses six decimals. Reconcile the
decoded stake-time scale with the verified source and enforce this independent
plausibility invariant for every epoch:

```text
0 < average_stake_e <= maximum reconstructed supply during epoch e
```

The `rateRay` identity alone cannot prove the decimal scale because all its
inputs are raw integers. Add a fixed real-epoch characterisation test which
compares the calculated average stake with reconstructed supply and a pinned
onchain epoch read.

Initialise the share-price equivalent at 1.0 immediately before the first fully
valid epoch. This is a distribution-reinvested comparison: it assumes that FT is converted to
ftUSD and restaked at settlement. It is not an automatically compounded
contract balance and the documentation and API must say so.

Retain and validate the event's `rateRay` using the contract identity
`rateRay = rewardAmount * 10^27 / stakeTime`. Reject zero or negative duration,
non-monotonic epoch IDs, duplicate conflicting events, non-positive
stake-seconds for a non-zero reward, and implausible price orientation. Store a
zero-reward epoch as a valid zero return. If an FT price is missing or stale,
store the raw epoch but leave that epoch and the compounded suffix unresolved;
do not treat an unavailable price as zero and do not bridge over the gap.

This index measures the reward available to a continuously staked unit. An
individual account can differ because its stake was present for only part of an
epoch, transferred during the epoch or claimed at a different FT price.

## Reuse from the GMX pipeline

Reuse these parts of the GMX work on `origin/master`:

- `VaultHistoricalReader.uses_contextual_history` and
  `fetch_contextual_historical_reads()`;
- protocol-owned tables inside
  `$PIPELINE_DATA_DIR/vault-historical-context.duckdb`;
- Hypersync streaming through `configure_hypersync_from_env()` and
  `open_hypersync_stream()`;
- chunk commits, bounded retries, explicit stream closure, idempotent inserts
  and hard failure on conflicting source rows;
- pre-filling contextual data to the same snapped scanner end block before the
  common Parquet writer runs;
- address-bounded stateless backfills which do not modify reader state or
  unrelated Parquet rows; and
- the common raw Parquet, cleaning and lifetime-metrics pipeline.

Do not reuse GMX's event schema, table or valuation assumptions. GMX events
contain a matched pool value and token supply from which an absolute price can
be calculated independently. Flying Tulip epochs are incremental, require an
FT price join and are path-dependent. They need their own raw tables and must
be replayed in epoch order from the deployment boundary.

## 1. Add protocol package, ABIs and deployment registry

Create `eth_defi/erc_4626/vault_protocol/flying_tulip/` with:

- `addresses.py` for checksummed, chain-aware sftUSD, ftUSD, FT, wrapper,
  circuit-breaker and deployment-block records;
- `vault.py` for `FlyingTulipVault` and its historical reader;
- `historical_oracle.py` for Hypersync log decoding;
- `historical_context.py` for DuckDB persistence, deterministic replay and
  scanner prefill;
- `reward_price.py` for FT/ftUSD oracle observations and timestamp-to-Ethereum
  block resolution;
- `tags.py` for address-level strategy classification; and
- `__init__.py`.

Commit verified interfaces for `EpochRewardsVault`, `ftYieldWrapperV2` and
`CircuitBreakerV2` under `eth_defi/abi/flying_tulip/`. Add an ABI README with
each proxy, implementation, explorer URL, source date and loader role. Include
the Curve pool interface only if an existing shared Curve ABI does not expose
the required methods.

Use `HARDCODED_PROTOCOLS` in `eth_defi/erc_4626/classification.py` because this
is a finite official deployment set. Classification keys must include the
chain ID and lower-case proxy address; do not classify by address alone or add
a probe to every candidate vault. Add `ERC4626Feature.flying_tulip_like`, map
it to `Flying Tulip`, and route it to `FlyingTulipVault` in
`create_vault_instance()`.

The official registry is a discovery and maintenance cross-check, not a live
runtime dependency. A new registry entry should cause an operator-visible
test/report failure until its chain, proxy, implementation, deployment block
and strategy tags have been reviewed and committed.

## 2. Collect epochs and supply changes with Hypersync

Query only the reviewed sftUSD proxy on each chain. Use targeted Hypersync log
selections for:

- `EpochSettled(uint32,uint256,uint256,uint256)`; and
- ERC-20 `Transfer` mints and burns where either endpoint is the zero address.

Do not use JSON-RPC `eth_getLogs`. Merge decoded logs in
`(block_number, log_index)` order. Reconstruct supply by adding zero-address
mints and subtracting zero-address burns; ordinary transfers do not change
supply. Record the supply immediately after each epoch event. Cross-check the
latest reconstruction against the live `totalSupply()` and fail visibly if it
does not match within exact token units.

Create two protocol-owned tables in the shared contextual DuckDB:

```sql
CREATE TABLE flying_tulip_epoch_context (
    chain_id UINTEGER NOT NULL,
    vault_address VARCHAR NOT NULL,
    epoch_id UINTEGER NOT NULL,
    block_number UBIGINT NOT NULL,
    block_timestamp UBIGINT NOT NULL,
    transaction_hash VARCHAR NOT NULL,
    log_index UINTEGER NOT NULL,
    raw_reward_amount VARCHAR NOT NULL,
    raw_stake_time VARCHAR NOT NULL,
    raw_rate_ray VARCHAR NOT NULL,
    PRIMARY KEY (chain_id, transaction_hash, log_index),
    UNIQUE (chain_id, vault_address, epoch_id)
);

CREATE TABLE flying_tulip_supply_context (
    chain_id UINTEGER NOT NULL,
    vault_address VARCHAR NOT NULL,
    block_number UBIGINT NOT NULL,
    block_timestamp UBIGINT NOT NULL,
    transaction_hash VARCHAR NOT NULL,
    log_index UINTEGER NOT NULL,
    is_mint BOOLEAN NOT NULL,
    raw_amount VARCHAR NOT NULL,
    PRIMARY KEY (chain_id, transaction_hash, log_index)
);
```

Keep these tables as raw source evidence. Calculate supply-at-epoch,
stake-seconds, valued reward, epoch return and the compounded index during a
deterministic ordered replay rather than persisting derived values that can go
stale when an FT price or source event is corrected. Decimal strings preserve
the full EVM `uint256` domain; validate canonical unsigned decimal syntax and
range before insertion, then parse to Python integers for replay.

The first prefill starts at the verified proxy deployment block. Scheduled
prefills resume from the last contiguous stored block with a conservative
reorganisation overlap. In one transaction, delete the protocol rows in that
overlap and insert the newly observed canonical rows before recomputing the
ordered replay. Idempotent inserts alone are insufficient because they cannot
remove a transaction orphaned by a reorganisation. Add an orphan-removal test
where an epoch disappears and re-settles under a different transaction hash.
Because the post-Curve index is path-dependent, a missing epoch within that
supported suffix is a hard error. Do not apply an arbitrary recent lookback as
if epochs were independent observations.

Use the cache-aware Hypersync timestamp helpers. Preserve the dense per-chain
timestamp caches at `~/.tradingstrategy/block-timestamp/`; never create a
parallel timestamp location.

## 3. Value FT from the Ethereum Curve market

Use the Ethereum Curve FT/ftUSD pool
`0x68102ff5406475881462880a8da3c9bc9181ad6c` as the primary onchain reward
price source. Resolve each epoch timestamp, including Sonic and BNB epochs, to
the greatest Ethereum block whose timestamp is not later than the settlement.
Populate/query the normal Ethereum timestamp DuckDB with the cache-aware
Hypersync API, then execute an archive `eth_call` for the pool's
`price_oracle()` at that block.

Before accepting the feed, verify `coins(0)`, `coins(1)`, token decimals and the
oracle orientation against `get_dy()` at a fixed current block. The observed
raw oracle is likely the inverse quote; encode and test the normalisation
instead of assuming that `10^18` means ftUSD per FT. Reject a zero oracle and
record both the raw oracle value and the normalised FT price.

Store sparse price provenance in a third protocol-owned table:

```sql
CREATE TABLE flying_tulip_reward_price_context (
    ethereum_block_number UBIGINT PRIMARY KEY,
    block_timestamp UBIGINT NOT NULL,
    pool_address VARCHAR NOT NULL,
    raw_oracle VARCHAR NOT NULL,
    oracle_updated_at UBIGINT NOT NULL,
    ft_price_ftusd VARCHAR NOT NULL
);
```

One Ethereum price cache serves epochs from all three chains. Require
`JSON_RPC_ETHEREUM` to be archive-capable for historical oracle calls, and use
`create_multi_provider_web3()` because scanner variables can contain
space-separated fallbacks. Block-mapping lag only proves that the chosen block
precedes the epoch; it does not prove that Curve's EMA is fresh. Read the pool's
historical last-price update timestamp at the same block (for example
`last_prices_timestamp()` where confirmed by the verified pool interface),
record it as `oracle_updated_at`, and reject prices older than a documented
maximum age chosen from observed pool interaction cadence. Exclude epochs
before pool deployment from performance coverage. Treat epochs during
uninitialised oracle warm-up or outside the supported pool interface as
unresolved. Verify the exact `price_oracle` signature for the
deployed Curve pool type rather than assuming a stableswap/twocrypto variant.
Do not fall back to the dashboard, DefiLlama or a current spot price for
historical rows. Those sources are validation references only.

As a later optimisation, Curve `TokenExchange` events may be collected with
Hypersync for independent TWAP checks. They are not the primary feed because a
low-volume interval can have no trade even while the pool oracle has a valid
state.

## 4. Implement the contextual historical reader

`FlyingTulipHistoricalReader` opts into contextual history and yields one
`VaultHistoricalRead` at each valid settled epoch. It reads only the prefetched
local context; all network collection happens before
`scan_historical_prices_to_parquet()`.

For every row it supplies:

- the source chain block and naive UTC timestamp;
- the compounded reward-adjusted `share_price` equivalent;
- reconstructed `total_supply` and synthetic `total_assets` equal to
  `share_price * total_supply`;
- zero direct management and performance fees; and
- `vault_poll_frequency = "contextual"` through the common reader.

The contextual reader does not make historical conversion calls. Treat 1.0 as
the reviewed contract invariant and, during every prefill, pin
`convertToAssets(10**share_decimals)`, `totalAssets()` and `totalSupply()` calls
to the exact safe source `end_block`. Fail visibly if the conversion is no
longer 1:1 or contractual assets no longer equal supply; an upgrade then needs
a new accounting review before scanning continues. Pin the latest reconstructed
supply comparison to the same `end_block` so a later mint cannot create a
false mismatch.

Use the source event block as row identity. If more than one epoch occurs in a
common sampling bucket, retain the latest event only after replaying every
intermediate epoch into the index. Downsampling must never omit a reward from
the compounded calculation.

Set `share_price_equivalence` on the Flying Tulip vault feature set. The common
reader then already compares share price rather than supply/TVL, as it does for
GMX. Retain a first equivalent-price baseline row at 1.0 before or at the first
fully valid epoch so lifetime calculations have a defined starting point. The
exact baseline block must be deterministic and covered by a test.

## 5. Wire the scanner and backfill tools

In `eth_defi/vault/scan_all_chains.py`, select Flying Tulip vaults after normal
lead discovery/hardcoded seeding and prefill their context to the same snapped
`end_block` passed to the common historical scan. Reuse one long-lived
`asyncio.Runner` per chain scan and explicitly close every Hypersync receiver.
Create the Ethereum FT price observations needed by the newly fetched epochs
before opening the common Parquet writer.

Serialise access to `vault-historical-context.duckdb` with a shared
interprocess lock used by the scheduled scanner, backfill and examination
tools. A writer holds the lock across its delete/refill transaction; a manual
writer must fail clearly or wait with observable status instead of racing the
looped scanner. Continue to use the common Parquet writer's own atomic and lock
discipline for the output file.

The scheduled order becomes:

```text
ordinary ERC-4626 discovery and Flying Tulip classification
  -> Hypersync epoch and supply prefill
  -> Ethereum timestamp mapping and Curve archive oracle reads
  -> FlyingTulipHistoricalReader contextual rows
  -> common raw Parquet
  -> common cleaning and lifetime metrics using share_price_equivalence
```

Add environment-variable-driven scripts under `scripts/erc-4626/`:

1. `seed-flying-tulip-vaults.py` idempotently ensures all reviewed official
   deployments exist in the metadata pickle without deleting enrichment.
2. `backfill-flying-tulip-history.py` immediately finds each reviewed sftUSD
   proxy's deployment block using archive code reads, then provides a full
   Flying Tulip genesis-to-safe-head, real-Hypersync source backfill. It writes
   replayable epoch and supply evidence to the shared contextual DuckDB, is
   stateless and never reads or resets
   `reader-state.pickle`. Once the contextual reader is added,
   `backfill-flying-tulip-vault-prices.py` reuses that complete source context
   and rewrites only Flying Tulip addresses in the common Parquet.
3. `examine-flying-tulip-vault-backfill.py` validates source coverage,
   contiguous epochs, supply reconciliation, price provenance, equivalent-price
   continuity, pinned 1:1 redemption invariant, duplicates and missing/stale-
   price gaps.
4. `examine-flying-tulip-vault-performance.py` prints epoch rewards, FT/ftUSD
   prices, principal TVL, total-return index, reward APR and common lifetime
   metrics in a table.

Document all variables and examples in
`scripts/erc-4626/README-vault-scripts.md`. Long-running actions need progress
and periodic log output. The backfill must accept an address allowlist and
explicit start/end bounds, but a performance rebuild still verifies that all
prior epochs required by the index exist in context.

## 6. Add protocol metadata and capabilities

Complete the normal vault-protocol onboarding workflow:

- add Flying Tulip to the risk and fee matrices, initially using human-reviewed
  risk or `None` and zero direct vault fees;
- add public metadata YAML, original logo sources, processed `light.png` and
  any defensible `dark.png`;
- add vault and API documentation plus index references;
- add the protocol feed YAML;
- add chain/address strategy tags after running the
  `categorise-vault-strategy` workflow for every deployment; Ethereum and Sonic
  require evidence-based lending and delta-neutral/carry review, while dormant
  BNB must remain explicitly unmapped if there is no active strategy evidence;
  and
- add a dated `CHANGELOG.md` entry when the implementation is prepared as a
  feature pull request.

Document that FT rewards are treasury-discretionary, account stake-second
weighted and not included in the redemption price. Also document upgrade,
blacklist, wrapper, oracle, strategy-liquidity and circuit-breaker risks. Do not
double count sftUSD principal and the ftUSD collateral backing in protocol TVL.

The share-price equivalent is denominated in ftUSD and therefore does not
include a possible ftUSD/USD depeg. Do not label it as a dollar return or an
executable redemption quote. Principal TVL is also ftUSD-denominated unless a
separate historical ftUSD/USD market-price join is present. If consumers need
USD results, add that price as an independent conversion so the ftUSD peg
assumption is explicit. Add ftUSD, `MintAndRedeem`, the wrappers and circuit
breakers to the appropriate non-vault/exclusion checks so generic ERC-4626
discovery cannot create a second listing or double-count backing capital.

The inherited generic ERC-4626 deposit manager must not be certified. A
redemption may burn shares and return a non-zero circuit-breaker queue ID while
delivery is delayed by up to the live `settlementDelay()` (six hours at the
research snapshot). Initially return no public deposit/redemption capability.
A follow-up manager must call `redeemWithQueueId()` or
`withdrawWithQueueId()`, persist and expose the queue ID, wait for settlement,
call `executeQueued()`, and cover both immediate and forced-queue paths before
the capability is advertised.

Read and expose wrapper capital, wrapper liquidity, withdrawal capacity,
settlement delay and pause state in protocol-specific metadata where the
existing schema permits. Keep wrapper backing value distinct from contractual
`totalAssets()`, and do not label circuit-breaker capacity as lending
utilisation.

## 7. Tests and acceptance

### Unit coverage

Add focused tests for:

- exact `EpochSettled` topic/data decoding and integer scale handling;
- mint/burn supply reconstruction, including multiple logs in one block;
- identical retry idempotency and conflicting duplicate rejection;
- missing prefixes, skipped/non-monotonic epochs and reorganisation overlap;
- a reorganisation which removes an old event from the overlap window;
- the first-epoch boundary derived from pinned onchain accumulator state;
- `rateRay` reconciliation and zero-reward epochs;
- token-decimal verification and average stake bounded by epoch supply;
- FT oracle token order, inversion, decimals, zero values and staleness;
- stake-time return calculation and deterministic compounding;
- a missing reward price invalidating the unresolved suffix;
- bucket downsampling which retains the latest row but compounds every epoch;
- `share_price_equivalence` sparse filtering despite deposits and withdrawals;
- synthetic total-assets identity (`share_price * total_supply`) for Flying
  Tulip equivalent rows;
- ordinary common return metrics consuming the equivalent `share_price` without
  a protocol-specific cleaner;
- unchanged GMX, ordinary ERC-4626 and native-vault results;
- metadata seeding preserving unrelated scanner and enrichment fields; and
- concurrent context writers respecting the shared lock.

No common Parquet schema migration is required. The implementation still must
run its address-bounded write against a copy of the production
`vault-prices-1h.parquet` and must never reset it to empty.

### Provider integration coverage

Add a mandatory, non-mocked real integration test that streams a known Ethereum
`EpochSettled` event and its supply logs through the configured Hypersync
endpoint, maps the timestamp to an Ethereum block, reads the real Curve oracle
from the configured archive RPC, stores all three context tables, yields the
contextual reader row, writes it through the common Parquet path and reaches
lifetime return calculation. Do not replace any part of this path with mocked
HTTP, RPC or Hypersync responses; skip only when the required authenticated
provider configuration is absent.

Hypersync multi-minute tests use the repository's CI marker pattern. The test
must assert at least one real settled epoch, its transaction/log identity and a
positive onchain oracle observation. Record the exact real-provider command,
pass result, date and redacted provider in the pull request as required for a
new external integration.

### Fork coverage

Before writing fork tests, read the module docstring in
`eth_defi/testing/anvil_fork_pool.py`. Use the shared Anvil pool, fixed reviewed
blocks and `xdist_group` markers. Add characterisation coverage for all three
adapters where archive RPCs are available, with absolute assertions for:

- protocol classification and denomination/share token metadata;
- fixed 1:1 contract conversion and contractual assets;
- zero direct fees and unsupported generic deposit manager;
- wrapper capital, liquidity, circuit-breaker capacity and settlement delay;
  and
- latest onchain supply matching the Hypersync reconstruction.

Pin the comparison calls to the Hypersync end block. Also test that a dormant
deployment with no epochs remains a classified vault with an explicit
`performance_not_started`-style status rather than being marked broken or
receiving a fabricated baseline row. Confirm dashboards and metrics tolerate
the absence of a performance series between settlements.

Transaction support is not part of the initial pull request. Its follow-up
must additionally test deposit, immediate redemption, forced queued redemption
and later queue execution on a snapshot/reverted shared fork.

### Completion criteria

The integration is complete when:

1. all three official deployments classify as Flying Tulip without adding a
   global probe;
2. active chains replay every epoch from deployment and reconcile current
   supply, while dormant BNB emits no false data;
3. raw rows carry the Flying Tulip `share_price_equivalence` feature and a
   reward-adjusted, clearly non-redeemable `share_price` equivalent;
4. scheduled scans and full backfills are restartable, address-bounded and do
   not touch reader state or unrelated Parquet rows;
5. cleaning, charts and lifetime metrics use the common equivalent-price path
   for Flying Tulip and retain existing behaviour for all other vaults;
6. missing/stale FT price data is visible and cannot become a zero return;
7. the focused unit, real-provider and fixed-fork tests pass; and
8. examination output explains every index point through an epoch event and a
   recorded Curve oracle observation.

## Production rollout

1. Back up the metadata pickle, raw and cleaned Parquets, reader-state pickle,
   shared historical-context DuckDB and all per-chain timestamp DuckDBs.
2. Validate the address-bounded equivalent-price write on a copy of the
   production raw Parquet.
3. Run metadata-only seeding first with historical price scanning disabled.
4. Prefill and examine the three context histories in a temporary pipeline
   directory; compare current reward APR with the Flying Tulip dashboard and
   DefiLlama as non-authoritative sanity checks.
5. Run the address-bounded common Parquet backfill on a production-file copy
   and execute the examination scripts.
6. Stop `vault-scanner-looped` and any concurrent maintenance writers, acquire
   the shared context/Parquet locks, then atomically replace production
   artefacts only after row counts, schema,
   source linkage, supply reconciliation and metrics pass.
7. Restart `vault-scanner-looped`, enable scheduled contextual prefills and
   watch warnings for epoch gaps,
   supply mismatches, oracle staleness and unresolved equivalent-price suffixes.

Never delete or recreate `~/.tradingstrategy`, reset reader state, discard an
unreadable Parquet or allow a one-shot container without the mounted dense
timestamp cache to bootstrap production history.
