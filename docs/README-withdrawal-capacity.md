# Withdrawal capacity warnings

`vault.fetch_withdrawal_capacity_limit(block_identifier=..., capacity_threshold=0.15)`
returns `WithdrawalCapacityLimit`. The same query is available on the deposit
manager. This is a read-only catalogue warning for current or near-term
withdrawals, not an order authorisation or a prediction of future liquidity.

The JSON exports contain `withdrawal_capacity_limit` with `capacity_warning`,
`pause_warning`, `capacity_reason`, `capacity_threshold`, `current_capacity` and
`current_tvl`, and `tiny_tvl`. Asset quantities are lossless decimal strings in the denomination
token; the threshold is a JSON fraction. Exactly 15% does not warn. Configure
`WITHDRAWAL_CAPACITY_THRESHOLD` at export time; changing it does not require RPC
refresh. Python APIs use the `Percent` type alias (`float`) for thresholds and assert
the float type; the environment boundary parses its text setting as `float`.
Token amounts remain `Decimal`; threshold arithmetic converts the float through
its decimal string to preserve exact boundary comparisons. The sibling `withdrawal_capacity_observation` records the source block,
time, measurement status, directional pauses and structural warning scope.

Unknown, partial, failed and stale observations never become healthy or zero
capacity. Measurements expire after their refresh interval plus two hours of
scanner grace. Historical API queries
are assessed at the source block's timestamp, while JSON export checks freshness
against its clock. Capacity is gross before fees. Pause warnings include deposit
pauses: inspect `deposits_paused` and `withdrawals_paused` before interpreting one
as an exit restriction. Both SingularV and LiquityV2 have deposit-pause fixtures.

40acres has an explicit structural warning even with an adequate numerical
buffer: outstanding loans are in TVL but cannot be recalled by lenders. Its
capacity measurement uses direct cash, capped by total asset entitlement.
Morpho V1 reads the withdrawal queue, supplied market positions and available
market cash, with stored-interest conservative rounding. Morpho V2 includes only
the configured ordinary withdrawal route; optional paid recovery is excluded.

IPOR with no instant-withdrawal fuses uses idle assets; configured fuses currently
produce an explicit partial result because idle cash alone cannot describe their
market exits. Fluid with zero cash in its configured liquidity contract has zero
capacity; nonzero cash remains partial until its expanding withdrawal limit is
resolved. Native fTokens use the Liquidity contract's native balance, identified
by `NATIVE_TOKEN_ADDRESS()`, instead of its WETH balance. Wrapped-token donations
or dust do not measure native reserves. An older wrapped-native deployment that
does not expose this interface remains partial, with its observed native balance
recorded separately. An unknown chain wrapper mapping also remains partial.
These limits must not be inferred from a zero-share account's maxima.
At the recorded stcUSD fork, available cash after reserved withdrawal claims is 42.590261 USDC against
57.010412 USDC TVL, so it does not warn: the earlier 42.953828-USDC limit was a **deposit** limit.

Withdrawal capacity has its own loop, separate from `create_vault_scan_record()`.
Vaults whose latest known TVL meets the **USD 10,000** guideline are checked every
24 hours; smaller vaults and unknown denominations are checked every seven days.
The scheduler reuses `convert_usd_threshold_to_denomination()` with the existing
policy rates: USD 1 per stablecoin unit, USD 2,000 per ETH-like unit and
USD 60,000 per BTC-like unit. Thus 5 WETH meets the daily boundary, while
0.16 WBTC stays weekly. This is a scheduling guideline, not a live USD valuation
or a new source for the `tiny_tvl` warning.

The last capacity TVL takes precedence over metadata NAV; metadata supplies the
fallback when the reader has no TVL. A successful read can promote or demote the
next interval. A failed read retains a previously daily schedule rather than
falling back to an older, smaller metadata NAV. New rows without an observation
join the due queue on the next loop tick, subject to the per-chain batch limit. Missing or malformed scheduling timestamps
are also due immediately.

