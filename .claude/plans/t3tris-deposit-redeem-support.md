# T3tris deposit and redemption support

## Goal

Make T3tris vaults tradeable through the existing `eth_defi` vault framework:
a deposit manager for the request/settle/claim lifecycle, GuardV0 admission
for its four calls, an Anvil settlement driver, and the capability metadata
and deposit-probe support that depend on them.

Today `T3trisVault` is scanner-only. It inherits the synchronous
`ERC4626DepositManager`, returns no capability, has no guard selectors, and
both probed Arbitrum vaults end `reverted` in
`eth_defi/data/deposit-status/vault-deposit-status.json`.

## Verified facts this plan relies on

Observed on Arbitrum on 2026-11-14, from historical transactions or on an
Anvil fork at `ARBITRUM_MIDNIGHT_BLOCK` (487,039,644) against First USDC
`0x98e43a491a464f0886bc5e57207c340bbed0d01f`. The public T3tris docs
repository and GraphQL endpoint return 404, so the committed
`eth_defi/abi/t3tris/IVault.json` and onchain behaviour are the sources.

- All three known Arbitrum vaults report `isVaultOpen() == false`. They only
  accept the asynchronous flow; `maxDeposit()` is 0.
- First USDC is permissionless (`isDepositEnabled` true, both whitelists off)
  at the midnight block and at head. Gami USDC `0x9984…` has deposits disabled
  and both whitelists on. `0x394c…` has the deposit whitelist on.
- The deposit spender is the vault itself. `requestDeposit(receiver, false,
  assets, b"")` with a plain ERC-20 allowance works; `permit2Data` is optional.
- `requestId` is a vault-wide epoch (`getCurrentDepositRequestId()`), not a
  per-user id. Per-owner state is keyed by `(owner, requestId)`.
- The request events have the same types as ERC-7540 (`DepositRequest` and
  `RedeemRequest` with `address,address,uint256,address,uint256`), so the topic
  and the `requestId` argument match the generic parser.
- Claims are owner-wide: `claimDeposit(receiver)` and
  `claimRedeem(owner, receiver, false)` take no request id and emit
  `ClaimDeposit` / `ClaimRedeem(requestId, owner, receiver, assets, shares)`.
- A new `requestDeposit` auto-claims an earlier claimable deposit to
  `receiver`; `requestRedeem` has `previousClaimReceiver` for the same reason.
- Settlement is `settle(0, false, b"")` sent by `getCurator()`. Each
  settlement needs an oracle valuation newer than the previous settlement,
  otherwise it reverts `Vault__StaleValuation()`. The oracle (`getOracle()`)
  is refreshed with `setTotalAssets(uint256)` by its `owner()`. In closed mode
  the vault's `totalAssets()` equals the oracle's.
- Redemption settlement pulls assets from the curator wallet through the
  curator's allowance to the vault.
- Full fork lifecycle with an impersonated curator and oracle owner:
  100 USDC → 98,468,952 raw shares → 99,467,682 raw USDC.

| Ticket state | Deposit read | Redemption read |
| --- | --- | --- |
| Claimable | `getSettledDepositShares(owner, id) > 0` | `getSettledRedeemAssets(owner, id) > 0` |
| Pending | otherwise `getRequestDepositAmount(owner, id) > 0` | otherwise `getRequestRedeemAmount(owner, id) > 0` |
| None | both 0 (claimed, auto-claimed or cancelled) | both 0 |

## Implementation status

Implemented on 2026-11-14 in commits `84596e7b4` (step 3), `03841b30b`
(step 4), `a1f96cd4c` (steps 1, 2, 5 and 6), `ff255257b` (step 9; the migration was renumbered from 036 to 072 when merging master) and
`69ab8729d` (step 7 fixes). The security review record is
[t3tris-guard-security-review.md](t3tris-guard-security-review.md).

Deviations from the steps below:

- Step 2: claim analysis decodes `ClaimDeposit` and `ClaimRedeem` in one
  helper and raises `VaultRequestInvariantViolation` when the claim's request
  id, owner or receiver differ from the ticket, instead of relying on the
  inherited redemption analyser's topic positions.
- Step 5: the inline oracle ABI fragment has `owner()` and
  `setTotalAssets(uint256)`; the refreshed value is the vault's
  `totalAssets()`.
- Step 6: the guard test also covers the automatic claims made by a new
  request, malformed calldata and the share-token calls, from the security
  review.
