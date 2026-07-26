# Guarded vault deposit and redemption plan

## Goal

Make every publicly supported `VaultDepositManager` execute its deposit and
redemption lifecycle through deployed `SimpleVaultV0` and `GuardV0` contracts
on an Anvil fork.  A manager-generated call must not be considered supported
merely because Python can construct it, or because a direct EOA call succeeds.

Use C-Sigma as the first end-to-end prototype.  It is the smallest useful
case: a synchronous ERC-4626 `approve → deposit → redeem` lifecycle with a
protocol-specific capacity preflight, but no asynchronous settlement machinery.

The deliverable has four parts:

1. reusable guarded-execution test support which executes every manager call
   through `SimpleVaultV0.performCall()`;
2. deployed Solidity mocks for every distinct manager call surface, with a
   manager-to-mock coverage matrix;
3. narrowly scoped GuardV0 selectors and argument checks for all those call
   surfaces; and
4. focused live-fork tests and a refreshed status artefact which prove both
   directions where the manager advertises both directions.

This is security work.  A failed or unrecognised selector must remain a hard
failure.  Do not add a generic “any vault selector” bypass or weaken receiver,
controller, owner, token or approval-destination checks to make a test pass.

## Starting point

`eth_defi/erc_4626/deposit_probe.py` already deploys a `SimpleVaultV0`,
configures its `GuardV0`, performs manager-generated approval and deposit calls
through `performCall()`, and performs synchronous redemption similarly.  Its
asynchronous branch ends after the initial deposit request and records
`not_exercised_asynchronous`; it does not settle, claim or exercise the
redemption request.  Some focused protocol tests also construct calls and use
`guard.validateCall(...).call()`, which proves only a static validation result,
not the guarded execution path.

`GuardV0Base.whitelistERC4626()` currently registers a broad mixed collection
of standard, ERC-7540, Ember, Umami, Gains and Ostium selectors.  Its dispatcher
does not yet have safe, per-signature handling for every current manager.  In
particular, Nara’s `cooldownShares`/`unstake` and Upshift’s
`deposit(address,uint256,address)` need dedicated support.  ERC-7540’s three
address-bearing shapes need an explicit review instead of being routed through
the standard ERC-4626 receiver decoder.

`tests/erc_4626/vault_protocol/test_csigma.py` currently uses an independent
Anvil fork at block 21,900,000.  Migrate its mutating lifecycle test to the
shared Anvil pool, a fixed canonical block and snapshot/revert isolation before
adding the guarded variant.

## Scope and protocol inventory

“Functional” below means that the adapter publishes both directions, or that
the version-controlled guarded Anvil status artefact has a current successful
deposit plus completed synchronous redemption.  It is intentionally narrower
than “has an ERC-4626 ABI”.

### First wave: existing guarded success evidence

The current `eth_defi/data/deposit-status/vault-deposit-status.json` contains
successful guarded deposit and completed synchronous redemption evidence for:

| Protocol / adapter family | Flow shape | Guard surface |
| --- | --- | --- |
| AUTO Finance / AutoPool | synchronous | standard ERC-4626 |
| Dolomite | synchronous | standard ERC-4626 |
| generic ERC-4626 | synchronous | standard ERC-4626 |
| Euler / Euler Earn | synchronous | standard ERC-4626 |
| Fluid | synchronous | standard ERC-4626 |
| Gearbox | synchronous | standard ERC-4626 |
| Goat Protocol | synchronous | standard ERC-4626 |
| IPOR Fusion | synchronous | standard ERC-4626 |
| Kiln | synchronous | standard ERC-4626 |
| Morpho V1 / V2 | synchronous | standard ERC-4626 |
| Peapods | synchronous | standard ERC-4626 |
| Plutus legacy deployment | synchronous | standard ERC-4626 |
| Royco | synchronous | standard ERC-4626 |
| Silo Finance | synchronous | standard ERC-4626 |
| Superform | synchronous | standard ERC-4626 |
| Yearn V3 | synchronous | standard ERC-4626 |
| Yo | synchronous | standard ERC-4626 |
| Ostium V1.5 | asynchronous / asynchronous | request, claim, cancellation and reclaim selectors; current artefact has only the initial request |

C-Sigma is also a full synchronous public manager and is the prototype, but
is not presently represented by a completed row in that status snapshot.

### Second wave: advertised full managers lacking full guarded lifecycle proof

These are in scope once the common executor exists.  They must be guarded at
every manager-produced call, even when an external operator prevents a
full live-fork settlement.

