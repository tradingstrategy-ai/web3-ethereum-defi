# Lagoon v0.5 maximum settlement limit plan

> **For agentic workers:** Execute the tasks in order and keep the checkbox state current. This is security-sensitive money-movement code; stop if the observed Lagoon v0.5 balance flow differs from the invariants below.

**Goal:** Add an optional maximum Lagoon settlement amount to each dedicated vault/Safe/`TradingStrategyModuleV0` deployment. Asset-manager settlements above the configured limit are rejected atomically. Stock Lagoon v0.5 remains unchanged and existing Guard integrations remain unlimited unless they explicitly opt in.

**Architecture:** Stock Lagoon v0.5 settles a snapshotted queue in full and does not expose enough queue state for a dependable pre-call amount check. The module will therefore snapshot ERC-20 balances, execute the settlement tentatively through the Safe, and reject the transaction if the measured gross settlement exceeds the cap. An EVM revert rolls back the Safe call, Lagoon accounting, token transfers and emitted logs, so an oversized settlement never persists. Lagoon-specific configuration, selectors and balance validation move to a new external Forge library, `lib/LagoonLib.sol`, using the repository's diamond-storage pattern.

**Scope:** Lagoon v0.5 `settleDeposit(uint256)` and `settleRedeem(uint256)` are the supported target. Legacy no-argument selectors stay allowlisted and use the same cap when a limit is configured. No Lagoon protocol fork, partial settlement, rolling/cumulative limit or time-window policy is included.

## Locked behaviour

- The limit is per Lagoon vault and per settlement transaction, expressed in raw underlying-token units.
- `settledAmount <= maxSettlementAmount` succeeds; `maxSettlementAmount + 1` is rejected.
- A separate `limitEnabled` flag distinguishes unlimited mode from a deliberately configured zero limit.
- The measured amount is gross, not the Safe's net balance change:
  - `depositAssets = siloBalanceBefore - siloBalanceAfter`
  - `redeemAssets = vaultBalanceAfter - vaultBalanceBefore`
  - `settledAmount = depositAssets + redeemAssets`
- Gross accounting prevents a combined `settleDeposit(uint256)` call from hiding a large deposit and redemption that approximately net to zero at the Safe.
- Limits apply to whitelisted asset managers calling through `TradingStrategyModuleV0.performCall`. Governance retains its current bypass, and direct Safe-owner transactions remain outside module policy.
- An oversized queue is not partially consumed. The asset-manager call reverts and governance must settle it or change/disable the limit.
- The feature assumes a conventional, non-rebasing ERC-20 underlying without transfer fees. Fee-on-transfer tokens can make balance deltas undercount the economic amount and are explicitly unsupported; the maximum-settlement guarantee does not apply to them.

## Stock Lagoon v0.5 invariants already verified

- `settleDeposit(uint256)` transfers the snapshotted pending assets from the Silo to the Safe and then attempts redemption settlement in the same call.
- Redemption settlement transfers the underlying from the Safe into the Lagoon vault contract, so the vault's underlying balance increase measures redeemed assets.
- Lagoon v0.5 settlement fees are paid by minting shares, not by transferring underlying out of the measured Silo/vault balance envelope.
- Stock v0.5 removed the public `pendingSilo()` getter. The repository's `LagoonVault.silo_address` reads its ERC-7201 storage slot, and the Silo constructor grants the vault a persistent underlying-token allowance.
- The settlement `uint256 _newTotalAssets` argument is the proposed NAV, not a transfer amount. The maximum-settlement feature is not a NAV or valuation-integrity control.

## Backwards-compatibility contract

- Keep `whitelistLagoon(address,string)` with the same selector, events and unlimited behaviour.
- Keep both `performCall(address,bytes)` and `performCall(address,bytes,uint256)` unchanged.
- Keep all four Lagoon settlement selectors accepted.
- Preserve `LagoonVaultApproved(address,string)` exactly, even after moving its declaration/emission to `LagoonLib`.
- Replace the removed public mapping's generated getter with an explicit `allowedLagoonVaults(address)` function, and preserve `isAllowedLagoonVault(address)`.
- Existing deployment configuration, callers and tests that do not supply a settlement limit continue to work without changes.
- This is source/ABI/deployment compatibility, not an in-place upgrade. The current module is constructor-deployed rather than proxied; an existing Safe must deploy and enable a new module to adopt this feature.

## Task 1: Add `LagoonLib` and move Lagoon state

**Files:**

