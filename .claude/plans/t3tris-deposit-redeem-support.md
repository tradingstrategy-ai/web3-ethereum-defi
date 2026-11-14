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
  `supports_anvil_settlement=True`. Add this last, once the tests pass.

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
| `force_settle` | Step 4 |

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

### 3. Guard

All in `GuardV0Base.sol`:

- Change the 17 `bytes memory callData` validator parameters to
  `bytes calldata callData`. No other edit to existing validators.
- Four selector constants, one dispatch branch per call, and three dedicated
  validators (`claimDeposit` reuses the existing single-receiver validator):

  | Call | Selector | Required |
  | --- | --- | --- |
  | `requestDeposit(address,bool,uint256,bytes)` | `0xafd933fb` | receiver allowed, `unsafe == false`, empty `permit2Data` |
  | `claimDeposit(address)` | `0xde56603c` | receiver allowed |
  | `requestRedeem(address,address,address,bool,uint256)` | `0x5fb8c2cf` | receiver, owner and `previousClaimReceiver` allowed, `unsafe == false` |
  | `claimRedeem(address,address,bool)` | `0x9cb4b041` | owner and receiver allowed, `unsafe == false` |

- `whitelistT3tris(address vault, string notes)`, modelled on
  `whitelistAtoma()`: the four call sites, the vault as approval destination
  and asset, and the denomination token. Settlement and request decreases are
  not registered.

Around it:

- Rebuild ABIs with `make guard safe-integration`, then run the existing
  guard suites (`contracts/guard` Foundry tests, `tests/guard`,
  `tests/safe-integration`), because the calldata change touches every
  validator. Update the measured sizes in `docs/README-contract-size.md`.
- `lagoon/deployment.py` `setup_guard()`: add `T3trisVault` / `t3tris_like` to
  the existing policy resolution and pick `whitelistT3tris` in the whitelist
  loop, next to `whitelistAtoma`.

Existing deployed modules do not contain the dispatch branch. T3tris vaults
need a newly deployed guard.

### 4. Anvil settlement driver

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

### 5. Tests

Both on the shared Arbitrum fork pool (midnight block, `xdist_group`,
`evm_snapshot_revert`) against First USDC.

- `tests/guard/test_guard_simple_vault_t3tris.py`, with `SimpleVaultV0` and a
  non-governance asset manager:
  - full lifecycle through `performCall()`: approval, request, `force_settle`,
    claim, redemption request, `force_settle`, claim, with absolute share and
    USDC assertions and analysed results;
  - guard rejections: an unapproved approval spender, a substituted address
    in every validated position of all four calls, `unsafe=true`, non-empty
    `permit2Data`, and the unregistered `decreaseDepositRequest`, `settle`
    and `multicall` selectors;
  - guard policy: feature-bearing and address-only descriptors resolve to the
    T3tris calls in `setup_guard()`, and a resolution failure sends no policy
    transaction.
- `tests/erc_4626/vault_protocol/test_t3tris_deposit_redeem.py`: each
  preflight raises its typed result without a broadcast (closed deposits and
  whitelist exclusion on the restricted vaults, a second request while one is
  open), ticket serialisation round trip, and a consumed ticket reporting
  `none` after a later request.

### 6. Deposit probe and status data

- `deposit_probe.py`: pick `whitelistT3tris` for a `T3trisVault`, next to the
  existing `whitelistAtoma` choice. The existing asynchronous
  branch already parses the request ticket.
- Rerun the probe for the two T3tris rows following
  `README-deposit-status.md`; do not hand-edit the JSON.

### 7. Persisted metadata, docs and changelog

- New numbered migration under `migration/scripts/` refreshing the persisted
  `_deposit_manager` capability for T3tris rows, with the PR run reminder.
- `README-t3tris.md`: replace the "Trading support needs…" section with the
  implemented flow, the settlement findings and the manager-to-guard coverage
  matrix. Note the dead docs and GraphQL links.
- Add the T3tris row to `README-vault-protocol-support.md`, an API stub under
  `docs/source/api`, and a `CHANGELOG.md` entry.

## Risks and open questions

- **The calldata change touches every validator.** It is mechanical and the
  Foundry suite passes on it, but the Python guard suites must be rerun on the
  rebuilt ABIs before merge. It could also ship as its own small PR first.
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