| Protocol | Deposit | Redemption | Additional selector family |
| --- | --- | --- | --- |
| generic ERC-7540 | request then `deposit` claim | request then `redeem` claim | ERC-7540 |
| Lagoon | ERC-7540 request/claim | ERC-7540 request/claim | ERC-7540 plus existing Lagoon settlement |
| Accountable | standard ERC-4626 | ERC-7540 request then standard `redeem` claim | mixed standard/ERC-7540 |
| Ember | standard ERC-4626 | `approve` then `redeemShares` | Ember |
| Gains V1 | standard ERC-4626 | `makeWithdrawRequest`, later `redeem` | Gains V1 |
| Ostium V1.5 | `requestDeposit`, then `claimDeposit` | `requestWithdraw`, then `claimWithdraw` | Ostium V1.5 |
| Plutus Hedge V2 | standard ERC-4626 | `requestRedeem`, then `redeem` | ERC-7540 |
| Nara | standard ERC-4626 | `cooldownShares`, then `unstake(receiver)` | Nara |

### Explicitly partial or not-yet-certified work

Upshift multi-asset currently supports only
`deposit(asset, amount, receiver)` and must receive deposit-only GuardV0
coverage.  YieldNest likewise advertises deposit only.  Do not claim redemption
coverage for either until its adapter implements and tests a complete
redemption lifecycle.  D2, Forty Acres, Umami and any status row whose result
is only `funding_error`, `reverted`, `skipped` or `guard_validation_error` are
not added to the public full-support matrix merely because a manager class
exists; triage them after the above work.

## Design decisions

### Mock contract location and purpose

Create `contracts/guard/src/testing/Mock.sol`.  The requested logical path is
`guard/testing/Mock.sol`; placing it below `src/` is required by the existing
Foundry configuration (`src = "src"`) so `forge build` compiles it.  Do not
change the Foundry source root just to host test contracts.

Keep the file deliberately protocol-shaped, not a universal permissive mock.
It should contain a small mock ERC-20 plus one stateful mock per unique manager
surface.  Each mock must expose only the exact function overloads and events
used by that manager, record the decoded arguments, and enforce enough token
and share accounting to prove that the guarded call really reached the target.
The proposed contracts are:

| Mock contract | Covers | Required callable surface |
| --- | --- | --- |
| `MockERC20` | approvals and denomination/share transfers | mint, approve, transfer, transferFrom, allowance, balanceOf |
| `MockERC4626Vault` | C-Sigma prototype and all standard adapters | asset, deposit(uint256,address), withdraw(uint256,address,address), redeem(uint256,address,address) |
| `MockCsigmaV2Pool` | C-Sigma-specific negative path | standard ERC-4626 surface plus configurable immediate redemption capacity and `WithdrawalPending()` |
| `MockERC7540Vault` | generic ERC-7540, Lagoon, Plutus and Accountable request/claim shapes | requestDeposit, requestRedeem, deposit(uint256,address,address), redeem(uint256,address,address), request-state toggles |
| `MockEmberVault` | Ember | deposit, redeemShares(uint256,address) |
| `MockGainsV1Vault` | Gains V1 | deposit, makeWithdrawRequest(uint256,address), redeem |
| `MockOstiumV15Vault` | Ostium V1.5 | requestDeposit, claimDeposit, cancelRequestDeposit, reclaimDeposit, requestWithdraw, claimWithdraw, cancelRequestWithdraw, reclaimWithdraw |
| `MockNaraVault` | Nara | deposit, cooldownShares(uint256), unstake(address) |
| `MockUpshiftVault` | Upshift partial deposit support | deposit(address,uint256,address) |

Where two managers produce byte-for-byte identical calls, use the same mock
and separate Python/Solidity parameterised cases.  “A mock for every manager”
means every manager is named in the coverage matrix, not duplicated contracts
with the same attack surface.

Update the `guard` Makefile target to copy `Mock.json` artefacts required by
Python tests to `eth_defi/abi/guard/`; use unambiguous Solidity contract names
and matching ABI filenames.  Add a source comment explaining that these are
test-only guard targets and cannot be used as production protocol ABIs.

### Guard configuration API

Split configuration into composable, protocol-specific public whitelist
methods while retaining `whitelistERC4626(address,string)` as a backwards
compatible legacy convenience method:

- `_whitelistERC4626Core` / `whitelistERC4626Core` for standard
  ERC-20 approval, standard ERC-4626 deposit/withdraw/redeem and underlying
  asset/share-token registration;
- `whitelistERC7540` for request and claim selectors;
- `whitelistEmber`, `whitelistGainsV1`, `whitelistOstiumV15`,
  `whitelistNara` and `whitelistUpshift` for their respective selectors;
- keep `whitelistERC4626` as an explicitly documented compatibility wrapper
  composed from the existing permitted families until callers are migrated.