- Create: `contracts/guard/src/lib/LagoonLib.sol`
- Modify: `contracts/guard/src/GuardV0Base.sol`
- Add generated ABI: `eth_defi/abi/guard/LagoonLib.json`

- [ ] Add an external Forge library with diamond storage at a new, fixed slot such as `keccak256("eth_defi.lagoon.v1")`.
- [ ] Store the paired `vault`, `asset`, `pendingSilo`, `limitEnabled` and `maxSettlementAmount` values as singleton diamond storage. One Lagoon deployment always pairs exactly one vault, Safe and guard module.
- [ ] Move Lagoon selectors, `LagoonVaultApproved`, allowlist reads/writes and settlement validation into the library. Leave only generic call-site registration and thin ABI-compatible wrappers in `GuardV0Base`.
- [ ] Add a configuration event such as `LagoonSettlementLimitSet(vault, asset, pendingSilo, maxSettlementAmount, enabled, notes)` and an operational success event reporting deposit, redemption, gross and maximum amounts.
- [ ] Add owner-only Guard wrappers for:
  - atomic allowlisting with a limit;
  - updating an existing vault's limit;
  - disabling the limit without removing the Lagoon allowlist.
- [ ] Keep legacy `whitelistLagoon(address,string)` as unlimited. Prefer a distinctly named new function over a Solidity overload so web3.py callers do not face ambiguous overload resolution.
- [ ] Require `LagoonLib.isDeployed()` before every Lagoon library configuration, allowlist or validation call. Never rely on a void-returning delegatecall to fail when the library is linked to a codeless/zero address, because such a call can succeed as a silent no-op.
- [ ] Validate configured addresses: non-zero vault/asset/Silo, deployed code at each address, `vault.asset() == asset`, and a positive Silo allowance to the vault. The last check is the available stock-v0.5 relationship signal because `pendingSilo()` is not public.
- [ ] Add explicit compatibility getters for the old allowlist API plus a structured settlement-limit getter for monitoring and deployment assertions.

## Task 2: Implement atomic reject-after-execution enforcement

**Files:**

- Modify: `contracts/guard/src/lib/LagoonLib.sol`
- Modify: `contracts/safe-integration/src/TradingStrategyModuleV0.sol`
- Regenerate: `eth_defi/abi/guard/GuardV0.json`
- Regenerate: `eth_defi/abi/safe-integration/TradingStrategyModuleV0.json`

- [ ] Add a library pre-settlement function that verifies the vault is allowed and, only when a limit is enabled, returns a memory snapshot of the Silo and vault underlying balances.
- [ ] Add a post-settlement function that reads balances again, rejects non-monotonic deltas, calculates the gross amount, and reverts with a clear custom error containing actual and maximum amounts when the cap is exceeded.
- [ ] In `performCall`, identify recognised Lagoon selectors, capture the snapshot before `execAndReturnData`, bubble a Lagoon/Safe failure first, then perform the post-check. A post-check revert must occur in the same top-level transaction.
- [ ] Skip snapshot and post-check work for governance and unlimited configurations, preserving current behaviour and gas costs as far as practical.
- [ ] Do not compare the `uint256 _newTotalAssets` calldata argument with the limit. In Lagoon v0.5 it is the new NAV, not the queue or transfer amount.
- [ ] Avoid persistent "before" state: return a memory snapshot across the Safe call so failed or re-entrant executions cannot leave stale settlement state.
- [ ] Add an execution-capability hook that is false for standalone `GuardV0` and true for `TradingStrategyModuleV0`. Enabling a settlement limit must revert when this hook is false, preventing operators from configuring a limit on `GuardV0.validateCall()` where no post-execution enforcement is possible.
- [ ] Document that `GuardV0.validateCall()` alone can only perform pre-call validation. The maximum-settlement guarantee exists in the execution-aware `TradingStrategyModuleV0.performCall` path; standalone `GuardV0` may retain unlimited legacy Lagoon allowlisting but cannot enable a cap.

## Task 3: Link and deploy `LagoonLib`

**Files:**

- Modify: `eth_defi/deploy.py`
- Modify: `eth_defi/erc_4626/vault_protocol/lagoon/deployment.py`
- Modify: explicit library maps in affected guard tests
- Modify: `tests/lagoon/test_lagoon_module_library_deploy.py`

