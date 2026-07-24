# Vault deposit and redemption support plan

**Date:** 2026-07-22

**Implementation status:** This is a roadmap, not a claim that every section
is shipped. The current worktree implements the shared Anvil
`force_settle()` API, the Lagoon settlement driver, Plutus and Accountable
non-`previewDeposit()` estimates, the D2 closed-phase
preflight error, Upshift ordered denomination-token discovery, and the
Avalanche CCTP mapping. In particular, multi-asset Upshift transaction flows,
the cSigma, Yearn, YieldNest, IPOR and Lagoon-admission fixes, and the four
integration scenarios per protocol remain planned work. D2 has only a tested
closed-phase failure; it does not yet have a fork-proven successful transaction
path.

**Goal:** Give each in-scope vault protocol one working deposit path and one working redemption path through `VaultDepositManager`, with one representative failure for each direction. Fix the concrete eth_defi defects exposed by the trade-executor coverage run without attempting exhaustive support for every asset, deployment generation, queue state or settlement variant.

**Scope boundaries:** Ember and Gains remain outside this plan because their reported failures are in trade-executor routing. Base fork timeouts remain infrastructure issues until reproduced as eth_defi provider defects. Trade-executor position sizing and partial-close orchestration are outside this repository.

## Test and documentation policy

Each protocol manager receives four focused integration scenarios only:

1. One successful deposit from request construction through receipt analysis.
2. One failed deposit, preferably a typed preflight rejection; otherwise a decoded onchain failure.
3. One successful redemption from request construction through receipt analysis.
4. One failed redemption, preferably a typed preflight rejection; otherwise a decoded onchain failure.

Every successful scenario invokes the standard Anvil-only `VaultDepositManager.force_settle()` test API. Synchronous managers call it with no ticket and receive a no-settlement-required result; asynchronous managers pass their request ticket, settle, claim and perform terminal analysis. This remains one happy path. Tests do not need to cover partial settlement, repeated epochs, every accepted asset, every exit asset, every deployment generation, operator delegation, cancellation/reclaim or every reported address unless that behaviour is necessary for the selected happy path.

Every protocol-specific `VaultDepositManager` class docstring must include a **Supported simulation path** and **Known limitations** section. Document unimplemented or untested protocol functionality there, including relevant asset choices, deployment generations, queue behaviour, partial settlement, repeated epochs, operator delegation, cancellation/reclaim, maturity rules or private-vault restrictions. The docstring must not claim broader support than the four scenarios prove.

## Implementation order

1. Add the shared forced-settlement and diagnostic API from section 12.
2. Reproduce the exact reported failure needed to select one representative happy and failed path per direction.
3. Implement the smallest protocol manager change that makes those paths available through `VaultDepositManager`.
4. Add the four focused integration scenarios and manager docstring limitations.
5. Add API documentation stubs for new public modules and split shared API, individual protocol families and Avalanche CCTP into separate reviewable pull requests.

## 1. Upshift multi-asset deposit and redemption

**Issue description**

`UpshiftVault.get_deposit_manager()` rejects the reported multi-asset Sentora vault, leaving it without any manager deposit or redemption path.

**Changes needed**

- Add `UpshiftVault.fetch_all_denomination_tokens()` to resolve every configured multi-asset denomination token in deterministic protocol order.
- Override `UpshiftVault.fetch_denomination_token()` to return the first token from `fetch_all_denomination_tokens()` as the primary denomination token. Its docstring must state that only this first token is supported by the manager and all remaining tokens are intentionally unsupported for now.
- Select a test vault whose first denomination token is USDC. Use that primary USDC token for the one supported multi-asset deposit and redemption path, including estimates, transaction construction and receipt analysis in `UpshiftDepositManager`.
- Return a typed deposit or redemption failure when the primary-token path is unavailable instead of guessing another asset or conversion path.
- Keep standard single-asset Upshift vaults on the generic ERC-4626 manager.
- Document all non-USDC denomination tokens, alternative exit assets, routers, queues and deployment variants as known limitations in `UpshiftDepositManager`.

**Integration test coverage expansion needed**

