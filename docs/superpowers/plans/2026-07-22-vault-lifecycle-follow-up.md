# Vault lifecycle follow-up plan

**Date:** 2026-07-22

**Source:** [PR #1347 follow-up implementation hand-off](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1347#issuecomment-5045700241)

**Status:** cSigma implementation completed. PR #1347 supplied the shared
`force_settle()` API, structured receipt-failure fields and the Lagoon Anvil
settlement driver. The remaining protocol reports require reproducible evidence:
any symptom that cannot be reproduced is treated as an unrelated problem and
does not receive an adapter change.

## Implementation record

The cSigma V2 pool at Ethereum block `21_900_000` accepted a 100 USDC deposit
and a full subsequent redemption through the manager. Its `maxDeposit(owner)`
and `maxRedeem(owner)` views are respectively expressed in raw assets and raw
shares. The contract rejects a request above either value with its corresponding
ERC-4626 `more than max` error, so the specialised manager preflights those
exact conditions and raises `VaultFlowUnavailable` before broadcast.

Fresh manager paths for the remaining hand-off reports are left unchanged unless
their stated symptom reproduces under the evidence policy above. This prevents a
historical receipt, permission or admission issue from being represented as a
current protocol integration behaviour.

## Goal

Complete the remaining cSigma, Yearn/Arche USD, YieldNest, IPOR Fusion and
Lagoon lifecycle gaps through `VaultDepositManager`. For each reproducible gap,
prove one representative successful and failed deposit and one representative
successful and failed redemption without implying support for every vault,
asset, queue, epoch or deployment generation. A clean reproduction closes the
reported gap without speculative code.

The lifecycle test budget is a hard ceiling of four scenarios per protocol:

1. one successful deposit;
2. one failed deposit;
3. one successful redemption; and
4. one failed redemption.

Do not add another lifecycle scenario for a second asset, deployment, caller,
capacity boundary, lock state, event variant or ordinary-protocol regression.
Small no-RPC unit tests for shared error fields and static capability metadata
do not count as lifecycle scenarios, but must not perform vault transactions.

## Scope boundaries

- Keep `VaultDepositManager` as the only caller-facing transaction boundary.
- Successful synchronous flows call `force_settle(None)`. Asynchronous Lagoon
  flows pass the request ticket and verify the request becomes claimable.
- Never silently clip a deposit, redemption or share amount to live capacity.
- Use an observed protocol admission, capacity or lock failure for negative
  coverage. Do not manufacture a negative path, and do not use a missing
  approval or an empty wallet unless that is the protocol failure under test.
- Do not impersonate a protocol administrator or mutate protocol configuration
  merely to create a negative scenario. Anvil time travel to an ordinary
  protocol-defined unlock time and impersonation of existing token or share
  holders are acceptable fixture setup.
- Preserve ordinary Yearn V3 behaviour while adding the Arche USD receipt
  variant.
- Reuse Lagoon's existing `ERC7540DepositManager.force_settle()` implementation;
  do not add a second settlement driver.
- Do not implement partial settlement, queue iteration, cancellation, reclaim,
  alternative assets, private-vault scheduling or deployment matrices.
- Reuse one setup across a protocol's four scenarios wherever isolation permits.
  Do not parameterise the lifecycle tests into hidden asset, deployment or state
  matrices.
- Treat explorer-verified or application-exported ABIs as canonical. Add an ABI
  file under `eth_defi/abi/<protocol>/` when the adapter needs more than one or
  two stable function or error fragments.

## Evidence and assumption policy

Do not wait for clarification or historical coverage-run artefacts. Use the
named vault addresses, current repository fixtures, canonical contract ABIs and
fresh transactions on deterministic forks. Where the hand-off does not name a
representative deployment, select one from the repository's current
classification or protocol metadata.

If a correctly funded, approved and constructed manager call does not reproduce
the reported symptom, record the result and leave that adapter unchanged. Do
not add speculative event signatures, hardcoded error meanings or protocol
branches for a historical failure that cannot be observed.

For each workstream:

1. Pin a fork block on the relevant chain that preserves the implementation,
   admission state, capacity and liquidity.
2. Record the proxy implementation and canonical ABI source next to any new ABI.
3. Exercise the current manager with a fresh transaction before adding an
   override.
4. Identify a deterministic denomination-token holder and fund a fresh test
   account or `SimpleVaultV0` on the fork.
5. Add a regression test only after the expected protocol behaviour is
   observable and confirm it is not an approval, funding, RPC or proxy-ABI
   issue.
6. Use fixed absolute balance, share and receipt assertions at the chosen fork
   block.

## Shared diagnostic contract

Add the smallest common preflight error surface needed by the reproducibly
affected adapters in `eth_defi/vault/deposit_redeem.py` before implementing
protocol-specific checks.

- Add a neutral `VaultFlowError` base exception. Keep
  `VaultTransactionFailed` for failures with a broadcast transaction, reparent
  it under `VaultFlowError`, and derive the new sibling
  `VaultFlowUnavailable` from `VaultFlowError` for failures discovered before
  broadcast.
- Store the protocol, vault address, caller, direction, lifecycle phase,
  human-readable reason, decoded custom error and raw revert data when
  available. Capacity-based failures also expose the requested and available
  raw amounts without modifying either.
- Keep mined receipt diagnostics unchanged unless an analyser can populate a
  newly introduced field from actual provider data.
- Keep post-transaction analysis as a return value and preflight rejection as
  an exception; do not create two competing representations for the same
  phase.
- Add no-RPC unit tests for field preservation and string formatting.
- Audit repository catch sites so retry or receipt-handling code cannot mistake
  a preflight rejection for a mined transaction failure.

Protocol managers should translate only known protocol conditions to
`VaultFlowUnavailable`. Unexpected RPC, ABI and decoding errors must continue
to fail loudly rather than being reported as an ordinary closed vault.

`get_deposit_manager_capability()` remains static metadata about implemented
lifecycle support. `can_create_deposit_request(owner)` and
`can_create_redemption_request(owner)` are coarse live-state advisories. When a
reliable caller-specific view is found, use the same condition in the relevant
`can_create_*` method and amount-aware `create_*_request()` preflight; the latter
is authoritative and callers must still handle inclusion-time reverts.

## 1. cSigma redemption capacity

### Reconnaissance

- Reproduce redemption against
  `0x438982ea288763370946625fd76c2508ee1fb229` on a fixed Ethereum fork.
- Resolve the proxy implementation and verify whether the existing
  `CsigmaV3Pool.json` ABI matches this V2-labelled deployment.
- Compare `maxRedeem(owner)`, `maxWithdraw(owner)`, `previewRedeem(shares)`,
  reserve balances and any protocol-specific liquidity view at the same block.
- Establish whether capacity is expressed in shares or denomination assets and
  whether it changes after an immediate redemption.
- Identify a deterministic protocol-level deposit rejection at the selected
  block. If none is observable without administrative state mutation, omit the
  failed-deposit scenario and record the historical request as unrelated.

### Implementation

- Add explicit capacity accessors to `CsigmaVault` with documented units.
- Keep the shared `ERC4626DepositManager` only if its existing request path can
  enforce the verified capacity and raise `VaultFlowUnavailable` with cSigma
  context.
- Otherwise add
  `eth_defi/erc_4626/vault_protocol/csigma/deposit_redeem.py` containing
  `CsigmaDepositManager`, derived from `ERC4626DepositManager`.
- In `create_redemption_request()`, compare the request directly in the unit the
  contract natively enforces. Permit equality and reject values above capacity;
  do not introduce a rounding-sensitive shares-to-assets comparison.
- Override `can_create_redemption_request(owner)` to report whether the owner
  has any live redeemable capacity. The static manager capability continues to
  describe implemented lifecycle support rather than current vault state.
- Leave the standard synchronous deposit, transaction construction, receipt
  analysis and `force_settle(None)` behaviour inherited unless reproduction
  proves a cSigma-specific difference.
- Wire the selected manager and a two-way synchronous capability through
  `CsigmaVault` only after the four fork scenarios pass.
- Add **Supported simulation path** and **Known limitations** to the manager
  docstring, explicitly excluding FIFO queue processing, reserve replenishment,
  partial redemption and repeated claims.

### Focused tests

Extend `tests/erc_4626/vault_protocol/test_csigma.py` with one lifecycle fixture
at the selected block:

- successful deposit with exact assets and minted shares;
- representative protocol-level deposit rejection, if one is reproducible;
- successful redemption at or below capacity; and
- typed redemption rejection above capacity or at zero capacity, asserting the
  owner's complete share balance is unchanged.

## 2. Yearn / Arche USD receipt analysis

### Reconnaissance

- Execute a fresh successful Arche USD deposit and redemption for
  `0x33ffc177a7278ff84aab314a036bc7b799b7cc15` on a deterministic fork and
  decode every vault, wrapper, strategy and token log in the resulting receipt.
- Determine whether the transaction calls the Yearn vault directly or through
  a wrapper, which contract emits the authoritative execution event, and which
  balance movements establish executed assets and burned shares.
- Compare the fallback against the existing ordinary Yearn analyser and current
  fixtures without adding another ordinary-Yearn lifecycle scenario.
- If the generic analyser handles the fresh Arche receipt, add only regression
  coverage and treat the historical missing-event report as unrelated.
- Identify protocol-level deposit and redemption rejections at the same
  deployment. Make either negative test conditional when no such state is
  observable without administrative mutation.

### Implementation

- Add `eth_defi/erc_4626/vault_protocol/yearn/deposit_redeem.py` only if the
  receipt cannot be represented by a narrowly scoped pure analyser helper.
- Prefer a `YearnV3DepositManager` derived from `ERC4626DepositManager` that
  delegates standard receipts to the existing analyser and activates the
  Arche fallback only when a verified emitter, event signature and deployment
  shape match.
- Validate the vault or wrapper emitter, owner/receiver topics and denomination
  and share-token balance deltas. Do not accept a same-signature `Withdraw`
  emitted by an underlying strategy.
- Return the same `DepositRedeemEventAnalysis` shape as the generic manager.
- Keep Yearn's synchronous request construction, estimates and
  `force_settle(None)` behaviour unchanged.
- Preserve the current public Yearn capability and extend the existing
  `object.__new__(YearnV3Vault)` no-RPC pattern in
  `tests/erc_4626/test_deposit_probe.py` to assert that ordinary Yearn instances
  still expose synchronous deposit and redemption support.
- Document the verified Arche route and exclude other Yearn generations,
  withdrawal overloads, custom queues and nested wrappers from the support
  claim.

### Focused tests

Add `tests/erc_4626/vault_protocol/test_yearn_arche.py` rather than overloading
the current Arbitrum symbol test:

- successful deposit and one reproducible protocol rejection, when available;
- successful redemption with exact decoded assets, shares and post-transaction
  balance deltas;
- one reproducible redemption rejection, when available.

## 3. YieldNest RWA MAX

### Reconnaissance

- Execute and decode a fresh successful deposit for ynRWAx at
  `0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8` on a deterministic fork.
- Verify the deployed implementation ABI, authoritative deposit event and any
  wrapper involved. The current trimmed `yieldnest/Vault.json` does not contain
  events, so do not infer the event layout from ERC-4626 alone.
- Identify the contract view that distinguishes an account lock from an
  insufficient immediate-withdrawal buffer. Verify whether `maxRedeem(owner)`
  or `maxWithdraw(owner)` already incorporates both conditions.
- Confirm that the selected fixed fork block permits one immediate-buffer
  redemption even though the product has a separate published maturity date.
- If the generic analyser handles the fresh receipt, retain it and limit the
  change to verified lock/buffer handling and lifecycle coverage.
- Identify a protocol-level failed deposit at the selected block. If none is
  observable without administrative mutation, omit that negative scenario.
- Determine whether a successful deposit locks the depositor. If it does, use a
  separate existing unlocked share holder for immediate-redemption success, or
  advance Anvil only through the contract's ordinary unlock time. Use the newly
  locked depositor for the premature-redemption failure.

### Implementation

- Add
  `eth_defi/erc_4626/vault_protocol/yieldnest/deposit_redeem.py` containing a
  synchronous `YieldNestDepositManager` derived from
  `ERC4626DepositManager`.
- Override deposit analysis only for the verified ynRWAx event or wrapper and
  retain strict emitter, owner, asset and share validation.
- If reconnaissance finds a reliable deposit admission view, use it in both
  `can_create_deposit_request(owner)` and `create_deposit_request()`; otherwise
  do not add a speculative deposit preflight.
- Add explicit lock and immediate-buffer accessors with documented raw units to
  `YieldNestVault`.
- Preflight redemption in `create_redemption_request()` and raise
  `VaultFlowUnavailable` separately for a locked account and insufficient
  immediate capacity. Never route into the queued-withdrawal path and never
  reduce the requested amount to the available buffer.
- Override `can_create_redemption_request(owner)` with the same lock and
  immediate-capacity reads. It may report whether some redemption is currently
  possible; the amount-aware request builder remains authoritative.
- Wire the specialised manager and synchronous two-way capability through
  `YieldNestVault` after the fork lifecycle passes.
- Document support for the observed ynRWAx deposit and immediate-buffer
  redemption only. List queued withdrawals, maturity variants,
  cancellation/reclaim and other deployments as limitations.

### Focused tests

Extend `tests/erc_4626/vault_protocol/test_yieldnest.py` at a deterministic
pre-maturity fork block:

- successful deposit with strict receipt and balance assertions;
- reproducible paused, ineligible or other protocol-level failed deposit, when
  available;
- successful immediate-buffer redemption; and
- locked or insufficient-buffer typed rejection with shares unchanged.

## 4. IPOR Fusion access and redemption delay

### Reconnaissance

- Use the existing bdUSD Ethereum fixture as the public Fusion vault. Select a
  private Ethereum Fusion vault from current classified IPOR deployments whose
  access manager denies the same caller at a common usable block.
- Use the same deployed `SimpleVaultV0` address as the caller for public and
  private checks. The `AccessManager.canCall()` caller is the contract that
  invokes `deposit()`, not its controlling EOA.
- Verify `canCall(caller, vault, deposit(uint256,address))` returns immediate
  access for the public vault and denied or delayed access for the private
  vault.
- After the public deposit, read `getAccountLockTime(caller)` and the block
  timestamp to establish the exact ordinary redemption delay.
- If no current classified private vault denies the caller, retain the public
  lifecycle regression and treat the historical private-vault report as
  unrelated rather than hardcoding an unknown deployment.

### Implementation

- Add `eth_defi/erc_4626/vault_protocol/ipor/deposit_redeem.py` containing
  `IPORDepositManager`, derived from `ERC4626DepositManager`.
- Add a caller-specific deposit admission helper to `IPORVault` using the
  bundled `AccessManager.json` ABI and the exact `deposit(uint256,address)`
  selector.
- In `create_deposit_request()`, permit only immediate access. Raise
  `VaultFlowUnavailable` for denied or scheduled access and include the caller,
  selector, decoded `AccessManagedUnauthorized(address)` where reproduced and
  raw revert data when available.
- Override `can_create_deposit_request(owner)` with the same caller-specific
  `canCall()` result. Keep `get_deposit_manager_capability()` static: it states
  that the library implements the synchronous lifecycle, not that every live
  vault admits every caller.
- In `create_redemption_request()`, compare the access manager's account unlock
  timestamp with the fork block timestamp. Raise a typed premature-redemption
  failure that includes the unlock time; otherwise delegate to the generic
  synchronous redeem path.
- Override `can_create_redemption_request(owner)` with the same account-lock
  timestamp check.
- Do not advance time in production-facing code. The test may advance Anvil to
  the ordinary unlock timestamp and then build the redemption normally.
- Return `IPORDepositManager` from `IPORVault`, replace the generic-manager
  allow-list dependency with an explicit synchronous capability override, and
  retain `force_settle(None)` as the inherited Anvil-validated no-op.
- Document the public immediate-deposit route and ordinary account delay. List
  scheduled private access, alternate selectors and deployment generations as
  unsupported.

### Focused tests

Expand `tests/erc_4626/vault_protocol/test_ipor.py` with an Anvil fork and a
`SimpleVaultV0` caller:

- successful public-vault deposit;
- typed private-vault deposit denial for the same caller;
- successful public-vault redemption after advancing to the onchain unlock
  timestamp; and
- premature redemption rejection before advancing time, with shares unchanged.

Assert both decoded and raw failure context where the RPC supplies raw revert
data.

## 5. Lagoon admission diagnostics

### Reconnaissance

- Exercise `requestDeposit()` on the currently supported Lagoon fixtures from a
  funded and approved caller so approval or balance failures cannot mask an
  admission condition.
- Inspect any reproducible admission revert, decode its exact custom error
  selector or payload and locate the corresponding contract condition.
- Check whether a reproduced condition has a cheap, reliable view that agrees
  with the transaction at the same block and for the same caller.
- No `XJy8` vault, chain or revert payload is present in the repository or
  hand-off. If current supported fixtures do not reproduce it, classify that
  historical report as unrelated and make no `XJy8`-specific code change.

### Implementation

- Keep `ERC7540DepositManager.force_settle()` unchanged.
- If a reproducible condition has a reliable view, add it beside
  `_is_vault_paused()` and use it in
  both `can_create_deposit_request()` and `create_deposit_request()`.
- If a reproduced condition has no reliable view, perform a caller-specific
  `eth_call` preflight of the exact `requestDeposit()` calldata after ordinary
  amount validation and translate only the verified payload to
  `VaultFlowUnavailable`.
- Preserve the vault, caller, deposit direction, request phase, decoded reason
  and raw payload. Unknown errors continue to propagate.
- Reuse the current public 722 Capital Base deposit and redemption lifecycle,
  including ticket-driven `force_settle()`. Retain the existing tampered-
  receiver redemption rejection as the representative failed redemption, but
  update it to assert structured manager context if the shared error surface
  reaches that phase.
- Expand the Lagoon manager docstring only when a verified admission rule is
  added. Otherwise retain its current supported path and limitations.

### Focused tests

- Keep the public happy deposit and successful redemption in
  `tests/lagoon/test_erc_7540_deposit_redeem.py`.
- Add a fixed-fork typed failed-deposit test only for an admission condition
  reproduced on a current supported fixture.
- Keep one failed redemption only; do not duplicate the existing
  tampered-receiver scenario across Lagoon deployments.

## Verification matrix

This table is the complete protocol lifecycle test budget, not a starting point
for parameterisation or additional variants.

| Protocol | Successful deposit | Failed deposit | Successful redemption | Failed redemption |
|---|---|---|---|---|
| cSigma | Immediate ERC-4626 route | Reproducible protocol admission failure, otherwise no new case | At or below live capacity | Above or at zero capacity; shares unchanged |
| Yearn / Arche | Verified Arche route | Reproducible protocol rejection, otherwise no new case | Arche wrapper/event route | Reproducible rejection, otherwise no new case |
| YieldNest | Verified ynRWAx route | Reproducible protocol rejection, otherwise no new case | Immediate buffer using an unlocked holder or ordinary time advance | Locked or insufficient buffer; shares unchanged |
| IPOR Fusion | Public vault, `SimpleVaultV0` caller | Private vault with the same caller, otherwise no new case | After ordinary account delay | Before unlock; shares unchanged |
| Lagoon | Existing public async route | Reproducible admission condition, otherwise no new case | Existing ticket settlement and claim | Existing tampered-receiver rejection |

Every successful row must assert the manager capability, request type,
`force_settle()` result, executed denomination amount, executed share amount and
final balances. Every reproducible failed row must assert the typed or
structured failure fields and that no unintended token or share balance
changed.

Run only the affected modules, always through the repository environment:

```shell
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_csigma.py -v
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_yearn_arche.py -v
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_yieldnest.py -v
source .local-test.env && poetry run pytest tests/erc_4626/vault_protocol/test_ipor.py -v
source .local-test.env && poetry run pytest tests/lagoon/test_erc_7540_deposit_redeem.py tests/lagoon/test_lagoon_erc_7540.py -v
```

Format changed Python files with `poetry run ruff format` and add no-RPC unit
coverage for capability and error-schema changes.

## Documentation changes

- Add **Supported simulation path** and **Known limitations** to every new or
  changed protocol manager docstring.
- Add each new public `deposit_redeem` module to its protocol autosummary under
  `docs/source/vaults/`.
- Add or update the corresponding API stub under `docs/source/api/` and its
  parent table of contents.
- Record canonical ABI and implementation links in the relevant
  `eth_defi/abi/<protocol>/README.md` when an ABI changes.
- Describe only the tested asset, deployment and lifecycle route as supported.
- State that a live-state preflight can become stale before transaction
  inclusion and does not replace handling an onchain revert.
- If an implementation PR uses a `feat:` title, add its dated feature entry to
  `CHANGELOG.md` as required by the repository PR instructions.

## Delivery order

Keep the changes independently reviewable:

1. Shared typed preflight diagnostics and no-RPC tests.
2. cSigma capacity and lifecycle coverage.
3. Yearn/Arche receipt analysis and its four bounded lifecycle scenarios.
4. YieldNest receipt analysis, lock/buffer preflight and lifecycle coverage.
5. IPOR caller-specific access and delayed-redemption coverage.
6. Lagoon admission reconnaissance; add diagnostics only for a reproduced
   condition and otherwise close the historical `XJy8` report as unrelated.

The five protocol branches may be developed independently after the shared
diagnostic contract lands. Each protocol change should contain its own focused
tests and documentation so it can be reviewed or reverted without affecting
the others.

## Completion criteria

- All five protocols expose the selected path through `VaultDepositManager`.
- All successful synchronous paths invoke `force_settle(None)`; Lagoon passes a
  ticket and proves it becomes claimable.
- Each reproducible protocol lifecycle has at most one successful and one
  failed scenario per direction: four lifecycle scenarios total. Missing
  historical evidence does not justify a substitute scenario, and no queue,
  asset, deployment, caller or state matrix is permitted.
- Capacity, buffer and access checks reject rather than clipping or silently
  changing the request.
- Receipt analysis returns positive executed assets and shares from verified
  events and balance movements.
- Failures preserve vault, caller, direction, phase and decoded/raw reason when
  available.
- Manager capabilities are advertised only after guarded fork lifecycles pass.
- Manager and API documentation state the tested route and its limitations.
