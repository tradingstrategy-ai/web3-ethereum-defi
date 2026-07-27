# Vault trade-simulation gaps plan

**Date:** 2026-07-27

**Source:** [trade-executor PR #1582 gap list](https://github.com/tradingstrategy-ai/trade-executor/pull/1582#issuecomment-5096814259)

**Status:** Planned. This supersedes only the unresolved items below; it does
not reopen previously verified fixes merely because a later matrix lists a
similar symptom.

## Goal

Make every selected vault action either complete with strict receipt and balance
evidence, or stop before broadcast with a stable, structured, protocol-specific
reason. A generic `transaction_reverted`, `execution_failed`, or guessed
permission status is not an acceptable final result for a predictable state.

This repository owns vault classification, metadata, manager capabilities,
preflight diagnostics, protocol ABI bindings, Anvil settlement drivers and
exact-vault tests. Trade-executor owns candidate-asset selection, gas limits,
result mapping, report persistence and the final matrix run. The plan therefore
publishes an eth-defi revision only after its focused evidence passes, then
requires trade-executor to consume that revision and rerun the affected IDs.

## Evidence and constraints

The plan incorporates these merged-PR hand-offs and corrections:

- [#1347](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1347#issuecomment-5045700241)
  requires `VaultDepositManager` as the caller boundary, ticket-level
  settlement proof, focused representative paths and explicit limitations.
- [#1357](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1357#issuecomment-5062449269)
  requires the consumer to use the selected manager for construction and
  receipt analysis, and to honour the directional Anvil-settlement capability.
- [#1375](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1375#issuecomment-5079051954)
  and [#1376](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1376)
  establish that a capacity preflight needs an authoritative, exact-deployment
  value and must fail closed rather than allow a raw redemption revert.
- [#1378](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1378#issuecomment-5080899464)
  disproved its own IPOR liquidity diagnosis: first rule out the caller gas cap
  before modelling a protocol liquidity failure. Its 40acres fix remains a
  regression control, not new work.
- [#1389](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1389)
  separates permission policy, account admission, asset eligibility and current
  availability. Do not turn a pause, cap, epoch or unsupported reserve asset
  into a whitelist classification.

For every target, first record the chain, fixed fork block (or Monad's current
provider-supported boundary), proxy implementation, ABI source, caller, asset,
requested raw amount, gas limit, transaction/calldata and the precise failure.
Use the shared `anvil_fork_pool`, chain midnight block and snapshot/revert test
pattern. A same-ABI substitute, a latest fork, or a historical Monad state call
is not acceptable evidence.

Implementation starts from current upstream master containing the already
merged vault fixes, including #1378's Pharaoh-specific 40acres preflight. The
planning worktree predates those merges, so absence from this checkout is not
evidence that a merged regression control must be reimplemented.

`VaultFlowUnavailable` is the pre-broadcast subtype of the shared
`VaultFlowError` context carrier. Known protocol states raise it before a
request is built, with `protocol`, `vault_address`, `caller`, `direction`,
`phase`, decoded/raw error data and the relevant requested/available/minimum
raw values. An adapter may declare `supports_anvil_settlement=True` only when
its exact ticket advances to its documented terminal state and the test asserts
it. Otherwise it must publish `False` and a concrete reason, allowing
trade-executor to report `simulation_unsupported_async` before attempting a
speculative settlement.

The two diagnostic channels are intentionally ordered. A known request
preflight (`VaultFlowUnavailable.preflight_result`) wins before any request is
broadcast. In full-lifecycle Anvil mode, a known false settlement capability is
checked for the selected direction before an async request is broadcast and
yields `simulation_unsupported_async`, not a fabricated `preflight_result`.
Request-only mode may still create and persist the protocol request because it
does not promise settlement. Trade-executor must retain the exception fields in
the first case, and the capability, settlement unsupported reason and selected
direction in the second. It must not collapse either result into a generic
failure.

## Work streams

### 1. Establish the exact-address regression matrix

Add a small parametrised exact-address test harness, split by chain where that
makes fixtures clearer, and a machine-readable fixture containing the reported
vault ID, operation, reserve asset, evidence block, **observed baseline** and
**required acceptance result**. The first reproduction records the observed
result without changing adapter code; a target remains an expected failing row
until an implementation changes it to the required result. Reuse existing
protocol tests for lifecycle mechanics; this harness verifies the reported
production deployment rather than a representative one.

Resolve the target name, chain and address from the production vault database,
then snapshot the resolved full vault IDs into the fixture. The database is the
authoritative source when a hand-off comment contains only a display name or
has become stale. Before protocol implementation begins, the fixture must also
record the chosen evidence block, caller and reserve asset; tests must not query
mutable production data at collection time.

The harness always supplies a known-sufficient transaction gas limit for the
target chain before interpreting a revert as protocol capacity, liquidity or
admission. Record the configured and measured gas in the evidence record; a
gas-cap failure is handed to trade-executor, not converted into an eth-defi
preflight rule.

The matrix must include the following current reports:

| Area | Exact targets | Required result after this work |
| --- | --- | --- |
| D2 policy | `42161-0x75288264fdfea8ce68e6d852696ab1ce2f3e5004` | Correct exported policy and account-admission result, proven from the deployed contract |
| Ember | `1-0x2b13311fd553e74b421d4ccc96e348f71e179dcf`, `1-0x9be9294722f8aad37b11a9792be2c782182cafa2`, `1-0x0b9342c15143e8f54a83f887c280a922f4c48771`, `1-0xf3190a3ecc109f88e7947b849b281918c798a0c4`, `1-0x373152feef81cc59502da2c8de877b3d5ae2e342` | Minimum-size preflight or a proven settlement; never a generic async failure |
| Lagoon | `1-0xdae854d0896ad2fee335689a3f7b4a95fd1a3e46`, `1-0xca790385506b790554571cbc9da73f0130cdcfd5`, `1-0xa00f63e85b3d242568a9edecb48f5e2cf879b07b`, `1-0xa96bc6e084aad6976d25df9431525ed2c4d3cae4`, `1-0xd17049ed25d8f99fe3bfd10cef2263da9995cfd8`, `8453-0x8092ca384d44260ea4feaf7457b629b8dc6f88f0`, `8453-0x2bff679b1a9fbcc202316c1402172747ba2fbf56`, `8453-0x4efc07dca8697792119484af33549f33ab11bf3c`, `8453-0x63b04d3ce2c14f6d308657ab73ac92fc1a0b1075`, `42161-0x1723cb57af58efb35a013870c90fcc3d60174a4e` | Proven settlement with clearly marked synthetic liquidity, or target-scoped async unsupported/capacity result |
| Gains | `8453-0xad20523a7dc37babc1cc74897e4977232b3d02e5`, `42161-0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0` | Ticket reaches the documented terminal state, or target-scoped async unsupported before settlement |
| Plutus | `42161-0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a` | Proven supported lifecycle or stable role-gated async unsupported result |
| YieldNest | `1-0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8` | Maturity/buffer-aware redemption result |
| 40acres | `43114-0x124d00b1ce4453ffc5a5f65ce83af13a7709bac7` | Existing exact-address capacity preflight remains intact |
| cSigma | `1-0xd5d097f278a735d0a3c609deee71234cac14b47e`, `1-0x438982ea288763370946625fd76c2508ee1fb229` | Paused-deposit or immediate-redemption state is preflighted |
| Morpho | `1-0x069662d2588fcac24b5c209456db965d151556f0` | Decoded, stable redemption limitation or successful lifecycle |
| Upshift | `1-0x74ad2f789ed583dbd141bbdafc673fe1f033718b` | Compatible asset selected or explicit reserve-asset mismatch |

The fixture must distinguish **eth-defi-owned** adapter/metadata work from a
trade-executor-owned caller configuration or gas limit. It must also retain the
following fourteen already correctly `whitelisted` IPOR/Lagoon rows as a
non-regression set; those are operational admission requirements, not data
defects: `1-0x95b2ed8f821570f85fd0e3e6e7088c6296587088`,
`1-0x888e1d3c509c80e24cab8a4872e164b7e5a6eb10`,
`1-0xc825779c89120eeef746c51130b362478e181d39`,
`1-0x4c5a611694c426cae9335d53e95b885090cf8c31`,
`1-0x32f07401eb177f2c0fc4f95f3928050d88dae7ed`,
`1-0xc2a119ea6de75e4b1451330321cb2474eb8d82d4`,
`1-0x3be67ba2d3fec744d1d2b5d564c83f57372578e4`,
`1-0x9fdbaaa76194d56e49cade12c1f216f47d2b865e`,
`1-0xf10801bcc3deaf467fb8b3dbb7430111822e6dab`,
`1-0xba6cfe8a9d199cd7f3e50114c4e4ec66f2d52c87`,
`1-0xef39d77c7fb6224ac974c5fa4e3151a6c6ce9594`,
`1-0xb993c32f578e5156369330787cf8c8fe033bf40e`,
`1-0xcb58582b0d52ce5feecb06ba9ce66598b0d57886` and
`1-0x175ea882b492c9b7a6d5852fe9da560dc7af1c72`.

### 2. Make classification and manager diagnostics composable

Extend the structured `VaultFlowError` contract with a stable
`preflight_result` enum/string, serialisation and no-RPC tests. Use a short
closed set at first: `whitelisting_needed`, `below_minimum`,
`deposit_paused`, `deposit_closed`, `reserve_asset_mismatch`,
`redemption_capacity_limited`, `redemption_unavailable`, and
`redemption_window_closed`. Keep the existing decoded error and raw selectors:
the result categorises the user-facing condition; it does not replace protocol
evidence.

Audit the target managers for user-state `ValueError`, `assert` and known raw
custom-error paths. Convert only repeatable protocol conditions to the common
exception. Unknown contracts, ABI drift and provider failures must still raise
normally, so they cannot be silently misreported as a business constraint.

Update capability tests to enforce all of the following:

- a mixed manager is evaluated by the selected direction; the existing single
  `supports_anvil_settlement` value applies only to its asynchronous directions
  and does not change an independent synchronous direction;
- `True` settlement capability has an exact ticket postcondition and a
  protocol-specific driver;
- `False` has a new stable `anvil_settlement_unsupported_reason` and, in
  full-lifecycle mode, prevents request and settlement broadcast;
- if reconnaissance finds a manager whose deposit and redemption directions
  are both asynchronous but have different Anvil support, replace the common
  boolean/reason with directional settlement fields; do not add that schema
  complexity speculatively;
- `deposit_assets` is emitted only for a manager that supports an explicit
  reserve-asset flow; and
- a full-lifecycle result keeps `synthetic_assets_injected_raw` separate from
  live redemption capacity.

Trade-executor then maps `preflight_result` first, preserving every structured
field in its report, and keeps decoded-error mapping only as compatibility for
an older eth-defi dependency.

### 3. Validate closed-deposit calldata against GuardV0

Add `simulated-success-deposit-closed` as a trade-executor **simulation-only**
outcome. It means two independent facts were proved: live admission is closed
with a structured `deposit_closed`/`deposit_paused` reason, and the exact
deposit calldata generated by the eth-defi manager is allowed by GuardV0 when
called as the non-governance asset manager. It never means the protocol accepted
a deposit, shares were minted, or the vault is open.

First add a common manager-level deposit-admission preflight result that
preserves current availability, a stable closure reason, raw capacity where it
is authoritative, and phase/window detail where available. `deposit_closed`
remains the live-state fact; `whitelisting_needed` stays a separate admission
fact and can never enter this validation path. A restricted account must still
receive `whitelisting_needed`, even if the vault is also closed.

Add an explicitly named validation-only manager method, for example
`create_deposit_request_for_guard_validation(owner, raw_amount)`. It must be
available only in an Anvil/fork validation context and return the normal
`DepositRequest` using exactly the production transaction targets, selectors,
receiver and calldata. It may bypass only temporary availability checks (cap,
pause, funding phase, date or window) far enough to construct the call. It must
not bypass account admission, reserve-asset selection, amount positivity, or an
adapter's permanent unsupported-flow boundary. Do not add a general production
`ignore_checks=True` flag. A manager that cannot prove production-equivalent
deposit calldata raises
`UnsupportedVaultSimulation` with a stable reason.

Expose the guarded probe mechanics as a reusable eth-defi helper. It accepts a
manager-generated validation request, its original typed closure and the
consumer-selected GuardV0-compatible contract, encodes every request function,
and calls `GuardV0.validateCall()` on each as the delegated asset manager—not
governance. It returns structured validation evidence containing the closure
reason and every independently validated target, calldata and selector. The
consumer remains responsible for selecting its actual primary or satellite
`TradingStrategyModuleV0` and corresponding Guard configuration; eth-defi must
not guess that deployment topology. This is simulated Guard contract
compatibility under the selected configuration procedure, not evidence about a
previously deployed production Guard. Do not broadcast a protocol deposit to a
known closed vault.

Approval validation and approval-before-deposit ordering are deliberately out
of scope. They add complexity without improving this adapter/Guard call-compatibility
proof, and `GuardV0.validateCall()` validates each call independently rather
than proving batch order. Add this rationale to the relevant Guard-validation
test module and fixture docstrings, and to comments in any protocol-shaped mock
smart contracts used by those tests. The tests validate only the adapter's
deposit calls; they do not construct, validate or order ERC-20 approvals.

Implement this narrowly for D2 phase gates, Plutus admin closures, cSigma pauses
and 40acres capacity/pause states, then add another protocol only after its
manager can guarantee equivalent calldata. Do **not** treat a standard
ERC-4626/Yearn `maxDeposit=0` result as a closure: it is ambiguous capacity
guidance. Add a Yearn path only after a verified shutdown/pause signal gives a
typed closure result at a fixed production address. A missing Guard whitelist,
wrong asset, wrong receiver or rejected deposit target/selector must fail
validation and cannot produce the new outcome.

Trade-executor detects and records the live closure, then invokes this path
only during explicit Anvil simulation. It emits
`simulated-success-deposit-closed` only after all deposit calls validate,
preserving the closure reason and GuardV0 evidence in `outcome_data`.
Non-simulated execution and an open-vault simulation retain their existing
behaviour: respectively stop at `deposit_closed` and perform the normal full
deposit lifecycle.

### 4. Correct policy and asset inputs before simulating

For D2 HYPE++, inspect the proxy implementation and its actual admission
surface with the executor Safe at one recorded Arbitrum block. Reconcile that
result with the existing source-derived `permissionless` export from #1389.
Only then either implement a D2 account/policy reader or create an
address-scoped, sourced metadata override. Add scanner/export tests proving
that policy and account membership are not conflated and that the emitted JSON
changes from `permissionless` to `whitelisted` only when the contract evidence
supports it.

For Upshift, consume the existing explicit `deposit_assets` and
`fetch_accepted_assets()` surface rather than guessing from ERC-4626 `asset()`.
Add an exact Sentora USD Earn test for RLUSD that proves the compatible asset
is selected, and a native-USDC test that refuses before approval/broadcast with
`reserve_asset_mismatch` naming the selected and accepted token addresses. The
trade-executor runner must choose a compatible configured reserve or publish
that stable result; it must not call the generic ERC-4626 route.

### 5. Finish or truthfully refuse asynchronous settlement

For Ember, retain the current request parsing and operator event validation,
but make paused withdrawal, share-balance and `minWithdrawableShares()` states
typed preflights. Apollo ACRED must return `below_minimum` with both raw share
values. For the four operator-settlement rows, inspect the exact deployed
operator path. Implement a driver only if it can impersonate the documented
role, process the ticket and prove the correct terminal event/payout. Otherwise
declare an exact supported false capability and return the concrete
operator/claimability reason before settlement.

For Lagoon, inspect each named deployment's version, Safe, settlement manager,
valuation actor, reserve location, approval target and asset. Strengthen the
existing `force_lagoon_settle()` integration only where the exact deployment
can materialise liquidity and progress its request. Report any Anvil top-up in
`synthetic_assets_injected_raw`; do not call it real redeemable liquidity. When
the Safe cannot fund a full redemption, compute a reliable immediate-capacity
preflight or return target-scoped async unsupported—never a failed forced
settlement transaction.

For Gains satellite deployments, reproduce the `vault_settlement_pending`
transition on both chains. Implement the manager/fulfilment sequence only when
the manager can prove request state progression and final claimant entitlement.
If required offchain operator authority cannot be recreated safely, advertise
false capability for that deployment and preserve the exact blocked transition
in the diagnostic.

For Plutus, first resolve the contradiction between the current direct
ERC-4626 manager and the reported fulfilment-gated redemption. Bind the
verified ABI, determine whether the target emits a request ticket, and test the
real role boundary. Either provide a role-safe, Anvil-only fulfilment helper
that proves completion, or declare the redemption asynchronously unsupported
with the role/error evidence. Do not impersonate or permanently alter a
protocol administrator outside the isolated Anvil test.

For each of Ember, Lagoon, Gains and Plutus, the investigation stop rule is:
record the verified ABI and implementation, role identity, ticket state, one
isolated Anvil impersonation/settlement attempt and the exact blocking selector
or state transition. If that evidence cannot prove a safe terminal transition,
stop reverse engineering, publish target-scoped false capability and retain the
evidence in the focused test and manager documentation.

### 6. Model real redemption and availability conditions

For YieldNest RWA MAX, inspect the maturity and immediate-buffer rules at the
fixed fork block. Add a dedicated manager only after identifying the verified
redeem/request/claim ABI. Before maturity or with insufficient immediate buffer,
return `redemption_unavailable` together with maturity/buffer evidence; after a
proven state transition, test the actual redemption path. Do not advertise the
generic redemption capability merely because the deposit is ERC-4626-shaped.

Re-run the Pharaoh 40acres exact-address test from #1378 before changing any
code. Keep its direct-underlying-balance preflight if it still reproduces. If it
does not, diagnose the changed onchain state rather than generalising its rule
to other 40acres vaults.

For cSigma, add a protocol-specific paused-deposit view or a narrowly scoped
preflight `eth_call` based on the verified deployed ABI. Map `Pausable: paused`
to `deposit_paused` before a deposit broadcast. Retain the #1376 cSuperior
queue-capacity proof and cSigma USD regression. Do not resurrect ERC-7540 or
partial-fill modelling: neither has an onchain ticket/claim surface that the
consumer can honour.

For Apyx USDC, obtain the verified Morpho implementation ABI that defines
`0xace2a47e`, bind it only to the affected adapter/deployment where necessary,
and reproduce the redemption with the correct owner, shares, reserve and gas
limit. Map a repeatable condition to the appropriate structured preflight only
when a reliable view or matching `eth_call` proves it. If the redeem succeeds
with adequate caller gas, hand the gas/configuration correction to
trade-executor instead of inventing a liquidity or capacity rule.

### 7. Require balance evidence for every simulated lifecycle

Every actual `success (simulated)` deposit must snapshot the designated share
receiver's raw share-token balance immediately before the deposit and after its
terminal receipt or claim. The after balance must be greater than the before
balance, and the raw delta must equal the manager's decoded minted-share amount.
This applies to synchronous deposits and to asynchronous deposits after final
claim; a request or claimable ticket alone is not success.

Every actual `success (simulated)` redemption must snapshot the designated cash
receiver's raw denomination-token balance immediately before the redemption
and after its terminal receipt or claim. The after balance must be greater than
the before balance, and the raw delta must equal the manager's decoded redeemed
cash amount. For forced Anvil settlement, report synthetic liquidity separately
and still prove the receiver obtained the decoded cash amount.

Persist the before balance, after balance and raw delta in the focused-test
evidence and trade-executor `outcome_data`. The
`simulated-success-deposit-closed` Guard-validation outcome is not an actual
simulated deposit and is deliberately exempt: it broadcasts nothing, mints no
shares and must continue to say so explicitly.

### 8. Verify, publish and close the cross-repository loop

Run no-RPC schema/capability/metadata tests, then focused shared-fork tests for
each changed protocol. Use `source .local-test.env && poetry run pytest …` with
a three-minute command timeout; do not run the whole suite. Format changed code
with `poetry run ruff format`.

After the eth-defi revision is available, update trade-executor's dependency
and run its matrix only for the exact IDs above plus the fourteen whitelist and
existing 40acres regression controls. The final report must include selected
asset, policy/account evidence, capability, preflight fields, decoded selector,
ticket status before/after, transaction hashes, synthetic top-up and the required
share/cash balance snapshots and deltas. A row is complete only when it is
`success (simulated)` with strict balance-backed action evidence or a documented
pre-broadcast result; no generic execution or infrastructure result may mask an
unresolved protocol gap.

## Sequencing

1. Land the exact-address fixture and reproduce each item without changing
   classification or adapter code.
2. Land the shared diagnostic schema, closed-deposit GuardV0 validation
   contract, balance-evidence contract and consumer mapping contract.
3. Address D2/Upshift input selection and the initial closed-deposit manager
   implementations, then Ember/Lagoon/Gains/Plutus
   settlement-capability truthfulness.
4. Address YieldNest, cSigma and Apyx only with verified ABI and state evidence;
   retain 40acres as a regression control.
5. Publish the dependency and require the focused trade-executor rerun before
   declaring any row closed.

Each protocol group is independently reviewable after step 2. Mark a row
**closed in eth-defi** when its exact test has the required result and all
structured/capability fields are asserted. Mark it **closed end-to-end** only
after the released dependency is consumed by trade-executor and its persisted
matrix row matches. No change should
combine a new settlement driver, data-policy override and unrelated generic
exception mapping in the same pull request.

## Acceptance criteria

- Every production-database-resolved target has a fixed-block (or documented
  Monad-boundary) exact test and one persisted evidence record.
- Every known refusal occurs before the relevant request/settlement broadcast
  and carries the required structured fields, except that the closed-deposit
  validation-only branch constructs but never broadcasts its
  production-equivalent deposit calldata.
- Every true Anvil settlement capability has a target-specific state-transition
  proof; every unsupported async route is false-capability with a stable reason.
- At least one D2, Plutus or cSigma closure has a fixed-fork GuardV0 test
  proving production-equivalent calldata is accepted from the asset-manager
  address; a negative protocol-admission or Guard-whitelist case proves the new
  outcome cannot bypass either control. Add a closed Yearn/ERC-4626 case only
  when a verified shutdown/pause signal, rather than ambiguous `maxDeposit=0`,
  supplies the typed closure evidence.
- The closed-deposit GuardV0 tests and protocol-shaped mock contracts explicitly
  document that ERC-20 approval validation and approval/deposit ordering are
  outside the test contract and are not asserted.
- A closed-vault validation records both the original closure detail and GuardV0
  evidence, never claims minted shares, and leaves ordinary production
  `deposit_closed` behaviour unchanged.
- Every actual successful simulated deposit proves the receiver's raw share
  balance increased by the decoded minted shares; every actual successful
  simulated redemption proves the receiver's raw cash balance increased by the
  decoded redeemed amount.
- D2 policy, account admission, Upshift asset eligibility, pause/capacity and
  operator settlement are represented as distinct facts in exported reports.
- Existing 40acres, cSuperior, cSigma USD, IPOR delay/gas and correctly
  whitelisted-vault behaviour remain covered.
- The final trade-executor report contains no generic failure for a target that
  has a known protocol-specific outcome.