- Step 8 is not done. The probe selects candidates from
  `~/.tradingstrategy/vaults/vault-metadata-db.pickle`, and the local copy
  has no T3tris rows. An operator with a current database must rerun it with
  `VAULT_SELECTION=protocol PROTOCOL=T3tris CHAIN_ID=42161`. Until then the
  two T3tris rows in `vault-deposit-status.json` still record the old
  `reverted` result; the guarded fork tests are the certification.
- `tests/lagoon/test_lagoon_max_settlement.py` now checks the guard internal
  version with `>=`, because the settlement limit constant is a minimum.

## Comparison with existing integrations

| | Atoma | IPOR | Lagoon | T3tris (this plan) |
| --- | --- | --- | --- | --- |
| Manager base | `ERC4626DepositManager` | `ERC4626DepositManager` | `ERC7540DepositManager` | `ERC7540DepositManager` |
| Manager overrides | most of the lifecycle (own tickets, reconciliation) | request builders and availability only | preflight, event topics, settlement | call builders, status, deposit claim analysis, settlement |
| Guard change | 2 inline selectors, `whitelistAtoma()` | none (standard selectors) | none for the vault flow | 4 inline selectors, `whitelistT3tris()` |
| Guard library | none | none | `LagoonLib`, for the Safe settlement budget only | none |
| Guard setup | 4-line branch in `setup_guard()` | generic | generic | same branch as Atoma |
| Solidity mock / Foundry case | none | none | shared ERC-7540 mock | none |
| `force_settle` | impersonates operator; rejects `mock` and `ignore_liquidity` | synchronous no-op | impersonates valuation manager and Safe | impersonates oracle owner and curator; rejects `mock` and `ignore_liquidity` |
| Tests | guard fork test, policy test, manager test | fork tests | fork tests | guard fork test with policy cases, manager test |

Atoma is the closest precedent: a non-standard asynchronous ABI that shipped
with inline guard selectors, a real-fork settlement driver and fork-only
evidence. Lagoon shows how little a subclass of the generic queued manager
needs. IPOR shows the floor: override only what differs.

## Design decisions

1. **Closed-mode asynchronous flow only.** Open-mode synchronous vaults,
   Permit2 deposits, `unsafe=true`, request decreases (cancellation) and
   `multicall` are out of scope. No known vault is open; the manager refuses
   one with a typed unavailable result.
2. **Reuse the generic queued manager.** `T3trisDepositManager` subclasses
   `ERC7540DepositManager`, as Lagoon does, and overrides only what differs.
   `T3trisVault` stays an `ERC4626Vault`.
3. **One open request per owner and direction.** The manager refuses a new
   request while the owner has a pending or claimable one in that direction,
   so the auto-claim path is not used. Ticket status is read for the ticket's
   own `requestId`. A ticket consumed some other way reports `none` and uses
   the framework's ordinary recovery; no reconciliation layer is added.
4. **No guard library; free the space instead.** The 17 internal validators
   in `GuardV0Base` take `bytes memory callData`, so every dispatch branch
   copies the call arguments from calldata to memory. Changing the parameter
   to `bytes calldata` is a one-word change per validator, keeps behaviour and
   revert messages, and frees about 1,200 bytes. Measured
   `TradingStrategyModuleV0` size (EIP-170 limit 24,576):

   | Variant | Module size | Margin |
   | --- | --- | --- |
   | Today | 24,259 | 317 |
   | Calldata validators | 23,059 | 1,517 |
   | Calldata validators and merged duplicate branches | 23,016 | 1,560 |
   | Calldata validators, T3tris validators | 23,717 | 859 |
   | **Calldata validators, T3tris validators, `whitelistT3tris()` (chosen)** | **24,003** | **573** |

   For reference, without the calldata change: new inline decoders overflow by
   49–91 bytes, reusing existing validators by word position leaves 73, and a
   separate `T3trisLib` leaves 118. Merging duplicate branches adds only 43
   bytes on top of the calldata change, so it is not done. The 18 existing
   Foundry guard tests pass on the chosen variant.
5. **An onchain `whitelistT3tris()`, as Atoma.** With the space freed there is
   room for it, so no Python call-list helper is needed.
6. **Fork-only evidence, as Atoma.** Both directions settle on a fork, so the
   capability publishes `supports_anvil_settlement=True`. No Solidity mock or
   Foundry case is added, and `force_settle` rejects `mock` and
   `ignore_liquidity`. This follows Atoma rather than the mock rule in
   `README-vault-protocol-support.md`; see the review record.

