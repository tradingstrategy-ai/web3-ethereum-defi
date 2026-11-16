# Withdrawal capacity warnings

`vault.fetch_withdrawal_capacity_limit(block_identifier=..., capacity_threshold="0.15")`
returns `WithdrawalCapacityLimit`. The same query is available on the deposit
manager. This is a read-only catalogue warning for current or near-term
withdrawals, not an order authorisation or a prediction of future liquidity.

The JSON exports contain `withdrawal_capacity_limit` with `capacity_warning`,
`pause_warning`, `capacity_reason`, `capacity_threshold`, `current_capacity` and
`current_tvl`, and `tiny_tvl`. Asset quantities are lossless decimal strings in the denomination
token; the threshold is a JSON fraction. Exactly 15% does not warn. Configure
`WITHDRAWAL_CAPACITY_THRESHOLD` at export time; changing it does not require RPC
refresh. The sibling `withdrawal_capacity_observation` records the source block,
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
resolved. These limits must not be inferred from a zero-share account's maxima.
At the recorded stcUSD fork, available cash after reserved withdrawal claims is 42.590261 USDC against
57.010412 USDC TVL, so it does not warn: the earlier 42.953828-USDC limit was a **deposit** limit.

The all-chain coordinator refreshes due capacity observations after parallel
chain writers have joined and before post-processing. It changes only the new
observation namespace, uses bounded RPC workers with shared same-block contract reads, and checks an
hourly due time on every configured-chain tick, including capacity-only ticks. This does not force discovery, rewrite prices or change flags,
permissions, execution preflights or strategy membership. Old rows become unknown
until refreshed, so no one-off database migration is needed. Code changes do not
publish a production export or add trade-executor consumption.

Euler EVK vaults use `cash()` (accounted unborrowed assets), bounded by total
assets and global withdrawal-hook configuration. Custom exit hooks leave
executable capacity unknown. AlphaGrowth Base RWA is the concrete low-buffer
example: Base `0x4c1aeda9b43efcf1da1d1755b18802aabe90f61e`, pinned at block
56,688,126 in the integration matrix. It is distinct from the similarly named
`alphagrowth-base-rwa-2` vault. Source: [vault catalogue](https://tradingstrategy.ai/vaults/alphagrowth-base-rwa?period=max).
EulerEarn metavault strategy aggregation needs its own capacity reader; EVK cash
must not be applied to those metavaults.

Configure `WITHDRAWAL_CAPACITY_REFRESH_SECONDS` (default 3,600) and
`WITHDRAWAL_CAPACITY_MAX_AGE_SECONDS` (default 7,200) to match the operator's
refresh/freshness policy. A failure of this optional enrichment stage is logged
without suppressing unrelated price exports. Numeric coverage is incomplete for
IPOR routes with configured fuses and Fluid routes with nonzero liquidity; these
remain explicit partial results, rather than claiming complete family coverage.

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