- On an Ethereum fork, assert `fetch_all_denomination_tokens()` returns the expected ordered collection and `fetch_denomination_token()` returns its first USDC token.
- Test one successful USDC deposit and one unavailable-primary-path or closed-vault deposit failure.
- Test one successful USDC redemption and one unavailable-redemption failure.
- Assert the USDC amounts, shares and final balances from strict receipt analysis.

## 2. Plutus deposit and redemption lifecycle

**Issue description**

The affected Plutus Hedge Token is classified as ERC-7540, rejects `previewDeposit()` and currently receives a generic synchronous manager.

**Changes needed**

- Inspect `0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a` and implement its actual deposit and redemption path in a Plutus-specific manager.
- Use a non-reverting conversion method or an explicit unavailable estimate instead of weakening the generic estimator.
- Implement only the request, settlement and claim phases required by the selected deployment.
- Document unimplemented Plutus generations, operator modes, cancellation/reclaim and alternative queue behaviour in the manager docstring.

**Integration test coverage expansion needed**

- On an Arbitrum fork, test one successful deposit and one closed-window or unavailable-estimate deposit failure.
- Test one successful redemption, calling `force_settle()` with its ticket if asynchronous or `None` if synchronous, and one redemption rejection such as no shares or a closed window.
- Assert decoded asset/share amounts and final balances.

## 3. Accountable estimation and exact-vault lifecycle

**Issue description**

`AccountableDepositManager` inherits a generic deposit estimator that fails for the ERC-7540-like Hyperithm vault even though its ordinary deposit and redemption path is otherwise supported.

**Changes needed**

- Implement a non-reverting Accountable deposit estimate for `0x7cd231120a60f500887444a9baf5e1bd753a5e59`.
- Keep the existing synchronous deposit and asynchronous redemption implementation, changing only what the exact deployment needs.
- Document partial claims, aggregate controller requests, repeated settlements and other Accountable generations as untested or unsupported where applicable.

**Integration test coverage expansion needed**

- On a latest-head Monad fork, test one successful estimated deposit and one zero/unavailable-estimate deposit failure.
- Test one full redemption through request, forced settlement and claim, plus one rejected redemption such as no shares or a concurrent request.
- Use state-relative assertions because Monad does not provide archive state.

## 4. cSigma redemption capacity

**Issue description**

The generic cSigma path does not distinguish a currently redeemable amount from a larger requested redemption, producing an unhelpful sell failure when liquidity is throttled.

**Changes needed**

- Decode the failure on `0x438982ea288763370946625fd76c2508ee1fb229` and read the current redeemable capacity.
- Add a `CsigmaDepositManager` only if needed to expose the supported immediate deposit and redemption calls.
- Allow a redemption at or below current capacity and reject a larger request without silently clipping it.
- Do not implement FIFO queue processing, partial-position orchestration, repeated claims or reserve replenishment in this plan; document them in the manager docstring as known limitations.

**Integration test coverage expansion needed**

- On an Ethereum fork, test one successful deposit and one representative rejected deposit.
- Test one successful redemption within current capacity and one typed failure above current capacity or at zero capacity.
- Assert that the failed redemption leaves all shares untouched.

## 5. Yearn receipt analysis variant

**Issue description**

Arche USD redemption succeeded onchain but the generic analyser did not find a usable `Withdraw` event.

**Changes needed**

- Reproduce `0x33ffc177a7278ff84aab314a036bc7b799b7cc15` and identify the exact event or wrapper path.
- Add the smallest Yearn-specific analyser or manager override needed for strict asset/share decoding.
- Keep ordinary Yearn V3 behaviour unchanged.
- Document untested Yearn generations, withdrawal overloads, queue selection and nested wrapper variants in the manager docstring.

**Integration test coverage expansion needed**

- On an Ethereum fork, test one successful Arche USD deposit and one representative deposit rejection.
- Test one successful Arche USD redemption with strict event and balance assertions and one representative redemption rejection.
- Do not add a generation matrix or tests for every same-signature/nested event combination.

## 6. YieldNest deposit analysis and immediate redemption

**Issue description**