## Implementation steps

### 1. Vault adapter (`eth_defi/erc_4626/vault_protocol/t3tris/vault.py`)

- `is_account_whitelisted(address)` → `isDepositWhitelisted(address)`, so the
  shared `check_deposit_whitelist()` preflight works.
- `get_deposit_manager()` → `T3trisDepositManager`.
- `get_deposit_manager_capability()` → both directions `asynchronous`,
  `supports_anvil_settlement=True`. Add this last, once the tests and the
  step 7 security review pass.

### 2. Deposit manager (new `t3tris/deposit_redeem.py`)

`T3trisDepositManager(ERC7540DepositManager)`. Its `__init__` asserts a
`T3trisVault` and calls `VaultDepositManager.__init__`, bypassing the base
class's `ERC7540Vault` assertion.

Inherited unchanged: request and ticket classes, request-event parsing, ticket
serialisation, `has_synchronous_*`, estimates, delay sentinels,
`fetch_vault_flow_events()`, redemption claim analysis and the typed "not
implemented" closed-deposit guard validation.

Overridden:

| Method | T3tris behaviour |
| --- | --- |
| `_assert_deposit_request_available` | Deposit preflight below (Lagoon's hook) |
| `create_deposit_request` | Preflight, then `requestDeposit(owner, False, raw_amount, b"")` |
| `create_redemption_request` | Preflight, then `requestRedeem(owner, owner, owner, False, raw_shares)` |
| `create_redemption_request_for_analysis` | Rebuild the request parser with no preflight; the inherited version would rerun the open-request check on an already-mined request |
| `finish_deposit` / `finish_redemption` | `claimDeposit(ticket.to)` / `claimRedeem(ticket.owner, ticket.to, False)` |
| `get_deposit_request_status` / `get_redemption_request_status` | The ticket-state table for `(ticket.owner, ticket.request_id)` |
| `can_finish_deposit` / `can_finish_redeem` | Status is claimable |
| `is_deposit_in_progress` / `is_redemption_in_progress` | `getLast*RequestId(owner)`, then status is not none |
| `can_create_deposit_request` / `can_create_redemption_request` | Boolean form of the preflights |
| `get_redemption_event_signatures` | The `ClaimRedeem` topic; its layout matches what the inherited analyser reads |
| `analyse_deposit` | Decode `ClaimDeposit`; its indexed topics are ordered differently from ERC-4626 `Deposit` |
| `force_settle` | Step 5 |

Preflights raise before any broadcast:

- Deposit: `paused()` → `deposit_paused`; `isDepositEnabled()` false or
  `isEndOfFund()` → `deposit_closed`; `isVaultOpen()` → unavailable;
  `check_deposit_whitelist(owner)`; an open deposit request → unavailable.
- Redemption: `paused()`; `isVaultOpen()` → unavailable;
  `isWithdrawWhitelistEnabled()` with `isWithdrawWhitelisted(owner)` false →
  `WhitelistingRequired`; an open redemption request → unavailable;
  insufficient share balance.

Estimates keep the inherited `convertToShares` / `convertToAssets`. They do
not include entry and exit fees (about 0.4% low on redemption proceeds at the
test block); document this rather than add fee maths.

### 3. Guard size optimisation (separate commit)

Land this first, as its own commit with its own regression run, so the
T3tris diff and the security review can tell the two changes apart.

- In `contracts/guard/src/GuardV0Base.sol`, change the 17 internal validators
  from `bytes memory callData` to `bytes calldata callData`. The dispatcher
  already holds the arguments as `bytes calldata`, so today every branch
  copies them into memory before decoding. Decoding logic, revert messages,
  storage layout and the public ABI do not change.
- Do not merge duplicate dispatch branches or remove the three duplicate
  `(uint256, address receiver)` validators. Measured on top of the calldata
  change, that saves only another 43 bytes and makes the diff harder to
  review.
- Measured with `forge build --sizes` (solc 0.8.26, `optimizer_runs = 1`,
  `via_ir = false`) on a scratch copy of this branch:

  | Contract | Today | After this step | After step 4 |
  | --- | --- | --- | --- |
  | `TradingStrategyModuleV0` | 24,259 (margin 317) | 23,059 (margin 1,517) | 24,003 (margin 573) |
  | `GuardV0` | 22,173 | — | 22,039 |

  The step 4 figures come from a sizing prototype and can move by a few bytes
  in the final code.
- Rebuild with `make guard safe-integration` and run the regression gates
  before starting step 4: the Foundry tests in `contracts/guard/test` (18
  pass on the prototype), `tests/guard` and `tests/safe-integration`.
- Update `docs/README-contract-size.md` with the measured sizes.

### 4. T3tris guard support

All in `GuardV0Base.sol`:

- Four selector constants with their full signatures, also added to
  `contracts/guard/script/ComputeSelectors.s.sol`.
- One dispatch branch per call and three dedicated validators;
  `claimDeposit` reuses the existing single-receiver validator:

  | Call | Selector | Required |
  | --- | --- | --- |
  | `requestDeposit(address,bool,uint256,bytes)` | `0xafd933fb` | receiver allowed, `unsafe == false`, empty `permit2Data` |
  | `claimDeposit(address)` | `0xde56603c` | receiver allowed |
  | `requestRedeem(address,address,address,bool,uint256)` | `0x5fb8c2cf` | receiver, owner and `previousClaimReceiver` allowed, `unsafe == false` |
  | `claimRedeem(address,address,bool)` | `0x9cb4b041` | owner and receiver allowed, `unsafe == false` |

- `whitelistT3tris(address vault, string notes)`, modelled on
  `whitelistAtoma()`: the four call sites, the vault as approval destination
  and asset, and the denomination token. Settlement, request decreases,
  `forceClaimDeposit`, `unsafeRedeem`, `unsafeWithdraw` and `multicall` are
  not registered.
- Bump `getInternalVersion()` from 4 to 5, as its comment requires for new
  whitelisting.

Around it:

- Rebuild ABIs with `make guard safe-integration`, rerun the step 3
  regression gates and record the final sizes in
  `docs/README-contract-size.md`.
- `lagoon/deployment.py` `setup_guard()`: add `T3trisVault` / `t3tris_like` to
  the existing policy resolution and pick `whitelistT3tris` in the whitelist
  loop, next to `whitelistAtoma`.
- Add the T3tris row to the security model and whitelisting notes in
  `contracts/guard/README.md`.

Existing deployed modules do not contain the dispatch branch. T3tris vaults
need a newly deployed guard.

### 5. Anvil settlement driver

`T3trisDepositManager.force_settle()`, shaped like Atoma's:

1. Reject a non-Anvil provider, a `mock` or `ignore_liquidity=True` with a
   typed `UnsupportedVaultSimulation`.
2. Return early if the ticket is already claimable; refuse if it is not
   pending.
3. Impersonate the oracle `owner()` and call
   `setTotalAssets(vault.totalAssets())` to refresh the valuation. This needs
   a two-function inline ABI fragment (`owner()`, `setTotalAssets(uint256)`).
4. Impersonate `getCurator()` and call `settle(0, False, b"")`. A revert, for
   example a curator without redemption liquidity, becomes a typed
   unsupported result.
5. Return `VaultForcedSettlementResult` with before/after status and
   transaction hashes; raise if the ticket did not become claimable.

### 6. Tests

Both on the shared Arbitrum fork pool (midnight block, `xdist_group`,
`evm_snapshot_revert`) against First USDC.

- `tests/guard/test_guard_simple_vault_t3tris.py`, with `SimpleVaultV0` and a
  non-governance asset manager:
  - full lifecycle through `performCall()`: approval, request, `force_settle`,
    claim, redemption request, `force_settle`, claim, with absolute share and
    USDC assertions and analysed results;
  - guard rejections: an unapproved approval spender, a substituted address
    in every validated position of all four calls, `unsafe=true`, non-empty
    `permit2Data`, and the unregistered `decreaseDepositRequest`, `settle`,
    `forceClaimDeposit`, `unsafeRedeem` and `multicall` selectors;
  - guard policy: feature-bearing and address-only descriptors resolve to the
    T3tris calls in `setup_guard()`, and a resolution failure sends no policy
    transaction.
- `tests/erc_4626/vault_protocol/test_t3tris_deposit_redeem.py`: each
  preflight raises its typed result without a broadcast (closed deposits and
  whitelist exclusion on the restricted vaults, a second request while one is
  open), ticket serialisation round trip, and a consumed ticket reporting
  `none` after a later request.

### 7. Agent security review of the modified contracts

A dedicated security review of the Solidity changes, separate from the
`clean-up-for-pr` cross-agent review of the whole PR. It runs after steps 3–6
pass and before the capability metadata in step 1 is published.

**Precedents followed.**

- Guard hardening audits
  [#769](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/769)
  and [#772](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/772):
  a review of `GuardV0Base` and `TradingStrategyModuleV0` against the
  compromised-asset-manager threat, findings with severity IDs (M-1, L-1),
  each fixed finding paired with a negative test, and sizes reported after
  the fixes. [#773](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/773)
  added source comments for accepted findings.
- Lighter guard plan reviews in
  `docs/superpowers/plans/2026-10-27-lighter-core-vault-guard-review.md` and
  `…-fable-review.md`: a metadata header (date, CLI version and confirmed
  model, effort, method, exact invocation, reviewed revision), the reviewer
  output preserved verbatim, and a primary-agent disposition table of
  finding, assessment and action.
- The invocation, streaming, timeout and result-validation rules in
  `.claude/docs/agent-tricks-and-troubleshooting.md`.

**Scope.** The diff to `contracts/guard/src/GuardV0Base.sol` from steps 3
and 4, the selector script, the rebuilt `GuardV0` and
`TradingStrategyModuleV0` sizes, and the guard tests from step 6 for test
adequacy. The Python manager is out of scope here; `clean-up-for-pr` covers
it.

**Reviewers.** Two independent, grounded, read-only reviews, run in
parallel in the background with a 15-minute deadline and raw JSONL output:

1. Codex CLI with `gpt-6-astra`:
   `codex exec --json --sandbox read-only -m gpt-6-astra "<brief>" < /dev/null`.
2. Claude CLI with `claude-opus-5-5`, read-only tools, as in the Lighter
   review: `claude -p "<brief>" --model claude-opus-5-5 --effort high
   --permission-mode dontAsk --allowedTools "Bash,Read,Grep,Glob"
   --disallowedTools "Edit,Write,MultiEdit,Skill,Task" --output-format
   stream-json --verbose --no-session-persistence < /dev/null`.

Smoke-test both model IDs first. Accept a review only from a final `result`
or `turn.completed` event, and confirm it inspected this worktree and a
non-empty diff. Tell both reviewers not to edit files, run tests or start
other agents or cleanup skills; give them the test results instead, because
a read-only sandbox cannot run pytest or Forge.

**Brief.** Threat model: a compromised but allowed asset manager, not
governance, trying to move, redirect or strand the Safe's tokens, shares or
pending requests. Governance, the T3tris curator and the oracle owner are
trusted; `anyAsset` is disabled in product deployments. The reviewers check:

- *Calldata refactor:* each of the 17 validators accepts and rejects exactly
  the same inputs as before, including short, malformed and dirty-bit
  arguments and dynamic `bytes` offsets; no caller still passes a memory
  argument; the Atoma payload-length check and every revert message are
  unchanged.
- *T3tris validation:* each selector constant matches its signature; the
  decode types match the ABI; every address argument is validated at its ABI
  position, including the auto-claim receivers (`receiver` on
  `requestDeposit`, `previousClaimReceiver` on `requestRedeem`); `owner`
  cannot name an account other than the Safe; `unsafe` and `permit2Data` are
  pinned.
- *Whitelisting:* `whitelistT3tris()` is `onlyGuardOwner`, registers only the
  four call sites, and allows only the vault as approval destination. Check
  what becomes reachable if governance also calls `whitelistERC4626()` on a
  T3tris vault, and the interaction with `anyAsset` and the call-site check.
- *Integration:* storage layout and library links are unchanged, existing
  deployments behave as before, both contracts stay under EIP-170, and the
  internal version is bumped.
- *Tests:* every validated position and both pinned flags have a negative
  case, the positive lifecycle runs through `performCall()`, and the
  unregistered selectors are rejected.

Findings first, each with a severity (critical, high, medium, low or
informational), an ID, `file:line`, the exploit path (which call, what moves
where) and the fix. Then state for each checklist area that no issue was
found and what was checked, and give a verdict.

**Disposition.**

- Verify every finding against the code before acting.
- Fix each valid critical, high or medium finding with a negative test that
  fails before the fix, as in #772.
- Fix a low finding or accept it with a short source comment beside the code
  and a note in the `contracts/guard/README.md` security model, as in #773.
- After fixes, rerun the guard regression gates and a blocking-only review
  pass on the changed lines.
- Record the review in `.claude/plans/t3tris-guard-security-review.md`, with
  the Lighter review's metadata header, the verbatim outputs and a
  disposition table, and post the findings table as a PR comment.

The review passes when no critical, high or medium finding remains open and
every low finding is fixed or documented as accepted.

### 8. Deposit probe and status data

- `deposit_probe.py`: pick `whitelistT3tris` for a `T3trisVault`, next to the
  existing `whitelistAtoma` choice. The existing asynchronous
  branch already parses the request ticket.
- Rerun the probe for the two T3tris rows following
  `README-deposit-status.md`; do not hand-edit the JSON.

### 9. Persisted metadata, docs and changelog

- New numbered migration under `migration/scripts/` refreshing the persisted
  `_deposit_manager` capability for T3tris rows, with the PR run reminder.
- `README-t3tris.md`: replace the "Trading support needs…" section with the
  implemented flow, the settlement findings and the manager-to-guard coverage
  matrix. Note the dead docs and GraphQL links.
- Add the T3tris row to `README-vault-protocol-support.md`, an API stub under
  `docs/source/api`, and a `CHANGELOG.md` entry.

## Risks and open questions

- **The calldata change touches every validator.** It is mechanical and the
  Foundry suite passes on the sizing prototype. It lands as its own commit,
  runs the full guard regression gates, and is the first item of the step 7
  security review. It could also ship as its own small PR first.
- **`unsafe` semantics are unverified.** Every historical call passes
  `false`; the guard pins it to `false`.
- **No mock or Foundry case.** This follows Atoma but not the letter of the
  protocol support contract.
- **Third-party requests for the same owner** can top up or auto-claim the
  owner's request. Funds still land with the owner; the ticket then reports
  `none` and goes through ordinary recovery.
- **Fork redemption settlement needs curator liquidity.** It works in the
  test because the curator holds the just-settled deposit.
- **Curator and oracle roles.** For First USDC both are one EOA; the driver
  must not assume the two addresses are equal.
- **Robinhood Chain vaults** use the same ABI but are not fork-tested here.
- **Trade-executor** routing and universe handling for T3tris are a separate
  change in that repository.

## Review record

**Codex CLI, 2026-11-14** (`gpt-6-astra`, read-only, grounded in the
repository), on the first draft. It found no inherited transaction path
calling a missing ERC-7540 function and judged the guarded address arguments
complete. Applied: the `create_redemption_request_for_analysis()` override,
ticket-specific status, the closed-mode check on redemptions, dropping the
liquidity bypass, and the unapproved-spender test.

**Simplification pass against Atoma, IPOR and Lagoon, 2026-11-14.** Not
re-reviewed by Codex. Changes:

- `T3trisLib` removed, together with its linking in `deploy.py`, the
  `Makefile` and Lagoon deployment, and the unlinked-library and
  deployment-linking tests.
- Dropped the Solidity mock and Foundry case that Codex asked for, on the
  Atoma precedent.
- Dropped the `VaultRequestInvariantViolation` amount matching that Codex
  asked for; ticket-specific status is kept.
- Dropped fee-aware estimates and the oracle ABI file.
- Folded the policy test into the guard test file.

**Guard size pass, 2026-11-14.** Not re-reviewed by Codex. Switching the
existing validators to calldata parameters raises the module margin from 317
to 1,517 bytes. That allows plain dedicated T3tris validators with `unsafe`
and `permit2Data` pinned again, and an onchain `whitelistT3tris()` instead of
the Python call-list helper, ending at 573 bytes of margin.

**Plan update, 2026-11-14.** The guard size optimisation is now its own step
and commit (step 3) with its measurements and regression gates, the T3tris
guard work bumps the internal version, and an agent security review of the
modified contracts (step 7) gates the published capability.

**PR cleanup review, 2026-11-14.** Codex CLI `gpt-6.1-sol`, read-only and
grounded, on the Python, migration, test and documentation changes. No
blocking findings. Applied all three low findings: negative claim-analysis
tests for a foreign request id, owner and receiver in both directions; the
manager's API entry moved to `docs/source/api/erc_4626/index.rst`; a pinned
clock and absolute amounts in the auto-claim test. Remaining gaps it listed:
the outstanding deposit probe rerun, other deployments not fork-tested, and no
direct tests of the pause, open-mode, end-of-fund and settlement-failure
branches.

**Simplification and documentation pass, 2026-11-14.** Removed redundant
manager attributes and unused event-signature overrides, rewrote stale
T3tris documentation and links after T3tris moved its app to
`earn.t3tris.finance` and its documentation to `docs.t3tris.finance`, and
dropped the dead GraphQL and `api/v1` descriptions. Codex CLI `gpt-6.1-sol`
reviewed the whole branch afterwards: no blocking findings or regressions.