Each specialised method must register only the target selector set and the
approval destinations/assets it actually needs.  It must emit the existing
configuration events or new events added to the configuration scanner in the
same change.

### Dispatcher checks

Add selector constants, explicit branches and small decode helpers in
`GuardV0Base.sol`; never let an unknown selector reach a generic fallback.

- Standard ERC-4626: retain checks that deposit shares and redemption assets go
  only to `isAllowedReceiver` destinations.  For `withdraw` and `redeem`, also
  validate the `owner` argument as an allowed account: it identifies the share
  balance from which the target may pull under an allowance.  The test suite
  must mutate both the payout receiver and the share owner.
- ERC-7540: decode each signature independently.  Require the actual share
  receiver, controller and owner addresses that control or receive funds to be
  permitted as appropriate to its semantics: `requestDeposit` and
  `requestRedeem` validate controller plus owner; `deposit` and `redeem` claim
  calls validate receiver plus controller; apply the same mapping to the
  `requestWithdraw` form.  The test suite must prove that changing *each*
  sensitive argument to an unapproved address reverts.  Do not continue to
  label the second argument of every three-address call a “receiver” without
  checking the relevant ABI.
- Ember: retain `redeemShares(shares, receiver)` receiver validation and prove
  the preceding share-token approval has the vault as an allowed approval
  destination.
- Gains V1: validate `makeWithdrawRequest(shares, receiver)` receiver.
  Ostium V1.5’s no-address request/claim/cancel/reclaim calls need only the
  call-site restriction because they act on `msg.sender`.
- Nara: allow `cooldownShares(uint256)` only at an approved Nara vault and
  decode `unstake(address)` to require an allowed payout receiver.
- Upshift: decode `deposit(address asset,uint256 amount,address receiver)`;
  require both an allowed asset and allowed receiver.  Its whitelist method
  must explicitly add every accepted asset and the vault as approval
  destination.  This is deposit-only until Upshift redemption exists.

Document each selector with its full signature beside its four-byte constant.
Update `contracts/guard/script/ComputeSelectors.s.sol` and add selector-value
assertions to prevent accidental signature drift.

## Implementation sequence

### 1. Establish the mock and Solidity security suite

1. Add `contracts/guard/src/testing/Mock.sol` with the contracts above.
2. Add Foundry tests under `contracts/guard/test/` which deploy `GuardV0`, set
   a non-governance allowed sender, deploy the relevant mock and configure only
   the protocol-specific whitelist method.
3. For every matrix row, prove both the accepted manager call and an adversarial
   address mutation.  The adverse cases include an unapproved approval spender,
   an unapproved deposit-share receiver, an unapproved redemption receiver,
   an unapproved standard-ERC-4626 share owner, ERC-7540 controller/owner
   substitutions, Nara unstake receiver substitution, and Upshift
   asset/receiver substitution.  Also prove that every unregistered manager
   selector, including `multicall` and ERC-20 allowance variants not emitted by
   a supported manager, remains rejected.
4. Make accepted cases call `SimpleVaultV0.performCall`, not only
   `GuardV0.validateCall`.  Assert the mock state changed, proving the guard
   and the downstream target both ran.  Retain `validateCall` unit tests only
   for clear revert diagnostics.
5. Run `make guard` and `forge test` in `contracts/guard` before touching live
   adapters.  Add copied mock ABIs only when a Python test deploys them.

### 2. C-Sigma prototype

1. Add a C-Sigma guard test that deploys `SimpleVaultV0`, calls
   `whitelistERC4626Core` (or the compatibility wrapper during the transition),
   funds the simple vault, then executes manager-generated token approval,
   `deposit(uint256,address)` and `redeem(uint256,address,address)` exclusively
   through `performCall`.
2. Assert the exact manager-selected receiver and owner are the simple vault,
   the mock/real vault minted shares to it, redemption burned them, and the
   denomination-token balance returned to it.  Also prove that replacing the
   deposit receiver, redemption receiver or redemption share owner with an
   outsider reverts in GuardV0 before target execution.
3. Preserve C-Sigma’s `WithdrawalPending` capacity preflight as a no-broadcast
   test.  It is not a GuardV0 failure: the guard test covers only an immediate
   successful redemption, while the adapter test proves an over-capacity request
   never reaches the guard or RPC broadcast.
4. Convert C-Sigma’s mutating fork lifecycle to `AnvilForkPool`, the matching
   `ETHEREUM_MIDNIGHT_BLOCK`, a `fork:ethereum:midnight` xdist group and
   `evm_snapshot_revert` isolation.  Keep the known block-21,900,000 values
   only if that is the one shared fixed block selected for the suite; otherwise
   re-baseline exact values at the canonical block.  Do not retain the current
   private `fork_network_anvil` fixture.

