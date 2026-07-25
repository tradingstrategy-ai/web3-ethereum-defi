# Plan: close the 21 remaining vault deposit/redemption simulation gaps

Date: 2026-07-25
Branch: `vault-deposit-redemption-simulation-fix` (based on master `b42ef5747`, PR #1368)

## Background and evidence

trade-executor PR [#1578](https://github.com/tradingstrategy-ai/trade-executor/pull/1578)
re-ran the 129-vault cross-chain `--auto-simulated --settle-async-on-anvil`
matrix against eth-defi master `b42ef5747` (#1368 "close vault simulation
adapter gaps"). 43 vaults complete the full lifecycle; **21 vaults still have
adapter/lifecycle gaps in this repository** (same count as before #1368 —
typing improved, but the concrete simulation paths did not close).

Evidence:

- The eth-defi work-item comment:
  <https://github.com/tradingstrategy-ai/trade-executor/pull/1578#issuecomment-5077668728>
- Full report: `docs/reports/cross-chain-vault-test-eth-defi.md` on the
  trade-executor branch `worktree-xchain-vault-eth-defi-1368`, with
  machine-readable results in
  `docs/reports/cross-chain-vault-test-2026-07-25.report.json`.

All root causes below were confirmed by code inspection of this repository at
`b42ef5747`; custom-error selectors were resolved against the verified
implementation sources on the relevant explorers.

## Framework recap (shared infrastructure)

- Manager base and typed results: `eth_defi/vault/deposit_redeem.py` —
  `VaultDepositManager` (line 685), `force_settle()` base semantics (709–737),
  `VaultForcedSettlementResult` (451), `VaultDepositManagerCapability` (51,
  `supports_anvil_settlement` at 83, validated at 105),
  `RedemptionTicket` (421), `AsyncVaultRequestStatus` (191).
- Typed exceptions: `VaultFlowError` (213) with structured fields
  (`requested_raw_amount`, `available_raw_amount`, `minimum_raw_amount`,
  `error_selector`, `decoded_error`, …), `VaultFlowUnavailable` (333),
  `VaultTransactionFailed` (329), `UnsupportedVaultSimulation` (337).
- Generic sync flow: `eth_defi/erc_4626/flow.py` — `deposit_4626()` with the
  raw `maxDeposit` assert at lines 105–108, `redeem_4626()` with the raw
  `maxRedeem` assert at line 251 and the silent `maxRedeem == 0` skip at
  243–244.
- Canonical Anvil settlement driver pattern:
  `LagoonDepositManager.force_settle()`
  (`eth_defi/erc_4626/vault_protocol/lagoon/deposit_redeem.py:65–122`) —
  Anvil guard, impersonation via `make_anvil_custom_rpc_request`, status
  before/after, `VaultForcedSettlementResult`.
- Reference lifecycle test pattern: `tests/lagoon/test_erc_7540_deposit_redeem.py`
  (fork at fixed block, whale-funded user, request → pending →
  `force_settle` → claimable → claim → analyse, absolute-value asserts).
- **Whitelisting preflight (shared).** `WhitelistingRequired(VaultFlowUnavailable)`
  (`eth_defi/vault/deposit_redeem.py`) and the reusable
  `VaultDepositManager.check_deposit_whitelist(owner)` helper raise a typed,
  distinguishable error when a vault's deposit whitelist is applicable and
  queryable but excludes the owner. It is wired into the generic ERC-4626 and
  ERC-7540 deposit preflights, and Lagoon's access-policy denial now raises
  it. Workstreams whose gap is an allow-list / role denial (e.g. Accountable
  `DepositNotAllowed`, any protocol whitelist) should raise
  `WhitelistingRequired` (or call the helper) rather than a generic
  `VaultFlowUnavailable`, keeping the "needs whitelisting" state separable
  from other preflight refusals. Adapters that cannot determine the policy
  must not raise it (the helper is a no-op when the whitelist reads raise
  `NotImplementedError`).

## Implementation status (2026-07-25)

| Workstream | Status | Notes |
|---|---|---|
| Whitelisting infra (`WhitelistingRequired`) | **Done** | Exception + `check_deposit_whitelist` helper wired into generic ERC-4626/ERC-7540 preflights + Lagoon; unit-tested. |
| WS0 generic flow hardening | **Done** | Typed `VaultFlowUnavailable` for maxDeposit/redeem/balance asserts; `maxRedeem==0` opt-in raise (conservative default); `fetch_depositable_raw_assets` hook. Unit + fork tests green (d2, cSigma, IPOR, probe, generic 4626). |
| WS2 Ember `force_settle` | **Done** | Operator-impersonating driver (`roles()[1]`), terminal-state semantics, `supports_anvil_settlement=True`; fork test refactored to `force_settle` + `roles()` assertion. |
| WS4 Gains `force_settle` + EndOfEpoch | **Done** | Epoch-advance driver with strict-increase assertion, `supports_anvil_settlement=True`; `END_OF_EPOCH_SELECTOR` typed preflight; fork test refactored + negative EndOfEpoch case. |
| WS5 YieldNest | **Done** | Full verified `Vault.json` (incl. `ExceededMaxRedeem` `0xb8b8b59c`); `YieldNestDepositManager` typed redemption preflight; test migrated to `ETHEREUM_MIDNIGHT_BLOCK` shared-pool + snapshot/revert with deposit + over-buffer preflight assertions. |
| WS6 Accountable | **Done** | Verified strategy ABI `OpenTermCompoundV1.json`; deposit preflight now reads `strategy().loan().minDeposit` (binding min = max(vault, strategy)), redemption min typed; Monad state-relative test on the live Hyperithm vault validating the `0x5945ea56` fix. |
| WS7 Upshift | **Done** | Overrode `fetch_depositable_raw_assets` in `UpshiftMultiAssetDepositManager` so a generic-path preflight gets a real per-asset limit instead of `ABIFunctionNotFound`; assertion added to the midnight-block Sentora lifecycle test. (The executor-side routing fix remains tracked in the trade-executor report.) |
| WS8 Plutus | **Done (fulfil deferred)** | `HedgeVaultV2.json` bound for the Hedge deployment; Plutus removed from `CERTIFIED_SYNCHRONOUS`; new `PlutusAsyncDepositManager` (requestRedeem → pending/claimable → `redeem(requestId,receiver)` claim, `cancelRedeemRequest` reclaim, typed `WithdrawalsArePaused`/`UseRequestRedeem`); capability flipped to `redemption_flow=asynchronous`; existing test + new `ARBITRUM_MIDNIGHT_BLOCK` async-lifecycle test pass. **Deferred:** operator `fulfillRedeem` `force_settle` is role-gated (OZ AccessControl) and raises a precise `UnsupportedVaultSimulation`; `supports_anvil_settlement` left unset pending role discovery. |
| WS1 Lagoon settlement | **Done** | `_provision_safe_for_settlement` issues the missing standing `approve(vault)` (idempotent — only when allowance is short, so it doesn't shift block numbers) and tops the Safe up with synthetic denomination tokens to cover queued redemptions (`calculate_underlying_needed_for_redemptions` + 1-token buffer); `synthetic_assets_injected_raw` field added to `VaultForcedSettlementResult` for honest signalling; version-aware selector documented in `force_lagoon_settle`. Tests: existing 722Capital deposit/redeem preserved + zero-injection assertion, plus a new test that synthesises **both** failure modes (revoke approval + drain Safe) and asserts the driver recovers with `synthetic_assets_injected_raw > 0`. (My earlier "needs pre-existing third-party pending redemptions" claim was wrong — the reference vault reproduces both modes deterministically.) |
| WS3 cSigma | **Done** | Current-state verification (midnight-block fork) established the honest model: cSigma pools are **reserve-limited synchronous** ERC-4626 with **no onchain request/claim/ticket surface** (the FIFO queue is off-chain `withdrawalManager`), so a "proper async ticket manager" is impossible — modelling it as one would be Grok's cSuperior trap. **3.1 cSigma USD** (`0xd5d097f2`) added to `CSIGMA_SYNCHRONOUS_POOL_ADDRESSES` → gets the capacity-aware `CsigmaDepositManager` (its raw `Max redeem` assert becomes a typed `VaultFlowUnavailable`); verified with a current-state midnight-block capacity-preflight test (it was undeployed at 21.9M and is `Pausable`-paused now, so a full lifecycle can't be pinned — documented in the address-set comment). **3.2 cSuperior** `WithdrawalPending` (`0xb34f5c6c`) is now decoded onto the capacity-preflight refusal (`decoded_error="WithdrawalPending"`, `error_selector`), so the over-capacity/queued case is typed rather than a raw revert; the reserve-limited model is extensively documented in the manager. |

**ABIs fetched and stored (2026-07-25)** via the Etherscan v2 unified API
(proxy→implementation resolved), with source-recording READMEs per repo policy:
`plutus/HedgeVaultV2.json` (Arbitrum), `yieldnest/Vault.json` (Ethereum, full
impl replacing the curated file), `csigma/CsigmaV2Pool.json` (Ethereum),
`accountable/OpenTermCompoundV1.json` (Monad). Every expected custom-error
selector was verified by keccak against the plan's decoded values.

All workstreams are now implemented and fork-tested. The only bounded
follow-up is the Plutus operator-`fulfillRedeem` driver, which is gated on
discovering the OZ AccessControl fulfilment role and currently raises a precise
`UnsupportedVaultSimulation` (so `supports_anvil_settlement` stays unset for
Plutus). cSigma's off-chain FIFO queue portion is likewise not
force-settleable (no onchain claim surface) by design.

## Acceptance criteria (apply to every workstream)

1. A focused manager-level pytest on the affected deployment/version, forked
   at a fixed block, with absolute-value asserts (Monad excepted — no archive
   state, use relative asserts there).
2. Capability metadata published only for complete deposit and redemption
   paths; `supports_anvil_settlement=True` only after a forced settlement
   provably moves the specific ticket pending → claimable (or to the
   protocol's terminal state).
3. Typed `VaultFlowUnavailable` / `UnsupportedVaultSimulation` with structured
   context instead of raw `assert`, generic `ValueError` or undecoded revert
   strings.
4. Enough ticket data serialised to reconstruct a claim after restart
   (`serialize_redemption_ticket()` / `reconstruct_redemption_ticket()`).
5. Settlement transaction receipts always checked
   (`assert_transaction_success_with_explanation`), settlement hashes only
   returned after every receipt is verified.

---

## Workstream 0 — cross-cutting generic-flow hardening (unblocks cSigma USD, Upshift, Plutus)

These are shared defects in the generic ERC-4626 path that several protocol
gaps funnel through. Do this first.

### 0.1 Replace raw asserts in `flow.py` with `VaultFlowUnavailable`

- `eth_defi/erc_4626/flow.py:108` (`assert raw_amount <= max_deposit`) and
  `flow.py:251` (`assert raw_amount <= max_redeem`), plus the balance assert
  at `flow.py:238` — raise `VaultFlowUnavailable` with
  `requested_raw_amount`, `available_raw_amount`, `direction`,
  `phase="preflight"` instead.
- These are reached via `ERC4626DepositManager.create_deposit_request()`
  (`eth_defi/erc_4626/deposit_redeem.py:66`) /
  `create_redemption_request()` (`deposit_redeem.py:95`), which call
  `deposit_4626()` / `redeem_4626()` at the `:79–85` / `:110–116` call sites.
- Note the deposit side has a symmetric silent skip: `flow.py:107` skips the
  `maxDeposit` guard when `maxDeposit == 0`. Leave that skip in place (a zero
  `maxDeposit` most often means "no limit exposed", not "closed") but say so
  explicitly, so the asymmetry with 0.2 is intentional and documented.

### 0.2 Fix the silent `maxRedeem == 0` skip

> **Implementation status (2026-07-25).** The safe default (skip the guard when
> `maxRedeem == 0`, treating a zero as "no limit exposed", mirroring the
> `maxDeposit == 0` handling) is retained, and
> `ERC4626DepositManager.create_redemption_request(..., check_max_redeem=True)`
> now threads the guard flag (previously hardcoded). A `raise_on_empty_max_redeem`
> opt-in was briefly added but **removed as dead code** — no caller needed it:
> the protocols where a zero `maxRedeem` means "closed / request-based"
> (YieldNest, Plutus) gate it in their own manager preflight before reaching
> this generic path, so flipping the generic default was never necessary.
> Flipping the global default to raise would still require the full fork-based
> deposit/redeem regression suite to gate the blast radius.

- `flow.py:243–244` currently skips the max-redeem guard entirely when
  `maxRedeem` returns 0 ("some vaults always return max redeem as zero?"),
  letting closed/async vaults broadcast a guaranteed-revert `redeem()`
  (this is exactly how the Plutus probe produced an undecoded `0x797f246a`).
- Change: when `maxRedeem == 0` and the caller requested the guard, raise
  `VaultFlowUnavailable(reason="maxRedeem is zero — redemption closed or
  request-based", available_raw_amount=0)`.
- **Plumbing prerequisite (blast-radius control).** The opt-out is not wired
  today: `ERC4626DepositManager.create_redemption_request()`
  (`eth_defi/erc_4626/deposit_redeem.py:95`) **hardcodes
  `check_max_redeem=True`** at `:115` and exposes no `check_max_redeem`
  parameter — it only carries an unused, misnamed `check_max_deposit` arg
  (`:101`) that is never forwarded to `redeem_4626()`. So this change must
  first: (a) add a real `check_max_redeem` parameter to
  `create_redemption_request()` and thread it into `redeem_4626()`
  (renaming/removing the dead `check_max_deposit` arg); and (b) let a manager
  override the default. This is the **default path for every non-specialised
  ERC-4626 vault**, so flipping the silent skip to a raise would make any
  generic vault whose `maxRedeem` currently returns 0 (closed or async state)
  start raising. Before merging, audit which existing managers rely on the
  zero-skip and set `check_max_redeem=False` for the vaults that legitimately
  return 0 while remaining synchronously redeemable; enumerate them in the PR
  description.
- **Merge gate (not unit-tests alone).** "Audit and set `False`" is not an
  acceptance criterion. Before WS0 merges: (1) enumerate every manager that
  inherits or calls `redeem_4626()` for request construction; (2) run the full
  existing `tests/erc_4626/test_4626_deposit_redeem.py` plus the certified
  synchronous-manager suite with the new raise active and confirm no
  regression; (3) any specialised async manager that still touches the generic
  redeem path must pass `check_max_redeem=False` or stop calling
  `redeem_4626()` for request construction; (4) list the opt-outs in the PR.

### 0.3 Manager-level deposit-limit hook (no hard `maxDeposit` ABI dependency)

- Extract the `contract.functions.maxDeposit(receiver).call()` read
  (`flow.py:105–108`) into an overridable
  `ERC4626DepositManager.fetch_depositable_raw_assets(owner)` hook (naming
  mirrors cSigma's `fetch_redeemable_raw_shares()`,
  `csigma/deposit_redeem.py:27–35`). Base implementation keeps the ERC-4626
  `maxDeposit` call; catch `web3.exceptions.ABIFunctionNotFound` in the base
  and raise `VaultFlowUnavailable(reason="vault does not expose ERC-4626
  maxDeposit; protocol manager required")` instead of leaking the raw web3
  error.
- **Specify the exact control flow — a hook alone is a no-op.** The guard
  currently lives inside the free function `deposit_4626()`, so a manager
  override only takes effect if the manager (a) preflights via the hook and
  then calls `deposit_4626(..., check_max_deposit=False)`, or (b)
  `deposit_4626()` is refactored to accept the limit value/callable. Adopt
  pattern (a): specialised managers (Upshift, cSigma) preflight with the hook
  and pass `check_max_deposit=False` into the generic builder — the same shape
  cSigma already uses for redemption capacity. Write this dual path into the
  plan so the hook is actually on the deposit path.
- **Broaden the caught exception set.** A missing selector on a proxy vault
  can surface as more than `ABIFunctionNotFound` (e.g. `ContractLogicError`,
  `BadFunctionCallOutput`, decoding errors). Catch the documented web3
  read-failure set, not `ABIFunctionNotFound` alone, and unit-test that
  Upshift/cSigma never reach the raw ERC-4626 ABI path.

### Tests

- Extend `tests/erc_4626/test_4626_deposit_redeem.py` /
  `tests/vault/test_deposit_redeem.py` with unit tests asserting the typed
  exceptions and their structured fields for over-capacity deposit,
  over-capacity redeem, zero `maxRedeem` and missing-`maxDeposit` ABI cases.

---

## Workstream 1 — Lagoon Finance (6 vaults)

Code: `eth_defi/erc_4626/vault_protocol/lagoon/deposit_redeem.py` (force
settle, 65–122), `lagoon/testing.py` (`force_lagoon_settle()`, 324–376),
`lagoon/vault.py` (roles 787–842, version detection 451–503).

### Root causes (from lagoon-v0 v0.5.1 sources — external to this repo)

The Solidity below comes from the external lagoon-v0 v0.5.1 sources, which are
**not committed to this repository**, so verify the exact function bodies
against the deployed implementation before coding. The Python-side symptoms
they explain (no `approve()` inside `force_lagoon_settle`; the terminal
`pending -> pending` raise) are consistent with the code here.

`settleDeposit(_newTotalAssets)` always runs `_settleRedeem(msg.sender)`
afterwards. `_settleRedeem`:

- executes `asset.safeTransferFrom(safe, vault, assetsToWithdraw)` — this
  **requires the Safe to have approved the vault**. Our own deployment script
  grants this approval in production
  (`lagoon/deployment.py:2541–2562`), but third-party deployments never did,
  and the Anvil driver impersonates the Safe without ever issuing an
  `approve()`. → failure mode (b), the three Base vaults reverting
  `ERC20: transfer amount exceeds allowance`
  (`8453-0x2bff679b…`, `8453-0x63b04d3c…`, `8453-0xbe7db44f…`).
- **returns silently** when `assetsToWithdraw > asset.balanceOf(safe)`
  (capital deployed into strategy positions). `settleDeposit` succeeds, the
  redemption epoch never settles, and `force_settle()` raises
  "pending -> pending" at `deposit_redeem.py:113–114`. → failure mode (a),
  Moon Digital `1-0xa00f63e8…`, Syntropia `1-0xd17049ed…`, Angmar
  `42161-0x1723cb57…`. Approval alone cannot fix these; an explicit
  `settleRedeem()` call would hit the same silent balance guard.

### Fix

In `force_lagoon_settle()` (`lagoon/testing.py`), impersonate the Safe, then
run the settlement in this strict order (the shortfall must be measured at the
*settled* NAV, not the pre-valuation one):

1. **Update NAV first**: broadcast `updateNewTotalAssets(raw_nav)` from the
   valuation manager (already present) so the share price used for the
   shortfall calculation matches the price `settleDeposit` will use.
2. **Standing approval**: broadcast
   `denomination_token.approve(vault.address, 2**256 - 1)` from the
   impersonated Safe. Fixes (b).
3. **Redemption liquidity top-up (measured post-valuation)**: compute the
   shortfall from the settled share price — reuse the existing
   `LagoonFlowManager.calculate_underlying_needed_for_redemptions()` helper
   rather than hand-rolling `convertToAssets(...)`, so the maths stays aligned
   with production settlement. When positive, top the Safe up with the
   denomination token on the Anvil fork (`fund_erc20_on_anvil` / storage-slot
   balance write in `eth_defi/provider/anvil.py`). Fixes (a).
   - **Honest signalling (required).** A synthetic top-up can turn
     `pending → claimable` even when the live Safe could not pay redemptions,
     and `VaultForcedSettlementResult` has no field today to disclose that.
     Add a `synthetic_assets_injected_raw` (or Lagoon-specific extension)
     field, populate it with the injected amount (0 for the allowance-only
     deployments), and document that `supports_anvil_settlement=True` means
     "the driver can advance tickets on a fork", **not** "the vault is solvent
     live". Log the injected amount as well.
4. **Version-aware settlement selector**: `force_lagoon_settle()` currently
   hard-codes the `settleDeposit(uint256)` selector for every version; the
   legacy ABI exposes argument-less `settleDeposit()`. Prefer reusing the
   vault's existing version-aware settle wrappers (`LagoonVault.settleDeposit`
   / `post_valuation_and_settle`) over another hand-encoded `EncodedCall`;
   otherwise choose the selector by `vault.version` (`LagoonVersion`,
   `vault.py:140–146`). On failure include the detected version and role
   addresses in the raised `UnsupportedVaultSimulation` (settlement
   diagnostics requested by the report).
5. Keep receipt assertions (already present at `testing.py:359, 375`); after
   settlement re-read ticket status and only return transaction hashes on a
   verified pending → claimable transition (already enforced at
   `deposit_redeem.py:107–122`).
6. If, after approval + top-up, a deployment still cannot reach claimable,
   raise `UnsupportedVaultSimulation` with the concrete version/role/balance
   reason — never a bare "pending -> pending".

### Tests

- New fork tests against one allowance-revert deployment (Base, For Yield v2
  `0x2bff679b…`, fork block pinned with pre-existing pending redemptions) and
  one balance-shortfall deployment (Moon Digital on Ethereum, or Angmar on
  Arbitrum), asserting deposit and redemption tickets both go
  pending → claimable through `force_settle()`.
- Assert the honest-signalling field: `synthetic_assets_injected_raw == 0` on
  the allowance-only deployment and `> 0` on the balance-shortfall deployment,
  so a regression that silently injects (or fails to disclose) liquidity is
  caught.
- Keep `tests/lagoon/test_erc_7540_deposit_redeem.py` green (self-deployed
  722Capital vault — regression guard for the happy path).

---

## Workstream 2 — Ember (4 vaults): implement `force_settle()`

Code: `eth_defi/erc_4626/vault_protocol/ember/deposit_redeem.py`
(`EmberDepositManager`, 134), `ember/vault.py` (capability, 147–164).
Vaults: Earn `1-0x9be92947…`, Polymarket `1-0x0b9342c1…`, Third Eye
`1-0xf3190a3e…`, UDL `1-0x373152fe…`.

The settlement recipe already exists manually in
`tests/erc_4626/vault_protocol/test_ember_deposit_redeem.py:132`: impersonate
the operator, call `processWithdrawalRequests(n)`. The ABI
(`eth_defi/abi/ember/EmberVault.json`) has everything needed: `roles()`
(→ operator address, no hardcoding), `processWithdrawalRequests(uint256)`,
`getPendingWithdrawalsLength()`, `getPendingWithdrawal(uint256)`, and the
`RequestProcessed` event with `skipped`/`cancelled` flags. No ABI additions
required.

### Fix

1. Implement `EmberDepositManager.force_settle(ticket)`:
   - guard `is_anvil()`, require an `EmberRedemptionTicket`;
   - read operator via `vault_contract.functions.roles().call()[1]`. **The
     first fork test must assert this equals the known
     `EMBER_OPERATOR = 0x116046991e3F0B0967723073a87820eF5edB29f2`** — the
     `roles()` tuple layout is an assumption; if it differs across versions the
     driver would impersonate the wrong account and revert (or mis-diagnose as
     unsupported);
   - `anvil_impersonateAccount` + `anvil_setBalance` (Lagoon pattern,
     `lagoon/deposit_redeem.py:96–101`);
   - transact `processWithdrawalRequests(n)` from the operator, with `n`
     taken from `getPendingWithdrawalsLength()` (or loop until the ticket's
     `request_sequence_number` is processed);
   - confirm via `fetch_completed_redemption_tx_hash(ticket)` /
     `RequestProcessed`, rejecting `skipped`/`cancelled` events
     (`_validate_processed_event()`, `ember/deposit_redeem.py:650–668`);
   - return `VaultForcedSettlementResult`. **Note the terminal-state
     semantics**: Ember has no claim step — after processing,
     `get_redemption_request_status()` returns `none`, not `claimable`; the
     post-condition is "terminal `RequestProcessed` event found for this
     ticket", not "claimable". Document this in the docstring.
2. Add `supports_anvil_settlement=True` to
   `EmberVault.get_deposit_manager_capability()` (`ember/vault.py:159–164`)
   — valid because `redemption_flow="asynchronous"`.
3. If a deployment's operator processing cannot be reproduced, raise
   `UnsupportedVaultSimulation` with the precise reason and keep the
   capability `False` for that deployment.

### Tests

- Refactor `test_ember_deposit_redeem.py` so the manual operator block is
  replaced by `deposit_manager.force_settle(ticket)`; assert the settlement
  result fields and the updated capability dict
  (currently asserting the key is absent at lines 89–94 — flip to `True`).
- Parametrise or add one more Ember deployment (e.g. Polymarket) to prove the
  operator discovery via `roles()` generalises.

---

## Workstream 3 — cSigma Finance (2 vaults)

Code: `eth_defi/erc_4626/vault_protocol/csigma/` (`vault.py`,
`deposit_redeem.py`), ABI `eth_defi/abi/csigma/CsigmaV3Pool.json`.

### 3.1 cSigma USD `1-0xd5d097f2…` — raw "Max redeem" assert

Root cause: the address is missing from `CSIGMA_SYNCHRONOUS_POOL_ADDRESSES`
(`csigma/vault.py:41–46`), so `get_deposit_manager()` falls back to the
generic `ERC4626DepositManager` and hits the raw assert at `flow.py:251`.
The capacity-aware `CsigmaDepositManager` — `fetch_redemption_preflight()` at
`csigma/deposit_redeem.py:48`, `create_redemption_request()` raising the typed
error around `:145–199` — already produces exactly the requested
`VaultFlowUnavailable` with `requested_raw_amount` vs `available_raw_amount`.

Fix:

1. Workstream 0.1 already converts the generic assert into
   `VaultFlowUnavailable` (safety net for all csigma-like pools).
2. Fork-verify cSigma USD's synchronous surface at a pinned block, then add
   `0xd5d097f278a735d0a3c609deee71234cac14b47e` to
   `CSIGMA_SYNCHRONOUS_POOL_ADDRESSES` so it gets `CsigmaDepositManager` and
   the amount-aware `fetch_redemption_preflight()`
   (`VaultRedemptionPreflight`) — partial redemption up to `maxRedeem`
   remains possible instead of failing outright.

### 3.2 cSuperior `1-0x438982ea…` — "Withdrawal pending" is a FIFO queue

Root cause: the pool is declared fully synchronous
(`csigma/vault.py:122–133`), proven only on an old pinned fork; at current
state `redeem()` enqueues into cSigma's FIFO and a follow-up `redeem()`
reverts `Withdrawal pending`.

Fix:

1. Regenerate the pool ABI from the verified implementation, including the
   queue/claim functions, events and errors (the committed
   `CsigmaV3Pool.json` has no queue surface and is currently unreferenced by
   Python code); record the canonical source alongside it per ABI policy.
2. Model redemption as asynchronous: new
   `CsigmaRedemptionTicket(RedemptionTicket)`, capability
   `redemption_flow="asynchronous"`, implement
   `create_redemption_request()` (request-event parsing),
   `get_redemption_request_status()`, `can_finish_redeem()`,
   `finish_redemption()` (claim construction),
   `serialize/reconstruct_redemption_ticket()`, and — if the queue is
   operator-processed and reproducible on a fork — a `force_settle()` driver
   on the Ember/Lagoon pattern; otherwise `supports_anvil_settlement` stays
   unset with a precise reason.
3. `Withdrawal pending` on a second `redeem()` becomes a typed
   `VaultFlowUnavailable(reason="redemption already queued", …)` from
   preflight (probe the queue state before building the call).

### Tests

- Extend `tests/erc_4626/vault_protocol/test_csigma.py`: cSigma USD
  over-capacity redemption → typed error with both raw amounts; partial
  redemption within `maxRedeem` completes; cSuperior full async
  request → status → (force-settle if implemented) → claim lifecycle at a
  recent pinned fork block.

---

## Workstream 4 — Gains Network gTrade (2 vaults)

Code: `eth_defi/erc_4626/vault_protocol/gains/` (`deposit_redeem.py`,
`vault.py`, `testing.py`), ABI `eth_defi/abi/gains/GToken.json`.

### 4.1 Base gTrade `8453-0xad20523a…` — epoch settlement driver

The driver building block already exists: `force_next_gains_epoch()`
(`gains/testing.py:26–74`) warps Anvil time past the epoch end and calls the
**permissionless** `openPnl.forceNewEpoch()` — no impersonation needed; the
Arbitrum test already loops it (`tests/gains/test_gtrade_usdc.py:176–185`).

Fix:

1. Implement `GainsDepositManager.force_settle(ticket)`:
   - guard `is_anvil()`, require a `GainsRedemptionTicket`;
   - `while fetch_current_epoch() < ticket.unlock_epoch:
     force_next_gains_epoch(...)` with a sane iteration cap. **Assert the epoch
     strictly increases each iteration** (`new_epoch > old_epoch`); if
     `forceNewEpoch()` mines time but the epoch does not advance (oracle/PnL
     path) the loop must raise `UnsupportedVaultSimulation` with the epoch
     numbers rather than spin the cap or return a false "settled";
   - assert `can_finish_redeem(ticket)` afterwards, else raise
     `UnsupportedVaultSimulation` naming epoch numbers;
   - return `VaultForcedSettlementResult` with the epoch-forcing tx hashes.
2. Verify on a Base fork that `forceNewEpoch()` succeeds via plain time-warp
   (some deployments may require Chainlink fulfilment; if Base does, drive
   the oracle callback under impersonation or keep the capability off with a
   precise reason).
3. Publish `supports_anvil_settlement=True` in
   `GainsVault.get_deposit_manager_capability()` (`gains/vault.py:533–547`)
   only once (1)–(2) pass on both Arbitrum and Base forks.

### 4.2 Arbitrum gTrade `42161-0xd3443ee1…` — decode `0xa73449b9`

`0xa73449b9 == keccak("EndOfEpoch()")[:4]` — thrown by GToken outside the
allowed request window (requests only in the first two days of a three-day
epoch; guard already read via `nextEpochValuesRequestCount()`,
`gains/deposit_redeem.py:173–179`). The error is **already in the packaged
ABI** — only decoding/preflight is missing.

Fix:

1. Add `END_OF_EPOCH_SELECTOR = HexBytes("0xa73449b9")` module constant
   (Lagoon selector-constant pattern, `lagoon/deposit_redeem.py:28–32`).
2. In `create_redemption_request()` and `finish_redemption()` preflight the
   epoch window and raise
   `VaultFlowUnavailable(decoded_error="EndOfEpoch",
   error_selector=END_OF_EPOCH_SELECTOR, next_open=<estimated next window>)`
   instead of letting the raw selector escape; also catch the selector when a
   broadcast still reverts and re-raise typed.
3. The satellite `redeem()` case in the report is the async claim path — the
   typed async flow (ticket + `force_settle` from 4.1) supersedes the
   synchronous revert.

### Tests

- Extend `tests/gains/test_gtrade_usdc.py` to route through
  `force_settle(ticket)` instead of the manual epoch loop; add a Base-fork
  gToken lifecycle test (none exists today); add a window-closed case
  asserting the typed `EndOfEpoch` error.

---

## Workstream 5 — YieldNest RWA MAX (1 vault)

Code: `eth_defi/erc_4626/vault_protocol/yieldnest/vault.py`, ABI
`eth_defi/abi/yieldnest/Vault.json`. Vault `1-0x01ba6972…`.

`0xb8b8b59c == ExceededMaxRedeem(address owner, uint256 shares,
uint256 maxShares)` — buffer-limited `maxRedeem(owner)` exceeded. The
committed ABI has **zero error entries**, so the selector cannot be decoded
symbolically. eth-defi already fail-closes redemption in the capability
(`can_redeem=False`, `yieldnest/vault.py:118–142`); the revert appears when
the caller redeems anyway.

Fix:

1. Add the verified custom errors to `yieldnest/Vault.json`
   (`ExceededMaxRedeem(address,uint256,uint256)`,
   `ExceededMaxWithdraw` `0xd929e443`, and siblings from the
   Blockscout-verified implementation recorded in
   `eth_defi/abi/yieldnest/README.md`).
2. Add a `YieldNestDepositManager` (cSigma pattern) with a redemption
   preflight comparing requested raw shares to `maxRedeem(owner)` (per-owner
   calls work; only the `address(0)` probe is broken,
   `yieldnest/vault.py:111–116`), raising `VaultFlowUnavailable` with
   `error_selector=0xb8b8b59c`, `requested_raw_amount`,
   `available_raw_amount` and the decoded owner/shares/maxShares.
3. Determine from the buffer mechanics whether partial synchronous redemption
   up to `maxRedeem` is safe → publish `can_redeem=True` with an
   amount-aware `VaultRedemptionPreflight`; if capacity is routinely zero,
   keep it a typed current-state admission condition
   (`VaultFlowUnavailable`), replacing the
   `maturity_aware_redemption_flow_not_implemented` placeholder reason.

### Tests

- Extend `tests/erc_4626/vault_protocol/test_yieldnest.py` with a redemption
  attempt: over-capacity → typed error carrying the decoded arguments;
  within-capacity (if any buffer exists at the pinned block) → completes.

---

## Workstream 6 — Accountable on Monad (1 vault)

Code: `eth_defi/erc_4626/vault_protocol/accountable/deposit_redeem.py`.
Vault: Hyperithm Delta Neutral `143-0x7cd23112…`.

`0x5945ea56 == InsufficientAmount()` — already mapped by #1368
(`ACCOUNTABLE_INSUFFICIENT_AMOUNT_SELECTOR`, `deposit_redeem.py:49`), but the
revert comes from the **strategy contract**, not the vault: `deposit()` calls
`strategy.onDeposit()` → `AccountableOpenTerm._requireMinDepositAmount()`,
which enforces the per-deployment loan term `loan().minDeposit`
(live: 1,000 USDC at 6 decimals) — far above the vault-level minimum the
adapter currently preflights (`deposit_redeem.py:202–215`, which reads the
onchain `vault_contract.functions.MIN_AMOUNT_WEI().call()` value, live
10,000). A 10-token probe passes the vault check and
reverts in the strategy with the same selector. Not a whitelist issue:
`permissionLevel() == 0`, `maxDeposit(0xdead) > 0`.

Fix:

1. Commit `eth_defi/abi/accountable/AccountableOpenTerm.json` from the
   Monad-verified implementation (`0x647c9584…` behind proxy `0xD0943c76…`),
   recording the canonical source per ABI policy. The vault ABI already
   exposes `strategy()`.
2. Extend `create_deposit_request()` preflight: after the `MIN_AMOUNT_WEI`
   check, read `vault.strategy()` → `loan().minDeposit`; when the amount is
   below it, raise `VaultFlowUnavailable(decoded_error="InsufficientAmount",
   error_selector=ACCOUNTABLE_INSUFFICIENT_AMOUNT_SELECTOR,
   minimum_raw_amount=loan.minDeposit, phase="preflight")`. Catch
   `ContractLogicError` / `BadFunctionCallOutput` specifically for strategy
   variants without `loan()` (the base `AccountableStrategy` has no loan
   terms) and fall back to the vault-level check.
3. Mirror in `create_redemption_request()` using `loan().minRedeem`
   (currently only `MIN_AMOUNT_WEI`, `deposit_redeem.py:284–286`), and migrate
   its remaining `ValueError` min-shares / in-progress raises to typed
   `VaultFlowUnavailable`. **Verify the `minRedeem` unit on the Monad
   deployment** (assets vs shares) before comparing — a like-for-unlike
   comparison would false-pass or false-block; convert to a common unit.
4. Optionally map `DepositNotAllowed()` (loan not in OngoingDynamic state) to
   a typed closed-window reason and use the strategy-aware capacity in
   `can_create_deposit_request()`.

### Tests

- Extend `tests/erc_4626/vault_protocol/test_accountable.py` with a
  Monad-fork case on this deployment: below-`minDeposit` → typed error with
  `minimum_raw_amount`; at/above `minDeposit` → request builds. Monad has no
  archive state — all reads at `latest`, use relative asserts.

---

## Workstream 7 — Upshift Sentora USD Earn (1 vault)

Code: `eth_defi/erc_4626/vault_protocol/upshift/` (`vault.py`,
`deposit_redeem.py`), ABI `eth_defi/abi/upshift/MultiAssetVault.json`.
Vault `1-0x74ad2f78…`.

Root cause: the multi-asset vault ABI has **no `maxDeposit`/`maxRedeem`**;
its limit surface is `maxDepositAmount()`, `depositCap()`,
`getTotalAssets()`, `depositsPaused()`, two-arg
`previewDeposit(asset, amount)`. The generic preflight
(`flow.py:105–108`) therefore raises web3's
"`maxDeposit` was not found in this contract's abi" whenever the simulation
reaches the vault through the generic `ERC4626DepositManager` instead of
`UpshiftMultiAssetDepositManager` (which never touches `maxDeposit` — it uses
`fetch_max_deposit_for_asset()`, `upshift/deposit_redeem.py:120–148`).

**Diagnose the real call path first (do not assume the generic preflight).**
`UpshiftVault.get_deposit_manager()` already returns
`UpshiftMultiAssetDepositManager` when `multi_asset_like` is set, and that
manager never calls `deposit_4626`/`maxDeposit`. So the reported
`maxDeposit not found` error means the matrix reached the vault through some
*other* surface — most likely (i) the executor/probe used a generic ERC-4626
entry rather than the Upshift manager, (ii) `multi_asset_like` was not detected
for this deployment/block, or (iii) a deposit was attempted without an
`accepted_asset`. Before coding, prove which of these holds for
`1-0x74ad2f78…` (manager class actually selected, feature-flag value,
`accepted_asset` presence). Fix the real routing/detection bug; treat WS0.3
below as a safety net for a *wrong* generic entry, not the primary Sentora
fix. The trade-executor routing fix is a hard dependency for "gap closed".

Fix:

1. Workstream 0.3 gives the manager-level
   `fetch_depositable_raw_assets(owner)` hook and converts the raw web3
   `ABIFunctionNotFound` into a typed `VaultFlowUnavailable` — this removes the
   regressed error shape on any path that wrongly reaches the generic builder,
   but is not sufficient on its own if the routing/detection bug above is the
   real cause.
2. Override the hook in `UpshiftMultiAssetDepositManager` to answer from
   `fetch_max_deposit_for_asset()` (`maxDepositAmount`/`depositCap`/
   `getTotalAssets` + asset-aware `previewDeposit`), so a generic preflight
   caller gets a real limit for the vault's primary/queried asset instead of
   an error.
3. Expose the same reader through the vault-level `get_max_deposit()` surface
   used by the probe so the executor preflight can size and gate the deposit
   without ERC-4626 `maxDeposit`. (The complementary executor-side routing
   fix — selecting the Upshift manager with `accepted_asset` from
   `capability.deposit_assets` — is tracked in the trade-executor report.)

### Tests

- Extend `tests/erc_4626/vault_protocol/test_upshift.py`: generic-path
  preflight on Sentora returns the typed limit (no `ABIFunctionNotFound`);
  keep the existing multi-asset lifecycle test green.

---

## Workstream 8 — Plutus Hedge Token (1 vault)

Code: `eth_defi/erc_4626/vault_protocol/plutus/vault.py`;
`CERTIFIED_SYNCHRONOUS_DEPOSIT_MANAGER_CLASSES` at
`eth_defi/erc_4626/vault.py:63`. Vault `42161-0x58bfc95a…`.

Root cause — this is **missing adapter support, not a temporary closure**:
the Hedge vault's proxy has been upgraded to an ERC-7540-style async
redemption implementation (`0xf2b0b9cc…`). Direct `redeem()` reverts
`0x797f246a == UseRequestRedeem()`; the implementation exposes
`requestRedeem(uint256,address,address)`, `pendingRedeemRequest`,
`claimableRedeemRequest`, `cancelRedeemRequest`, admin-gated
`fulfillRedeem(uint256)`, claim overload `redeem(uint256,address)`, events
`RedeemRequested`/`RedeemFulfilled`/`RedeemCancelled` and errors
`WithdrawalsArePaused()` `0xe14e66da`, `RequestNotClaimable()` `0x7570897f`,
etc. eth-defi currently certifies Plutus as fully synchronous, so the
executor sees `maxRedeem == 0` → `redemption_unavailable`.

Fix:

1. Commit `eth_defi/abi/plutus/PlutusHedgeVault.json` from the
   Arbiscan-verified implementation (record source), override
   `vault_contract` in `PlutusVault` for this deployment.
2. Remove `PlutusVault` from
   `CERTIFIED_SYNCHRONOUS_DEPOSIT_MANAGER_CLASSES`; give it an explicit
   `get_deposit_manager_capability()` with `deposit_flow="synchronous"`,
   `redemption_flow="asynchronous"` for the async-upgraded deployment
   (gate by address/probe — other Plutus vaults may still be synchronous).
3. Rework `PlutusDepositManager` on the Accountable/Ember pattern:
   `has_synchronous_redemption() -> False`; preflight
   `withdrawalsPaused()` → typed `VaultFlowUnavailable`
   (`decoded_error="WithdrawalsArePaused"`, `error_selector=0xe14e66da`);
   `create_redemption_request()` builds
   `requestRedeem(shares, controller=owner, owner)` with the ticket parsed
   from `RedeemRequested` (real `requestId`); status via
   `pendingRedeemRequest`/`claimableRedeemRequest`; claim via
   `redeem(shares, receiver, controller)`; reclaim via
   `cancelRedeemRequest(requestId)` mapped to
   `AsyncVaultRequestStatus.reclaimable`; ticket
   serialise/reconstruct.
4. `force_settle()`: impersonate the fulfilment role on Anvil and call
   `fulfillRedeem(requestId)`, verify `RedeemFulfilled` for the exact
   request, publish `supports_anvil_settlement=True` only if this provably
   works on a fork; fulfilment is operator-driven, so no
   next-eligible time is derivable — the typed reason + pending/claimable
   status is the correct surface.
5. Re-validate the historical reader's `maxRedeem(address(0)) == 0`
   heuristic and `fetch_redemption_closed_reason()`
   (`plutus/vault.py:81–115, 197–208`): with the upgrade, `maxRedeem == 0`
   likely means "use requestRedeem", not `REDEMPTION_CLOSED_BY_ADMIN`.

### Tests

- Extend `tests/erc_4626/vault_protocol/test_plutus.py` with the full async
  lifecycle on an Arbitrum fork: deposit → `requestRedeem` → pending →
  `force_settle` (fulfil) → claimable → claim → analyse; plus a typed
  `UseRequestRedeem`/`WithdrawalsArePaused` preflight case.

---

## Suggested implementation order

1. **Workstream 0** (generic-flow hardening) — small, unblocks 3 protocols
   and removes every raw assert the report complains about.
2. **Workstream 1 Lagoon** (6 vaults, biggest count; both root causes are
   understood and localised in the Anvil driver).
3. **Workstream 2 Ember** (4 vaults; the recipe already exists in a test —
   low risk, high yield).
4. **Workstream 4 Gains** (driver building block exists; Base verification
   is the only unknown).
5. **Workstreams 3, 5, 6, 7** (cSigma, YieldNest, Accountable, Upshift —
   independent, parallelisable; mostly preflights + ABI additions).
6. **Workstream 8 Plutus** (largest new surface: new ABI + async manager +
   settlement driver).

Each workstream should land as its own PR with a `CHANGELOG.md` entry where
user-facing, following the repository commentary format. After all land,
ask trade-executor to re-run the 129-vault matrix to confirm the gap count
drops from 21.

## Risks and open questions

- **Lagoon liquidity injection** changes fork state (synthetic Safe USDC).
  Acceptable for simulation, but the amount must be logged and the result
  marked as forced settlement — it does not prove the live vault can pay
  redemptions today.
- **Gains on Base**: `forceNewEpoch()` may require Chainlink oracle
  fulfilment on that deployment; if the plain time-warp fails, the fallback
  is impersonating the oracle callback or keeping
  `supports_anvil_settlement` off with a precise reason.
- **cSuperior queue ABI**: the FIFO/claim surface must be regenerated from
  the verified implementation; if the queue is processed by a permissioned
  operator not reproducible on a fork, `force_settle` stays unsupported with
  a typed reason (still an improvement over the raw revert).
- **Plutus fulfilment role**: `fulfillRedeem` is controller-gated; the role
  address must be read onchain and verified impersonatable on Anvil.
- **Monad**: no archive nodes — the Accountable test must use `latest` reads
  and relative asserts, and may be flaky if the loan state changes; consider
  marking it with the appropriate skip condition when the loan closes.
- **Capability schema consumers**: flipping Ember/Gains
  `supports_anvil_settlement` and Plutus `redemption_flow` changes the
  scanner-published `_deposit_manager` schema
  (`eth_defi/erc_4626/scan.py:405–436`) — confirm trade-executor treats the
  new values as expected before merging.
