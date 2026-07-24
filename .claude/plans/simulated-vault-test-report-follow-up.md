# Simulated vault test report follow-up plan

## Goal

Close every adapter and consumer gap identified by the 129-vault simulated
trade report in [trade-executor PR #1576](https://github.com/tradingstrategy-ai/trade-executor/pull/1576#issuecomment-5070789686).
The result must distinguish an unsupported adapter, a live vault restriction,
an asynchronous request awaiting settlement, insufficient immediately
redeemable liquidity, a reverted transaction, and an actual receipt-decoding
fault. It must not turn any of those conditions into a generic execution or
receipt-analysis failure.

This is a two-repository plan:

- `web3-ethereum-defi` owns protocol adapters, ABI/event decoding, manager
  capability and request/settlement interfaces, and fork evidence.
- `trade-executor` owns lifecycle orchestration, outcome classification,
  persisted ticket handling, bridge-back accounting and report rendering.

Do not send live transactions. All state-changing evidence uses ephemeral
Anvil forks, with a fixed block wherever the chain supports archive state.

## Baseline and boundaries

PR #1357 is the prerequisite capability baseline. Before beginning a work
item, verify its changes are present in the implementation branch rather than
duplicating them:

- directional `VaultDepositManagerCapability` with a protocol reason for an
  unsupported operation;
- manager request builders as the public entry point;
- raw-share immediate-redemption capacity preflight for cSigma;
- safe Anvil ticket-settlement capability for Lagoon; and
- the verified YieldNest ABI receipt-event addition.

The report's `deposit_closed`, `whitelisting-needed` and deliberately
request-only `async_request_only` results are live policy/state observations.
They need clear reporting and regression coverage, but they are not adapter
bugs to bypass. A capacity preflight is similarly advisory: it must be
repeated immediately before transaction construction and must never promise
that a later request will succeed.

Keep the shared manager contract protocol-neutral. In particular, do not make
the executor recognise `redeemShares` or `makeWithdrawRequest` selectors, do
not infer support from ERC-4626 interface detection, and do not encode a
protocol's current pause/cap/allow-list state in static capability metadata.

## Evidence targets

Use the following report rows as named regression fixtures. Record the fork
block, whale/source account, requested raw amount and expected result at the
time each test is added; use absolute assertions on fixed-state mainnet forks.

| Protocol / issue | Vault ID | Required final result |
| --- | --- | --- |
| Upshift multi-asset USDC deposit | `1-0x74ad2f789ed583dbd141bbdafc673fe1f033718b` | USDC deposit request is built and decoded; redemption/request lifecycle is represented accurately |
| cSigma immediate capacity | `1-0xd5d097f278a735d0a3c609deee71234cac14b47e` | insufficient `maxRedeem(owner)` reports requested and available raw shares |
| Ember async redemption | one report Ember vault plus a below-minimum fixture | terminal ticket/settlement is discoverable and below-minimum is structured unavailable |
| Gains / Ostium request flow | a report Gains vault and existing Ostium V1.5 fixture | manager-derived request ticket/settlement transaction, no selector allow-list |
| Yearn Arche USD redemption | `1-0x0b45a1e71a8a09f5d382fed27202d50ed983aaf3` | valid redemption `DepositRedeemEventAnalysis` |
| YieldNest RWA MAX deposit | `1-0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8` | valid deposit `DepositRedeemEventAnalysis` |
| D2 funding window | `42161-0x75288264fdfea8ce68e6d852696ab1ce2f3e5004` | structured deposit-unavailable result with next opening time |
| Accountable / Hyperithm deposit | failing report vault and decoded `0x5945ea56` source | structured admission/preflight reason or decoded custom error |
| Lagoon full lifecycle | `1-0x06973fbca7c589d10dfbe45d694dce634bff6165` | safe Anvil settlement is advertised and selected |

For any report fixture whose balance, cap or epoch has changed, preserve the
same behavioural assertion with a newly documented block rather than weakening
the test to merely assert that a call does not throw.

## Phase 1: settle the shared manager contract

1. Extend `eth_defi/vault/deposit_redeem.py` so capability is explicitly
   directional and describes, per direction:

   - whether request construction is implemented;
   - whether it is synchronous or asynchronous;
   - the reason when unsupported; and
   - whether the concrete ticket can safely be force-settled on Anvil.

   Make settlement support ticket/direction scoped, not a vault-wide boolean:
   an adapter may support a deposit ticket but not a redemption ticket. Preserve
   backwards-compatible serialisation for existing consumers, then version or
   extend the public schema deliberately rather than silently changing its
   meaning.

2. Add shared result types/methods for amount-aware availability before a
   request is constructed. The preflight query returns an availability result
   and never raises for an expected live restriction; it retains raw requested
   and available amounts, direction, reason code and optional next-open
   datetime. A request builder repeats that query immediately before binding
   its call and raises `VaultFlowUnavailable` carrying the same result object
   when the request is unavailable. Replace plain `ValueError` and
   `NotImplementedError` escapes at public adapter boundaries. Do not use a
   broad exception catch to convert programming or RPC errors into
   availability.

3. Define a manager-owned terminal transaction interface. A request/ticket
   must be able to return its next settlement or claim transaction(s), and the
   manager must analyse the terminal receipt. The generic executor may select
   lifecycle steps through this interface, but must not inspect calldata
   selectors.

4. Keep `ERC4626DepositManager` as the default for standard synchronous
   `Deposit`/`Withdraw` flows. Every protocol-specific manager must override
   only the construction, preflight, ticket, settlement and analysis behaviour
   that actually differs. Its analyser returns either
   `DepositRedeemEventAnalysis` or a typed failure; a status-zero receipt is
   still handled by the lifecycle boundary as the authoritative reverted
   transaction outcome.

5. Add no-RPC unit tests for capability validation/serialisation, unavailable
   amount preservation and manager-derived terminal-step dispatch, including a
   preflight that returns unavailable data without raising. Initially permit
   only Lagoon to advertise Anvil settlement, and prove each advertised Lagoon
   direction/ticket with a focussed fork test that reaches its terminal receipt.
   Any later adapter that advertises the flag must add an equivalent per-
   direction fork regression in the same change; do not advertise settlement
   support based solely on a unit test.

## Phase 2: implement protocol adapter work

### Upshift (P0)

1. Add `eth_defi/erc_4626/vault_protocol/upshift/deposit_redeem.py` with
   `UpshiftMultiAssetDepositManager`, plus explicit request and ticket classes.
   Change `UpshiftVault.get_deposit_manager()` to return it for
   `multi_asset_like` rather than raising `NotImplementedError`; retain
   `multi_asset_application_flow_not_implemented` until the complete manager
   and fork evidence land.

2. Obtain and package the verified proxy implementation/application ABI under
   `eth_defi/abi/upshift/`, recording canonical explorer/source links. Discover
   the supported USDC asset from the vault/application state; do not hard-code
   a token address or assume the first configured asset is USDC.

3. Implement the exact application deposit path: select USDC, quote the
   conversion and expected minted shares, read pause/cap/allow-list state,
   approve the real spender, construct the request, parse the protocol event
   and return a normal analysis. Model the real redemption shape: synchronous
   only if the deployed product redeems immediately, otherwise return a
   request ticket plus the manager's settlement metadata. Capacity/allow-list
   rejections must be typed `VaultFlowUnavailable` data.

4. Add a mainnet-fork USDC deposit and full redemption/request lifecycle test,
   including wrong asset, paused/capped and non-allow-listed preflight cases.
   Assert the actual spender, receiver, raw USDC quantity, minted shares and
   terminal event, so a generic ERC-4626 call cannot accidentally satisfy it.

### cSigma

1. Move cSigma's `maxRedeem(owner)` read and enforcement into a
   `CsigmaDepositManager` request builder. The builder checks capacity directly
   before `redeem` construction and raises structured unavailable data with
   requested and available **raw shares**. Do not calculate availability from a
   decimal conversion or allow a generic helper to submit an amount above the
   live maximum.

2. Determine cSuperior Quality Private Credit's deployed withdrawal semantics
   from its verified ABI/source. If it queues redemptions, create the matching
   ticket, pending/claimable status, manager-derived settlement/claim call and
   receipt analyser; do not advertise it as a synchronous cSigma redemption.

3. Add fork regressions for the limited csUSD fixture and the queued cSuperior
   lifecycle, covering exact raw-cap reporting and a successful valid-size
   request. Add no-RPC tests that retain both raw values through serialisation.

### Ember

1. Extend `EmberDepositManager.create_redemption_request()` to query and
   enforce the protocol minimum. A below-minimum request must return
   `VaultFlowUnavailable` with the minimum and requested raw shares, not a
   revert or receipt-analysis error.

2. Extend `EmberRedemptionTicket`/manager metadata with the terminal
   `redeemShares` request identity and a manager-derived operator settlement
   description. Reuse the existing `RequestRedeemed`/`RequestProcessed` parser;
   validate sequence, owner, receiver, raw shares and terminal status before
   producing an analysis. The executor must be able to locate the terminal
   request without knowing Ember calldata.

3. Keep the existing synchronous `VaultDeposit` analyser as the single source
   of deposit receipt semantics. Add focussed fork coverage for the
   below-minimum preflight, request ticket persistence, terminal settlement and
   a receipt whose standard ERC-4626 event is absent.

### Gains and Ostium

1. Add terminal request/ticket metadata to `GainsRedemptionTicket` and
   `OstiumRedemptionTicket`. Their managers expose `makeWithdrawRequest` and
   any claim/settlement action through the shared manager interface, including
   request id/epoch/settlement id, rather than leaving consumer code to infer
   it from a fixed selector list.

2. Preserve their distinct lifecycle semantics: Gains V1 epoch withdrawal and
   Ostium V1.5 request/settlement/claim must not share an invented universal
   `redeem` path. Confirm emitted events and return a typed unavailable result
   for a closed epoch or unavailable request operation.

3. Extend the existing `tests/vault/test_pending_vault_flow_events.py` and
   protocol fork tests to round-trip each ticket through persistence/restart,
   resolve the next manager-derived transaction and analyse the terminal
   receipt. Add a negative test proving neither manager needs an executor
   selector table.

### Yearn Arche USD and YieldNest RWA MAX

1. Inspect the verified proxy implementations and historical successful
   transaction receipts for the exact deployed event signatures. Add ABI event
   definitions under `eth_defi/abi/yearn/` and `eth_defi/abi/yieldnest/` from
   canonical sources; record the source URL alongside each ABI. Never hand
   construct a large inline ABI.

2. Implement/override the protocol analysers only where generic
   `Deposit`/`Withdraw` decoding cannot express the flow. They must validate
   emitting address and participant/amount fields, make raw-to-decimal
   conversion through `TokenDetails`, and return `DepositRedeemEventAnalysis`
   for a successful flow. Keep a standard ERC-4626 fallback only when its ABI
   contains the relevant event.

3. Add exact-fork regressions for Arche USD redemption and YieldNest RWA MAX
   deposit, plus unit tests for missing ABI events and wrong-address logs. A
   mined receipt with status one and no parsed matching event is a parser
   failure, not proof that the transaction failed.

### D2

1. Replace the public funding-window `ValueError` path in D2 pricing and
   estimation with structured deposit-unavailable data. Include a stable reason
   code, the current funding-window state and `next_open` as a naive UTC
   datetime when it can be read.

2. Do not silently swallow RPC/programming failures in
   `fetch_deposit_closed_reason()` or `fetch_deposit_next_open()`. Catch only
   expected contract-read failures, log enough context to diagnose them, and
   propagate unexpected failures.

3. Add a fixed Arbitrum fork test that exercises pricing/estimation while the
   window is closed and asserts the exact reason/next-open data before any
   transaction is built; add the matching open-window control test.

### Accountable / Hyperithm

1. Find custom error `0x5945ea56` in the verified Accountable implementation,
   proxy dependencies or protocol application ABI. Package the canonical error
   declaration and decode its arguments in the adapter's revert/preflight path.
   If it is an admission/cap/state failure that can be read before submission,
   expose a typed preflight reason as well; otherwise retain the decoded custom
   error as an authoritative reverted-transaction detail.

2. Do not alter Accountable's completed ERC-7540 request/claim state machine:
   its controller-aggregate pending/claimable semantics, partial fulfilment and
   repeated claims remain intact. Add the Hyperithm failing-vault regression to
   the existing Accountable fork coverage and test both the typed preflight and
   decoded-revert branches where chain state permits.

## Phase 3: integrate the consumer in trade-executor

Implement this phase in `trade-executor` after the relevant eth-defi release is
pinned. Keep it a separate PR or a clearly separated commit because it changes
order/execution accounting.

1. In `strategy/routing`/`vault_routing.py`, resolve the vault manager once
   and call `create_deposit_request()` or `create_redemption_request()` for
   synchronous as well as async flows. Use its availability/capacity check
   before pricing and again immediately before transaction construction. Map:

   - closed D2 funding windows to `deposit_closed` with next-open detail;
   - Upshift's missing implementation to `adapter_unsupported` with its
     protocol reason;
   - cSigma capacity to `redemption_capacity_limited` with requested and
     available raw shares; and
   - expected allow-list/admission restrictions to the existing whitelist or
     unavailable outcome, preserving structured details.

2. Persist the manager ticket and manager-derived next transaction after each
   request. Replace `get_swap_transactions()` fixed selector recognition with
   manager dispatch so Ember `redeemShares` and Gains
   `makeWithdrawRequest` reach the appropriate terminal state. When a full
   lifecycle has been requested, opt into Anvil settlement only if the exact
   manager ticket advertises a safe driver; otherwise emit
   `simulation_unsupported_async` with direction/ticket detail.

3. Make the cross-chain close use the actual settled redemption proceeds and
   executed share quantity. In the Aerodrome USDC, Autopilot USDC Morpho and
   Pharaoh USDC reproductions, calculate bridge-back size from terminal receipt
   analysis, not the requested estimate or pre-settlement share balance. Assert
   the resulting bridge transfer never exceeds the local token balance.

4. Enforce outcome precedence at the lifecycle boundary:

   1. a mined status-zero transaction is `transaction_reverted` with decoded
      revert detail;
   2. an expected preflight/availability/capacity result retains its typed
      outcome and skips execution;
   3. a status-one terminal receipt calls the manager analyser;
   4. an analyser failure is `receipt_analysis_failed` only when analysis is
      genuinely required and no earlier authoritative outcome exists; and
   5. unexpected program/RPC faults are `execution_failed` with preserved
      context.

5. Keep diagnostic positions with no trades out of long/short statistics (or
   make the statistics path represent them explicitly). Preserve the vault
   action outcome already recorded; statistics serialisation must never replace
   it. Add a focused no-trade diagnostic-position regression.

6. Rerun Arche USD and YieldNest RWA MAX through the shared manager conversion
   helper after their adapter fixes. Archive the machine-readable report
   alongside the PR and compare result counts with the prior 129-vault run;
   only the intended categories should change.

## Verification and release order

1. Complete the shared interface tests, then protocol-specific fork tests, in
   the `web3-ethereum-defi` worktree. Use the repository wrapper and target
   only the changed modules, for example:

   ```shell
   source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_upshift.py tests/erc_4626/vault_protocol/test_ember_deposit_redeem.py tests/erc_4626/vault_protocol/test_accountable.py -q
   source .local-test.env && poetry run pytest tests/vault/test_pending_vault_flow_events.py -k 'gains or ostium or ember' -q
   ```

   Add the exact cSigma, D2, Yearn and YieldNest modules to these commands as
   they are created. Run `poetry run ruff format` on changed Python files before
   review.

2. Release/pin the eth-defi revision only after every advertised capability has
   a complete request-to-terminal-receipt fork test. Update the executor's
   dependency pin, then run its targeted vault-test lifecycle tests and the
   five cross-chain close regressions.

3. Execute the full 129-vault rerun in request-only and full-lifecycle modes.
   Produce a protocol/outcome table and retain individual structured details.
   Expected remaining `deposit_closed` and whitelist results must remain
   visible; no known adapter limitation may be reported as an untyped Python
   exception.

4. Document each supported asynchronous lifecycle and its Anvil settlement
   limits in the relevant adapter README/API documentation. Update the
   trade-executor command documentation to define outcome names and to explain
   that a live-state result is not a permanent guarantee.

## Completion criteria

- Upshift accepts a discovered supported USDC asset through a tested
  multi-asset manager, or advertises a precise unsupported reason until it
  does.
- cSigma, Ember, Gains/Ostium, Yearn, YieldNest, D2 and Accountable each expose
  typed protocol behaviour at their public boundary; none leaks the report's
  known `NotImplementedError`/`ValueError`/missing-event failure shape.
- Every manager-advertised async ticket says whether it can safely settle on
  Anvil and provides its next transaction without executor selector knowledge.
- Terminal receipt analyses return correctly converted actual amounts, which
  are used for subsequent close/bridge accounting.
- The final simulation report classifies the named fixtures into their real
  state/adapter/outcome category, and the remaining receipt-analysis rows are
  independently reproducible parser failures.