### 3. Generalise guarded manager execution

1. Extract a test-only helper from `deposit_probe.py` (or a new module below
   `eth_defi/testing/`) that accepts a manager request, encodes each function,
   executes every call through `SimpleVaultV0.performCall`, checks every receipt
   and returns the transaction hashes for the manager parser.  It must reject a
   direct EOA target call in this path.
2. Extend the helper to run the full state machine:

   - approval(s) → deposit request → parse ticket;
   - if asynchronous, `force_settle(ticket)` only when supported, assert the
     required terminal/claimable state, then execute manager-produced claim or
     completion call through the simple vault;
   - mint or obtain shares, then approval(s) → redemption request → parse;
   - settle and execute the final manager-produced redemption claim where the
     protocol supports it; analyse receipts and assert balances.

   For ERC-7540, the mock and live tests must additionally assert that the
   request parser binds the ticket's controller/owner to the event and that the
   later guarded claim is made by the same `SimpleVaultV0` account.  GuardV0 is
   stateless across separate transactions; the target vault's request
   accounting and this ticket-level assertion provide the required
   request-to-claim binding rather than attempting unsafe GuardV0 storage.

3. For an advertised asynchronous manager without a safe Anvil settlement
   driver, do not silently omit the post-request guard checks.  Exercise the
   request and every independently constructible finish/reclaim/cancel function
   against the deployed mock suite; on a live fork record the explicit
   `UnsupportedVaultSimulation` reason.  Promote it to complete live guarded
   lifecycle evidence only after a ticket reaches the manager’s valid terminal
   state.
4. Replace the probe’s asynchronous `not_exercised_asynchronous` success with
   an explicit state: either `completed` after the guarded lifecycle, or an
   evidence-rich `simulation_unsupported_async`.  A request-only result must
   not be counted as a two-way success.

### 4. Roll out selector families and live protocol tests

Work in the following order, adding the mock case, Foundry positive/negative
tests, and at least one focused guarded Anvil test before moving to the next
family:

1. standard ERC-4626: all first-wave synchronous protocols and C-Sigma;
2. ERC-7540: generic ERC-7540, Lagoon, Accountable and Plutus Hedge;
3. Ember;
4. Gains V1 and Ostium V1.5;
5. Nara;
6. Upshift deposit-only.

For each live-fork test use the repository’s shared fixed-block Anvil pool,
matching `xdist_group` and snapshot/revert isolation for mutations.  Keep
tests focused on one representative real deployment per manager class;
the Solidity mocks cover the security properties of every selector and every
receiver/controller position without relying on provider state.

### 5. Update configuration consumers and documentation

1. Update `eth_defi/erc_4626/vault_protocol/lagoon/config_event_scanner.py`
   for any new GuardV0 configuration events and selectors.
2. Update Guard README selector/configuration tables and the deposit-status
   README so “success” explicitly means both directions completed through
   `SimpleVaultV0`/`GuardV0`, or records the exact directional limitation.
3. Add manager docstring links to the guarded flow test that protects its
   selector family.  Keep partial managers plainly marked as deposit-only.

## Verification and acceptance criteria

- `make guard` succeeds and produces the needed Guard and mock ABI artefacts.
- Focused Forge tests pass for every mock row, with both positive execution and
  negative receiver/controller/asset/spender assertions.
- C-Sigma passes both its reserve-capacity adapter tests and its full guarded
  deposit/redemption fork test.
- Each full-capability manager has one focused guarded live-fork lifecycle, or
  a recorded address-specific unsupported settlement reason plus complete mock
  selector coverage.  No test marks GuardV0 validation as skipped.
- Every manager-generated transaction in these tests is sent through
  `SimpleVaultV0.performCall`; direct calls are used only to arrange fork state
  (funding, configured operator settlement) and are visibly separate.
- The updated status artefact contains `completed` only for full guarded
  lifecycles, contains a positive fixed `fork_block_number`, and has no new
  request-only async result reported as success.
- Run the smallest focused pytest modules with `.local-test.env` and the
  required three-minute timeout, then run the relevant Guard and vault-protocol
  subsets.  Format Python with `poetry run ruff format` and Solidity with the
  repository’s Foundry formatter before review.

## Non-goals

- This plan does not invent Upshift, YieldNest, D2, Forty Acres or Umami
  redemption behaviour that their adapters do not yet implement.
- It does not make a protocol operator or offchain queue settle a live vault.
  Anvil impersonation remains test-only and must be explicitly disclosed by the
  manager’s settlement capability.
- It does not replace the Safe/`TradingStrategyModuleV0` production route.  The
  `SimpleVaultV0` route is the compact GuardV0 execution harness; Safe-module
  coverage remains a separate integration layer.