YieldNest RWA MAX completed a deposit but generic analysis failed, and its immediate-buffer redemption path is not represented by a dedicated manager.

**Changes needed**

- Decode the successful deposit receipt for `0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8`.
- Add a `YieldNestDepositManager` for the observed deposit and immediate-buffer redemption path.
- Return a typed failure when redemption is locked or the immediate buffer is unavailable.
- Document queued withdrawal, maturity variants, cancellation/reclaim and other YieldNest deployments as known limitations.

**Integration test coverage expansion needed**

- On an Ethereum fork, test one successful deposit with strict event analysis and one paused/ineligible deposit failure.
- Test one successful immediate-buffer redemption and one locked or insufficient-buffer redemption failure.
- Do not add separate before/after maturity, queue or cancellation scenarios.

## 7. D2 pricing and epoch admission

**Issue description**

The HYPE++ estimate returned zero, causing a downstream division-by-zero error, while the generic manager did not explain the D2 epoch state.

**Changes needed**

- Inspect `0x75288264fdfea8ce68e6d852696ab1ce2f3e5004` and implement a D2-specific estimate/admission result for zero or undefined pricing.
- Support one deposit during a valid funding phase and one redemption during a valid withdrawal phase.
- Document other epoch transitions, custodied phases, operator NAV updates and delayed/queued variants in the manager docstring.

**Integration test coverage expansion needed**

- On an Arbitrum fork, test one successful deposit in an open phase and one zero-price or closed-phase deposit failure without division by zero.
- Test one successful redemption in a permitted phase and one closed-phase redemption failure.
- Drive only the state transition required for the selected happy redemption path.

## 8. Lagoon adapter for unified Anvil forced settlement

**Issue description**

Lagoon tests call `force_lagoon_settle()` directly with protocol-specific role knowledge, so a generic simulator cannot settle a manager ticket.

**Changes needed**

- Implement `LagoonDepositManager.force_settle(ticket)` using the shared API from section 12.
- Discover and impersonate the required Lagoon role internally, post one valid valuation and move the selected ticket to claimable state.
- Keep `force_lagoon_settle()` as a low-level implementation helper, but remove it from caller-facing lifecycle tests.
- Do not add partial settlement, repeated settlement epochs, operator delegation or cancellation/reclaim support; document these limitations in the manager docstring.

**Integration test coverage expansion needed**

- Reuse one Lagoon deployment for one successful deposit and one failed deposit.
- Test one successful redemption, calling only `manager.force_settle(ticket)` before the ordinary claim, and one failed redemption.
- Add one shared negative test proving `force_settle()` refuses a non-Anvil provider; do not repeat it for every protocol.

## 9. IPOR Fusion access control and redemption delay

**Issue description**

Six IPOR Fusion vaults reverted deposits with `AccessManagedUnauthorized(address)` because admission does not check the actual caller and selector.

**Changes needed**

- Decode the custom error and add caller-specific `AccessManager.canCall()` admission for the deposit selector.
- Use one public IPOR vault for the supported lifecycle and one of the six private vaults for the representative failure.
- Respect the ordinary account redemption delay required by the selected public vault.
- Document the other five failing addresses, untested IPOR generations and role/delay variants in the manager docstring rather than adding a test matrix.

**Integration test coverage expansion needed**

- On an Ethereum fork with the same Safe-shaped caller, test one successful public-vault deposit and one typed private-vault deposit rejection.
- Test one successful redemption after the selected vault's delay and one premature-redemption failure.
- Assert the failed deposit includes the decoded error and caller context.

## 10. Lagoon admission diagnostics

**Issue description**

Seven Lagoon vaults reverted deposits with `XJy8`, while current admission checks only an optional pause flag.

**Changes needed**

- Reproduce one representative `XJy8` deployment and decode its actual admission condition.
- Add the corresponding cheap admission check when a reliable view exists; otherwise preserve a structured decoded failure.
- Reuse the Lagoon manager and four integration scenarios from section 8 rather than creating a second test matrix.
- Document the remaining six addresses, untested deployment generations and unresolved admission variants in the Lagoon manager docstring.

**Integration test coverage expansion needed**