The scanner loads the catalogue once per tick and selects due rows before
constructing providers or adapters. Chains with no due rows do not open an RPC
provider. Each tick attempts at most 100 vaults per chain by default; configure
`WITHDRAWAL_CAPACITY_MAX_VAULTS_PER_CHAIN` to change this positive limit. Older
overdue observations are selected first, and the log reports due, selected and
deferred counts. Large initial catalogues catch up across ticks, so consumers
may see unknown observations while that backlog drains. The recorded interval
governs scheduling until the next read, keeping its deadline aligned with
export freshness even if metadata NAV changes. The scanner skips fresh rows
before reading capacity. Due rows share a single numeric block (head minus three), at most four
workers per chain and a cache that coalesces identical contract reads within the
batch. After metadata workers finish, the loop updates only the capacity
namespace under the existing pipeline writer lock and atomically writes the
catalogue once per chain with results, reusing the same in-memory database
across chains. The coordinator also accepts `database=existing_database` to
reuse a supplied `VaultDatabase` without another disk read, updating it in place.
Supply a fresh, complete snapshot under the writer lock after metadata workers join; an
earlier settlement snapshot may be stale, and persisting it would overwrite
newer catalogue rows. Metadata replacements retain a previous
capacity observation when the protocol classification is unchanged.

Both normal and idle scanner ticks run the capacity loop. Idle ticks update
local observations without starting post-processing or uploads; normal scan
ticks retain the existing publication cadence. Both JSON exports assess stored
observations after cached-row replay. Discovery, flags, permissions, execution
preflights and strategy membership retain their existing behaviour.

Warnings are assessed when the export is generated. Daily observations expire
after 26 hours and weekly observations after seven days plus two hours. Legacy
observations without a recorded interval use 26 hours. Older observations become
unknown; consumers reusing an exported file must also check its observation
timestamp. Old rows without an observation remain unknown until the loop reads
them. No one-off catalogue migration is needed. Trade-executor consumption
remains separate.