- [ ] Add `LagoonLib` to `GUARD_LIBRARIES`, `GUARD_FORGE_LIBRARY_SOURCES` and `SAFE_INTEGRATION_FORGE_LIBRARY_SOURCES`.
- [ ] Add an explicit `lagoon`/`enable_lagoon_settlement` deployment flag to `deploy_safe_trading_strategy_module`. Default it to the current Lagoon-enabled behaviour so existing deployment callers do not silently link a zero address.
- [ ] Deploy and link `LagoonLib` on source chains with a Lagoon vault; link the zero address on satellite/no-vault chains where Lagoon selectors cannot be configured.
- [ ] Extend the focused deployment tests to assert both the deployed-library path and the zero-linked path.
- [ ] On a zero-linked deployment, prove `whitelistLagoon`, limit configuration and Lagoon settlement validation all revert rather than silently succeeding. This is a mandatory fail-closed regression test.
- [ ] Update existing test library dictionaries to include `LagoonLib: ZERO_ADDRESS` unless that test exercises Lagoon settlement.

## Task 4: Expose deployment configuration

**Files:**

- Modify: `eth_defi/erc_4626/vault_protocol/lagoon/deployment.py`
- Modify/add: focused Lagoon deployment configuration tests

- [ ] Add `max_settlement_amount: Decimal | None = None` to `LagoonConfig`. Keep it out of `LagoonDeploymentParameters`, because it is a Guard policy rather than a Lagoon vault initialisation parameter.
- [ ] Preserve legacy keyword-based deployment entry points with an optional default-`None` argument and map it into `LagoonConfig`.
- [ ] Convert a configured human amount with `TokenDetails.convert_to_raw()`; do not hard-code token decimals or use float arithmetic.
- [ ] Construct the `LagoonVault` wrapper early enough to obtain `asset()` and the v0.5 Silo address. Continue using the existing canonical v0.5 storage-slot reader in `LagoonVault.silo_address`, since stock v0.5 removed the public getter.
- [ ] When a limit is configured, call the new atomic allowlist-with-limit function and assert the full configuration through contract getters. When it is `None`, call the legacy `whitelistLagoon` path.
- [ ] Assert in deployment/setup code that a non-`None` limit is only configured on an execution-aware `TradingStrategyModuleV0`, never a standalone validation-only `GuardV0`.
- [ ] Add the limit, underlying token and Silo to deployment logging/`WhitelistEntry` output without changing old serialised deployment objects.

## Task 5: Keep configuration discovery complete

**Files:**

- Modify: `eth_defi/erc_4626/vault_protocol/lagoon/config_event_scanner.py`
- Modify/add: configuration scanner tests

- [ ] Add `guard/LagoonLib.json` and the new configuration event name to `GUARD_EVENT_ABI_FILES` and `GUARD_CONFIG_EVENT_NAMES`.
- [ ] After ABI regeneration, verify `LagoonVaultApproved` is still discoverable from at least one configured ABI file and add a scanner regression test that decodes a legacy pre-refactor approval log.
- [ ] Extend `ChainGuardConfig` with a backwards-compatible settlement-limit record containing vault, asset, Silo, enabled state and raw maximum.
- [ ] Process chronological limit updates/disables so the scanner reports current state, while continuing to discover old unlimited vaults from `LagoonVaultApproved` alone.
- [ ] Show unlimited versus capped status in human-readable and Markdown reports. Do not treat the per-settlement success event as configuration.

## Task 6: Add security regression tests

**Files:**

- Create: `contracts/guard/test/LagoonLib.t.sol` or an equivalent focused Forge test
- Create/modify: a stock-v0.5 test fixture using `contracts/lagoon-v0/src/v0.5.0`
- Add: `tests/lagoon/test_lagoon_max_settlement.py`