- Use one public Lagoon deposit as the happy deposit path and one representative `XJy8` address as the failed deposit path.
- Reuse section 8's successful and failed redemption paths; do not test every `XJy8` address or generation.
- Assert the failed path reports vault, caller, phase and decoded/raw reason.

## 11. Avalanche CCTP V2 vault funding

**Issue description**

Avalanche vault tests cannot be funded because chain ID `43114` is missing from the CCTP V2 domain map.

**Changes needed**

- Add Avalanche domain `1`, chain ID `43114`, reverse resolution and display name to the CCTP V2 mappings.
- Verify the shared V2 contracts and `localDomain()` on an Avalanche fork.
- Add one native-USDC funding fixture and the minimum shared mapping needed by vault tests.
- Do not add Fuji, every bridge direction, every affected vault address or a multichain deployment matrix in this plan.
- Document which Avalanche vault path is tested and which reported deployments remain untested in the corresponding manager docstrings.

**Integration test coverage expansion needed**

- Add one successful Ethereum-to-Avalanche funding test and one unknown/incorrect-domain failure.
- Use one representative affected Avalanche vault for one successful and one failed deposit, and one successful and one failed redemption.
- Do not rerun all three reported Avalanche vaults or require return bridging as part of this issue.

## 12. Shared manager settlement and diagnostics

**Issue description**

Callers lack a unified way to force asynchronous settlement on Anvil, and analysis failures lose protocol, phase and receipt context.

**Changes needed**

- Add `VaultDepositManager.force_settle(ticket: DepositTicket | RedemptionTicket | None) -> VaultForcedSettlementResult` as the single caller-facing Anvil settlement API.
- Require every in-scope protocol manager to implement this method. Synchronous managers implement the common Anvil-validated no-op path with `ticket=None` and return `settlement_required=False`; asynchronous managers require their ticket and drive it to claimable state.
- Check `eth_defi.provider.anvil.is_anvil()` before any mutation, including the synchronous no-op path, and raise `UnsupportedVaultSimulation` for a live provider. In-scope asynchronous managers must provide a driver for their selected happy path instead of returning unsupported.
- Keep asynchronous settlement protocol-agnostic for callers: the manager discovers any role, valuation, time advance and protocol calls needed for its selected ticket.
- Return the ticket identity, `settlement_required`, status before/after and settlement transaction hashes. Claims remain on `finish_deposit()` and `finish_redeem()`.
- Migrate `force_lagoon_settle()` behind the Lagoon manager and implement the corresponding manager override for each other asynchronous protocol selected in this plan. Synchronous managers use the shared no-op implementation.
- Add backwards-compatible structured failures containing protocol, vault, flow direction, phase, transaction hash, receipt status and decoded error.
- Add forced-settlement capability metadata distinguishing synchronous no-op and asynchronous Anvil-driver support.
- Update the base and protocol-manager docstrings with the supported simulation path and known limitations policy above.

**Integration test coverage expansion needed**

- Add one shared synchronous no-op `force_settle(None)` test and one asynchronous Lagoon `force_settle(ticket)` test, plus one failed non-Anvil-provider test.
- In every protocol's selected happy path, call its manager `force_settle()` and assert either no settlement is required or the asynchronous ticket becomes claimable and can be claimed through the ordinary manager API.
- Add one successful standard receipt-analysis test and one structured analysis-failure test.
- Do not add partial settlement, repeated calls, cumulative-progress, cancellation, operator or per-protocol conformance matrices.

## Completion criteria

- Each in-scope protocol has exactly one happy and one failed integration path for deposit and for redemption, reusing scenarios where two issue sections concern the same manager.
- Every in-scope manager implements `VaultDepositManager.force_settle()`. Successful paths use the Anvil-validated no-op for synchronous flows or ticket-driven settlement without protocol imports or caller-supplied privileged roles for asynchronous flows.
- Receipt analysis reports executed asset/share amounts, and failed paths retain actionable protocol and phase context.
- Every changed protocol manager docstring states its supported simulation path and known limitations.
- Unsupported assets, deployments and lifecycle variants are documented rather than silently presented as supported.