Euler EVK vaults use `cash()` (accounted unborrowed assets), bounded by total
assets and global withdrawal-hook configuration. Custom exit hooks leave
executable capacity unknown. AlphaGrowth Base RWA is the concrete low-buffer
example: Base `0x4c1aeda9b43efcf1da1d1755b18802aabe90f61e`, pinned at block
56,688,126 in the integration matrix. It is distinct from the similarly named
`alphagrowth-base-rwa-2` vault. Source: [vault catalogue](https://tradingstrategy.ai/vaults/alphagrowth-base-rwa?period=max).
EulerEarn metavault strategy aggregation needs its own capacity reader; EVK cash
must not be applied to those metavaults.

`WITHDRAWAL_CAPACITY_MAX_AGE_SECONDS`, when set to a nonempty value, overrides
the adaptive export freshness limit for all vaults. It does not change the daily
or weekly acquisition schedule. Leave it unset or empty to use each observation's
recorded cadence plus grace. Numeric coverage remains partial for IPOR routes
with configured fuses and Fluid routes with nonzero liquidity.

`tiny_tvl` warns when total USD TVL is strictly below **USD 10,000**; exactly
USD 10,000 does not warn. It is independent of the 15% withdrawal-buffer rule.
The observation records `current_tvl_usd` and its valuation source. Recognised
native USDC/USDT addresses use the catalogue's nominal USD face-value guideline;
this does not claim a live depeg-price check. Other denominations require an
explicit same-observation USD valuation. Missing or stale USD TVL produces
`tiny_tvl: null`; ETH, BTC and EUR amounts are never compared directly with USD.

Unknown values also cover vault classes without an implemented observation
reader. A plain ERC-4626 interface does not yet imply an authoritative liquidity
reader or a USD TVL observation. The recognised USD address maps are deliberately
limited; consumers must treat null as unknown rather than a negative warning.

## Manual list scan

Use the read-only scanner to check an explicit list without updating the
catalogue, prices or strategy membership:

```shell
source .local-test.env
VAULT_LIST_FILE=vaults.json OUTPUT_JSON=capacity.json COLOUR=always \
    poetry run python scripts/erc-4626/check-withdrawal-capacity.py
```

The input is a JSON array of `"CHAIN-ADDRESS"` strings or objects:

```json
[
  {
    "chain_id": 8453,
    "address": "0x4c1aeda9b43efcf1da1d1755b18802aabe90f61e",
    "name": "AlphaGrowth Base RWA",
    "features": ["euler_like"]
  }
]
```

Omit `features` to probe the protocol, or supply catalogue feature names to
select the same adapter as the catalogue. An empty array explicitly selects a
generic ERC-4626 adapter. Feature names are validated before RPC reads; `null`
and unknown names are rejected. `VAULTS="CHAIN-ADDRESS CHAIN-ADDRESS"` also works.
The script pins one head-minus-three block per chain and records its number,
hash and timestamp. Probe-based classification uses the detector's own current
state; capacity measurements and constructed adapter reads use the recorded
block. Use supplied features for reproducible adapter selection.

The leftmost column is a green `✓` only when all three warning fields are
known and false. Red `✗` includes both warnings and incomplete assessments;
`Status` distinguishes `warning`, `warning + unknown` and `unknown`, while
`Read` distinguishes measured, partial, unsupported and failed observations.
An unknown pause check therefore prevents green even when the measured buffer
is adequate. This does not assert a withdrawal failure. The scan does not check
account permissions, asynchronous settlement requirements or trading windows.

Native pools remain explicit unsupported rows, including Hypercore's
Ethereum-shaped addresses and all catalogue features ending in `_native`.
Individual vault failures and missing
chain RPCs preserve the requested rows. Failure reasons record exception types
without provider URLs. The JSON retains full precision and observations; the
table displays token amounts to six decimal places. Optional settings:
`WITHDRAWAL_CAPACITY_THRESHOLD` defaults to 0.15, `MAX_WORKERS` defaults to the
smaller of four and the available CPU count, and `COLOUR` defaults to `auto`
(colour on a terminal). Set `COLOUR=always` for saved ANSI tables or
`COLOUR=never` for plain text. Reads use threaded Joblib with progress reporting.

## Run the scheduled refresh manually

`refresh-withdrawal-capacity.py` exercises the production due selector, readers,
cache and atomic persistence for one chain in a separate diagnostic catalogue.
The default chain is Monad (143). It accepts `VAULT_DATABASE` (default normal
pipeline catalogue) or `VAULT_METADATA_JSON` (a published vault export). A JSON
export supplies its listed vaults, not an exhaustive discovery result. Published
`current_nav`, its denomination and supplied features seed the local catalogue;
`source_denomination` is retained separately as provenance;
actual capacity observations come from current chain state.

```shell
source .local-test.env
CHAIN_ID=143 VAULT_METADATA_JSON=vault-metadata.json MAX_BATCHES=2 \
    poetry run python scripts/erc-4626/refresh-withdrawal-capacity.py
```

Defaults are one batch, 100 vaults per batch and at most four reader threads.
`MAX_BATCHES`, `MAX_VAULTS_PER_CHAIN` and `MAX_WORKERS` control these limits.
`STATE_DIR` defaults to `data/withdrawal-capacity/<chain>/<UTC run timestamp>`;
reuse the displayed directory to verify that fresh observations are skipped
without opening a provider. The source bytes are read once for loading and
SHA-256 recording on initialisation. Resume uses the saved catalogue and identity
without rereading the source, so a live source update or removal cannot break it.
Start a new `STATE_DIR` to select a new input snapshot. Pickle input retains
existing production observation timestamps; already-fresh rows stay skipped. Input files and the production catalogue remain
unchanged; this diagnostic is not a production repair or migration.

Each invocation saves a timestamped JSON report with complete observations,
next-check times, read/cadence counts and deferred-vault counts. The table shows
the three independent warnings, denomination amounts and daily/weekly cadence.
Green means all three checks are known and clear; red includes warnings and
unknowns. A deferred vault with no observation is labelled `unchecked`. Set
`COLOUR=always` for ANSI-coloured saved output or `COLOUR=never` for plain text.
On Monad the reader uses current head minus three, never an arbitrary historical
state block; no discovery, historical scanning, uploads or transactions occur.