- [ ] Add an Anvil-based end-to-end fixture that deploys the stock Lagoon v0.5 contracts, ERC-20 underlying, Safe and linked `TradingStrategyModuleV0`, then enables an asset manager and configures the maximum settlement amount through the real deployment/setup path.
- [ ] Add an Anvil happy-path test: create a real deposit queue below the cap, settle it through the asset manager's `performCall`, and assert the receipt succeeds, the Silo is debited, the Safe receives the underlying, Lagoon settlement identifiers advance and the user can claim shares.
- [ ] Add an Anvil rejected-path test: create a real deposit queue above the cap, attempt settlement through the asset manager's `performCall`, assert the maximum-settlement custom error, and verify the Silo/Safe/vault balances, Lagoon total assets/supply, settlement identifiers and user claimability are identical before and after the reverted transaction.
- [ ] Repeat the Anvil happy and rejected paths for redemption, including proof that an accepted redemption transfers underlying into the Lagoon vault and a rejected redemption leaves the queued shares and Safe funds untouched.
- [ ] Add an Anvil combined-flow rejection test for `settleDeposit(uint256)`: queue both deposits and redemptions so each direction appears in the same call, make their gross sum exceed the cap while their Safe net change remains below it, and prove that gross accounting rejects and rolls back the call.
- [ ] Prove legacy allowlisting remains unlimited and all four selectors/getters remain available.
- [ ] Use a minimal legacy Lagoon mock to prove a configured cap is enforced for the no-argument `settleDeposit()` and `settleRedeem()` selectors as promised by the compatibility scope; stock v0.5 supplies the primary `uint256` integration coverage.
- [ ] Prove only the Guard owner can set, update or disable a limit, and invalid asset/Silo configurations fail.
- [ ] Cover deposit-only and redemption-only settlements below, exactly at and one raw unit above the maximum.
- [ ] Cover combined v0.5 `settleDeposit(uint256)` where deposit plus redemption exceeds the cap even though the Safe's net underlying change is small.
- [ ] On rejection, assert absolute expected balances, Lagoon total assets/supply, epoch/settle identifiers and request claimability, as well as equality to the pre-call snapshots. Assert the custom error's actual and maximum values, not only its selector.
- [ ] After an oversized asset-manager rejection, settle the unchanged queue through governance and assert success. This proves both rollback/recoverability and the intentional governance bypass in one flow.
- [ ] Cover zero-queue settlement, an explicitly enabled zero cap, limit disablement and an unlimited legacy configuration.
- [ ] Prove governance is not capped, unauthorised senders and unapproved vaults remain rejected, and original Lagoon revert data is not masked by the post-check.
- [ ] Exercise both `performCall` overloads and confirm unrelated guarded calls are unchanged.
- [ ] Assert the Lagoon deployment only registers the four settlement selectors on the vault target and does not expose a generic passthrough or vault `multicall` route that could avoid snapshot creation.
- [ ] Include at least one integration test against the stock Lagoon v0.5 contracts rather than only a permissive mock.

## Task 7: Build, size-check and document

**Files:**

- Modify: `contracts/guard/README.md`
- Modify: `docs/README-contract-size.md`
- Regenerate affected ABI JSON files through the compiler

- [ ] Document the reject approach, gross-delta formula, governance/direct-Safe bypass, unlimited default, standard-token limitation and the operational consequence of an oversized queue. State explicitly that this cap does not validate the asset manager's `_newTotalAssets` NAV.
- [ ] Run `make guard safe-integration` and regenerate ABIs, including `LagoonLib.json`.
- [ ] Measure deployed bytecode and update the contract-size table/library-pattern section. Fail the work if either deployable contract exceeds the EIP-170 limit.
- [ ] Run Solidity tests and targeted Python tests. Before pytest, ensure `.local-test.env` exists according to repository worktree instructions, then use commands of this form:

```bash
source .local-test.env && poetry run pytest tests/lagoon/test_lagoon_module_library_deploy.py tests/lagoon/test_lagoon_max_settlement.py -v
```

- [ ] Run the Anvil maximum-settlement tests separately with verbose transaction diagnostics so both the accepted and reverted asset-manager paths are visible:

```bash
source .local-test.env && poetry run pytest tests/lagoon/test_lagoon_max_settlement.py -v --log-cli-level=info
```

- [ ] Run the focused configuration-scanner tests and existing Lagoon deployment/settlement regression tests, with a 180-second command timeout.
- [ ] Run `poetry run ruff format` for changed Python files and rebuild once more to ensure generated artifacts match source.

## Acceptance criteria

- An asset manager cannot persist a stock Lagoon v0.5 settlement whose gross underlying transfer exceeds the configured maximum.
- Rejection is atomic and leaves the queue and all vault/Safe/token state unchanged.
- A combined deposit/redemption cannot evade the cap through netting.
- A missing/zero-linked `LagoonLib` fails closed on configuration and settlement paths.
- A settlement limit cannot be enabled on standalone `GuardV0`, where no post-execution check exists.
- Old deployments/configuration paths remain unlimited, old public selectors/getters remain callable, and governance behaviour is unchanged.
- All Lagoon-specific state and validation live in `LagoonLib.sol`; `GuardV0Base` and the module contain only generic routing/execution glue.
- Deployment tooling, event-based configuration reports, ABI artifacts, focused tests and contract-size documentation all reflect the new feature.
