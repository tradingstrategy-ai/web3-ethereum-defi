# Withdrawal capacity warnings

`vault.fetch_withdrawal_capacity_limit(block_identifier=..., capacity_threshold=Decimal("0.15"))`
returns `WithdrawalCapacityLimit`. The same query is available on the deposit
manager. This is a read-only catalogue warning for current or near-term
withdrawals, not an order authorisation or a prediction of future liquidity.

The JSON exports contain `withdrawal_capacity_limit` with `capacity_warning`,
`pause_warning`, `capacity_reason`, `capacity_threshold`, `current_capacity` and
`current_tvl`, and `tiny_tvl`. Asset quantities are lossless decimal strings in the denomination
token; the threshold is a JSON fraction. Exactly 15% does not warn. Configure
`WITHDRAWAL_CAPACITY_THRESHOLD` at export time; changing it does not require RPC
refresh. Python APIs accept `Decimal` thresholds only and assert the type; the
environment boundary converts its text setting to `Decimal`. The sibling `withdrawal_capacity_observation` records the source block,
time, measurement status, directional pauses and structural warning scope.

Unknown, partial, failed and stale observations never become healthy or zero
capacity. Measurements older than two hours are stale. Historical API queries
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

`create_vault_scan_record()` collects the capacity observation as part of the
normal vault metadata refresh. It reuses the already constructed adapter and
metadata source block, stores the observation in the catalogue row and
isolates optional read failures from otherwise valid metadata. Both JSON
exports assess the stored observation after cached-row replay. No separate
capacity scheduler, chain loop or catalogue rewrite is involved. Discovery,
metadata refresh and publication retain their existing cadence.

Warnings are assessed when the export is generated. Observations older than
the configured maximum age become unknown; consumers reusing an exported file
must also check its observation timestamp. Metadata refresh can be less frequent
than the freshness limit, so this change does not promise hourly capacity data.
It does not alter flags, permissions, execution preflights or strategy membership.
Old rows remain unknown until their next normal metadata refresh; no one-off
catalogue migration is needed. Trade-executor consumption remains separate.

Euler EVK vaults use `cash()` (accounted unborrowed assets), bounded by total
assets and global withdrawal-hook configuration. Custom exit hooks leave
executable capacity unknown. AlphaGrowth Base RWA is the concrete low-buffer
example: Base `0x4c1aeda9b43efcf1da1d1755b18802aabe90f61e`, pinned at block
56,688,126 in the integration matrix. It is distinct from the similarly named
`alphagrowth-base-rwa-2` vault. Source: [vault catalogue](https://tradingstrategy.ai/vaults/alphagrowth-base-rwa?period=max).
EulerEarn metavault strategy aggregation needs its own capacity reader; EVK cash
must not be applied to those metavaults.

Configure `WITHDRAWAL_CAPACITY_MAX_AGE_SECONDS` (default 7,200) for the export
freshness policy. Capacity acquisition follows the existing metadata lifecycle;
there is no capacity-specific refresh interval. Numeric coverage remains partial
for IPOR routes with configured fuses and Fluid routes with nonzero liquidity.

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
