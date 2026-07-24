# Vault-test follow-up plan

**Date:** 2026-07-23

**Source:** [trade-executor PR #1576 follow-up](https://github.com/tradingstrategy-ai/trade-executor/pull/1576#issuecomment-5062126018)

**Status:** Eth-defi implementation complete; trade-executor integration
intentionally deferred to its repository.

## Goal

Make the vault-test result describe what actually happened. A mined and
correctly decoded vault action must be successful; a known unsupported adapter,
an unavailable redemption capacity, an async lifecycle that cannot be settled
on Anvil, a receipt-parser failure, and a trade-executor statistics failure
must remain distinct outcomes.

The work crosses two repositories:

- **web3-ethereum-defi** owns the vault-manager contract, protocol ABIs,
  protocol receipt parsers, live preflight data and capability metadata.
- **trade-executor** owns manager dispatch, batch outcome mapping, Anvil
  lifecycle selection and diagnostic statistics serialisation.

Implement the eth-defi changes first, publish the dependency, then change
trade-executor to use the new or existing manager surface. Do not paper over a
missing adapter by accepting a status-1 receipt without strict amount decoding.

## Current-state findings

The reported interface is partly already present in the current eth-defi
master. `VaultDepositManager` declares `analyse_deposit()` and
`analyse_redemption()` in `eth_defi/vault/deposit_redeem.py`; the standard
implementation is in `eth_defi/erc_4626/deposit_redeem.py`. In particular,
`EmberDepositManager` already decodes and validates `VaultDeposit` and
`RequestProcessed` in
`eth_defi/erc_4626/vault_protocol/ember/deposit_redeem.py`.

The consumer-side synchronous route still calls
`analyse_4626_flow_transaction()` directly in
`tradeexecutor/ethereum/vault/vault_routing.py`. It also constructs synchronous
transactions with generic ERC-4626 helpers rather than the selected manager.
Consequently Ember's parser and cSigma's amount-aware request preflight are
bypassed. The first change must remove those bypasses; a second competing
receipt-analysis interface is neither necessary nor desirable.

`CsigmaDepositManager` already reads `maxRedeem(owner)` and raises structured
`VaultFlowUnavailable` during `create_redemption_request()`. The remaining gap
is an explicit, caller-consumable amount preflight and correct batch mapping.

`UpshiftVault` deliberately returns no capability and raises for the multi-asset
shape. `LagoonDepositManager.force_settle()` already handles ERC-7540 tickets
on Anvil, but callers cannot discover that support from static capability
metadata. The bundled YieldNest ABI has callable functions but no events, so
the generic ERC-4626 parser raises `NoABIEventsFound` before it can inspect the
receipt.

## Outcome contract

Use these outcomes in the trade-executor report. Preserve raw values and the
underlying manager diagnostic in the JSON output.

| Condition | Outcome | Required context |
|---|---|---|
| Manager declares the operation unsupported | `adapter_unsupported` | Protocol, vault, direction and stable unsupported reason |
| Manager preflight finds insufficient immediate redemption capacity | `redemption_capacity_limited` | Requested and available raw shares, owner and vault |
| Async request lacks a safe Anvil settlement driver in full-lifecycle mode | `simulation_unsupported_async` | Manager capability and ticket type |
| Broadcast transaction reverts | Existing execution-failure outcome | Receipt status and decoded revert data when available |
| Status-1 transaction cannot be decoded by its manager | `receipt_analysis_failed` | Parser exception, transaction hash, protocol and vault |
| Diagnostic statistics have no trades | Do not change the completed action outcome | Empty statistics representation or omitted diagnostic position |

Do not convert a capacity limit into a clipped partial redemption, retry any of
these outcomes automatically, or classify a post-action statistics assertion as
a receipt-analysis failure.

## 1. Route every vault action through its manager

### eth-defi contract

- Keep `VaultDepositManager.analyse_deposit()` and
  `analyse_redemption()` as the only adapter receipt-analysis API. The current
  `ERC4626DepositManager` methods remain the default standard-event
  implementation.
- Add a small shared helper only if needed to convert
  `DepositRedeemEventAnalysis` into common executed asset/share values. Keep
  the manager return dataclasses stable; do not make protocol managers return
  `TradeSuccess` merely for the legacy consumer.
- Ensure every protocol-specific analyser returns a
  `DepositRedeemEventFailure` for a mined revert and lets unknown ABI, RPC and
  event-shape errors raise. A successful receipt with an unrecognised event is
  a parser failure, not a successful trade.

### trade-executor changes

- In `VaultRouting.deposit_or_redeem()`, build both synchronous and
  asynchronous requests using `create_deposit_request()` or
  `create_redemption_request()` on the selected manager. Build approvals using
  `get_deposit_approval_target()` and sign every request function against its
  actual bound contract, so manager requests with multiple contracts continue
  to work.
- Retain the generic ERC-4626 helpers only as private implementation details of
  `ERC4626DepositManager`; remove their use as a routing bypass.
- Catch `VaultFlowUnavailable` from manager request construction before the
  generic request-error handler. Map a cSigma redemption-capacity failure to
  `redemption_capacity_limited` (including both raw amounts), and map any other
  verified typed preflight condition to its dedicated outcome. It must never
  become `receipt_analysis_failed` merely because no transaction was sent.
- In `VaultRouting.settle_trade()`, call `manager.analyse_deposit()` or
  `manager.analyse_redemption()` for synchronous receipts. Convert the returned
  decimal asset and share amounts into existing trade accounting, preserving
  the current signs for sell amounts and validating the pair's denomination and
  share tokens before `mark_trade_success()`.
- Treat `DepositRedeemEventFailure` and parser exceptions separately. Only the
  latter for a status-1 receipt becomes `receipt_analysis_failed`; a reverted
  receipt follows the execution-failure path.
- Add an end-to-end routing test with a fake specialised manager to prove that
  both transaction construction and receipt analysis use manager overrides,
  while an ordinary ERC-4626 test proves the default path has not changed. Add
  a status-1 fake receipt whose manager analyser raises, and assert that it is
  specifically `receipt_analysis_failed`, rather than an execution failure.

## 2. Dispatch Ember deposits through the existing parser

- Do not duplicate `EmberDepositManager.analyse_deposit()`. Its strict
  `VaultDeposit` validation is the intended eth-defi implementation.
- Add an Ethereum fork regression for Apollo ACRED
  (`0x2b13311fd553e74b421d4ccc96e348f71e179dcf`) that funds a test caller,
  deposits through the manager, invokes the generic routing analysis path, and
  checks decoded assets, minted shares, owner, receiver and final balances.
- Retest the remaining four reported Ember vault IDs through
  `vault-test-trade --auto-simulated --rerun`: Earn
  (`0x9be9294722f8aad37b11a9792be2c782182cafa2`), Polymarket
  (`0x0b9342c15143e8f54a83f887c280a922f4c48771`), Third Eye
  (`0xf3190a3ecc109f88e7947b849b281918c798a0c4`) and UDL
  (`0x373152feef81cc59502da2c8de877b3d5ae2e342`). These are validation
  samples, not a five-vault lifecycle test matrix.

## 3. Add YieldNest RWA MAX receipt support

### Reconnaissance

- On a deterministic Ethereum fork, reproduce a deposit into ynRWAx
  (`0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8`) and save the proxy
  implementation address, exact event topics, emitters and asset/share
  balances.
- Obtain the verified implementation ABI or an application-exported interface.
  Record its canonical source in `eth_defi/abi/yieldnest/README.md`. Do not
  infer event fields from transfers or from ERC-4626 assumptions.
- Identify whether the receipt includes a standard vault-emitted `Deposit`.
  If it does, extend `yieldnest/Vault.json` with that verified event and retain
  the default parser; otherwise identify the single authoritative
  YieldNest-specific event that supplies both deposited assets and minted
  shares.

### Implementation

- Replace the trimmed `eth_defi/abi/yieldnest/Vault.json` with the minimal
  verified event ABI required by the selected path, retaining the existing
  callable functions.
- Add `YieldNestDepositManager` under
  `eth_defi/erc_4626/vault_protocol/yieldnest/` only when the verified event is
  not a standard ERC-4626 `Deposit`. Override only the necessary analyser,
  filter by the selected vault address and validate owner, receiver, assets and
  shares before creating `DepositRedeemEventAnalysis`.
- Make `YieldNestVault.get_deposit_manager()` return that manager and advertise
  a synchronous deposit capability only after the fork regression is stable.
  Preserve the existing generic behaviour for events that are genuinely
  standard.
- Keep queued withdrawal, maturity handling, alternative YieldNest deployments
  and unsupported event variants out of this item. Their support requires
  independent evidence.

### Tests

- Add the exact-vault fork test to
  `tests/erc_4626/vault_protocol/test_yieldnest.py`, asserting ABI event
  availability, the decoded raw-to-decimal amounts and balance deltas.
- Add trade-executor coverage proving the row is no longer
  `receipt_analysis_failed` because of an empty ABI.

## 4. Make Upshift multi-asset non-support explicit

The safe scope for this work item is honest classification, not an unverified
application-flow implementation.

- Extend `VaultDepositManagerCapability` with optional stable,
  JSON-serialisable unsupported reasons for each direction. Validate that a
  reason can accompany only an operation with `can_deposit` or `can_redeem`
  set to `False`; retain compatibility for current callers that construct the
  dataclass without reasons.
- Change `UpshiftVault.get_deposit_manager_capability()` for
  `multi_asset_like` vaults to return explicit false/false capability metadata
  with a reason such as `multi_asset_application_flow_not_implemented`, rather
  than `None`. Continue to raise a dedicated unsupported-flow exception when a
  caller ignores metadata and asks for a manager.
- In trade-executor, inspect capability before constructing a manager. Map this
  known static condition to `adapter_unsupported` without broadcasting an
  approval or deposit transaction. Persist the reason in the report.
- Add no-RPC capability-schema tests and a trade-executor candidate/runner test
  for Sentora USD Earn
  (`0x74ad2f789ed583dbd141bbdafc673fe1f033718b`).

A future Upshift feature may replace this with an `UpshiftMultiAssetDepositManager`
only after it proves the exact input asset, conversion, capacity rules,
settlement lifecycle and round-trip event accounting on a fork. That is a
separate feature, not a fallback hidden in this reporting fix.

## 5. Advertise and use Anvil settlement support

- Extend static capability metadata with an optional
  `supports_anvil_settlement` flag. Its meaning is narrow: an advertised async
  lifecycle can be advanced with a correctly typed ticket on an Anvil fork.
  `None` means the operation is synchronous or no async lifecycle is
  advertised; `False` means a ticketed lifecycle is supported but has no safe
  simulation driver.
- Set the flag to `True` for Lagoon's ERC-7540 deposit and redemption ticket
  types, because `LagoonDepositManager.force_settle()` already validates and
  settles both. Keep generic ERC-7540 and any manager without a protocol-safe
  driver as `False`.
- Add no-RPC schema validation and focused Lagoon tests for both ticket types,
  non-Anvil rejection and an invalid ticket type. The flag must agree with the
  manager's actual `force_settle()` acceptance.
- In trade-executor, retain the current default request-only async behaviour.
  Make the full-lifecycle batch mode opt into forced settlement only when the
  manager capability is `True`; otherwise emit
  `simulation_unsupported_async` with the capability reason before attempting
  settlement. Wire the existing `--settle-async-on-anvil` option (or its
  documented full-lifecycle replacement) through this check.
- Run one Lagoon deposit and redemption in full-lifecycle batch mode and verify
  that a supported row proceeds from request, settlement and claim to the
  normal manager analyser. Do not claim that every Lagoon deployment or other
  ERC-7540 adapter can be settled.
- Add a runner-level fake or generic-ERC-7540 capability test with
  `supports_anvil_settlement=False`. It must emit
  `simulation_unsupported_async` before calling `force_settle()`. This is
  separate from the Lagoon `True` full-lifecycle regression.

## 6. Promote cSigma redemption capacity to an amount preflight

- Add a public, amount-aware redemption preflight result for adapters that
  have an owner-specific capacity query, for example a small immutable result
  containing availability, requested raw amount, available raw amount and an
  optional structured reason. Do not add a generic default that could imply a
  live capacity check where none exists.
- Implement the cSigma override with `maxRedeem(owner)`. It must return the
  exact raw share capacity and identify a requested amount above it as
  unavailable. Refactor `create_redemption_request()` to use that one result,
  while retaining its authoritative `VaultFlowUnavailable` safeguard for the
  race between preflight and broadcast.
- In trade-executor, call the preflight before scheduling a redemption. Map a
  cSigma unavailable result to `redemption_capacity_limited`, retain both raw
  values, and do not sign or retry the request. Continue to handle any
  inclusion-time revert as an execution failure. Catch the existing
  `VaultFlowUnavailable` raised by `create_redemption_request()` through the
  same mapping, so a stale or bypassed preflight cannot fall through to the
  generic request-error or receipt-analysis paths.
- Extend `tests/erc_4626/vault_protocol/test_csigma.py` with a fixed Ethereum
  fork assertion that capacity equals `maxRedeem(owner)`, an at-or-below-cap
  redemption path, and an above-cap result that leaves shares unchanged. Add a
  consumer test that verifies the dedicated result mapping.

## 7. Keep statistics from changing the vault result

- In `tradeexecutor/statistics/statistics_table.py` and its caller, make
  long/short statistics serialisation return an empty or explicitly
  not-applicable table when a diagnostic vault position has no executed trades.
  Do not call position-side long/short classification in that case.
- Keep ordinary positions with executed trades unchanged and preserve genuine
  statistics errors that are unrelated to an empty diagnostic position.
- Add a regression that completes a simulated vault action with the diagnostic
  no-trade position and verifies the saved action outcome survives statistics
  generation. Add a normal executed long and short regression so the table
  still contains both sides.
- Add a separate regression that deliberately raises a statistics exception
  after a completed action and asserts it is retained in a statistics
  diagnostic field without overwriting that action's result.
- Change the batch exception boundary so, after typed capability and
  `VaultFlowUnavailable` branches have been handled, only a status-1 manager
  receipt-analysis exception may produce `receipt_analysis_failed`.
  Statistics exceptions must have their own diagnostic field and must not
  overwrite a completed action.

## Delivery order

1. Land the manager-routing refactor, typed `VaultFlowUnavailable` outcome
   mapping and fake-manager tests in trade-executor as one release; this
   activates the already-shipped Ember and cSigma manager code without a
   cSigma classification regression.
2. Land eth-defi capability and amount-preflight schema changes with no-RPC
   compatibility tests. Publish the release and update trade-executor's
   eth-defi dependency before it consumes the new fields.
3. Land the Upshift explicit unsupported classification and cSigma preflight
   override, then update trade-executor outcome mapping.
4. Land Lagoon Anvil-settlement metadata, publish the eth-defi release, update
   the trade-executor dependency, then land the full-lifecycle batch wiring.
5. Complete YieldNest ABI reconnaissance, parser and exact-vault fork test,
   then update the eth-defi dependency and add the trade-executor regression.
6. Land the statistics isolation fix and rerun the affected report rows.

Each protocol or consumer change should be independently reviewable. Do not
combine the speculative Upshift implementation, unrelated vault adapters, ABI
refreshes or broad reporting redesign with this work.

## Verification

Run only focused tests, using the required repository environment:

```shell
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_ember_deposit_redeem.py -v
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_yieldnest.py -v
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_csigma.py -v
source .local-test.env && poetry run pytest tests/lagoon/test_erc_7540_deposit_redeem.py -v
source .local-test.env && poetry run pytest tests/erc_4626/test_deposit_probe.py -v
```

In trade-executor, add and run only the corresponding routing, vault-test
runner and statistics tests. Finally rerun these representative IDs with the
normal Lagoon test environment:

```shell
VAULT_ID=1-0x2b13311fd553e74b421d4ccc96e348f71e179dcf poetry run trade-executor vault-test-trade --auto-simulated --rerun
VAULT_ID=1-0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8 poetry run trade-executor vault-test-trade --auto-simulated --rerun
VAULT_ID=1-0x74ad2f789ed583dbd141bbdafc673fe1f033718b poetry run trade-executor vault-test-trade --auto-simulated --rerun
VAULT_ID=1-0xd5d097f278a735d0a3c609deee71234cac14b47e poetry run trade-executor vault-test-trade --auto-simulated --rerun
VAULT_ID=1-0x06973fbca7c589d10dfbe45d694dce634bff6165 poetry run trade-executor vault-test-trade --auto-simulated --rerun --settle-async-on-anvil
```

The expected classifications are respectively: successful Ember analysis,
successful YieldNest analysis, explicit `adapter_unsupported`, explicit
`redemption_capacity_limited`, and a Lagoon full lifecycle where its selected
request can be force-settled. Re-run one previously affected no-trade
statistics row to confirm its completed action is retained. The
`simulation_unsupported_async` false-capability branch is deliberately covered
by the focused runner test above, not by these five representative IDs.
