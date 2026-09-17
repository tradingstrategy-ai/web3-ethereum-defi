# Fix Lagoon settlement window budget

Status: **implemented**.
Priority: **P1**. Written 2026-09-17.

## Goal

Replace GuardV0's one-non-zero-settlement-per-cooldown rule with the intended
single-vault cumulative settlement budget.

For a 5,000 USDC cap and a 24-hour window, the asset-manager module must accept
any number of Lagoon settlements while the sum of their gross deposit and
redemption flows remains at or below 5,000 USDC. The first non-zero settlement
starts a fixed window. A settlement at or after expiry starts a fresh window;
empty settlements do not start, extend, or reset one. Direct Safe governance
settlements continue to bypass the module policy.

The Lighter bootstrap path must leave a newly deployed vault with 20 USDC
subscribed, 1 USDC credited to Lighter, and 19 USDC in the Safe. Every
automated Lagoon settlement uses the same 5,000-USDC window, so the first
ordinary 20-USDC investor settlement succeeds immediately with 40 USDC of
gross usage.

## Diagnosis

The balance-delta machinery is already the right enforcement point:

- `LagoonLib.capturePostCallContext()` snapshots the pending Silo and Lagoon
  vault balances before the Safe call.
- `LagoonLib.validatePostCall()` separately calculates the Silo decrease
  (deposits) and vault increase (redemptions), then adds them. This already
  prevents opposing flows from netting.
- A post-call revert atomically rolls back the Lagoon settlement, transfers,
  logs, and Guard state.

The defect is the policy layered on top of that measurement. The current code
compares only the new call's gross flow with `maxSettlementAmount`, records
`lastSettlementTimestamp`, and rejects every later non-zero call until
`settlementCooldown` has elapsed. It never stores cumulative usage. Therefore
1 USDC followed by 20 USDC is rejected even though 21 USDC is far below a
5,000-USDC daily allowance.

The Python deployment API, guard report, tests, comments, and events all
describe this as a cooldown, so they currently reinforce the incorrect
semantics. Guard internal version 3 and TradingStrategyModuleV0 version `v0.5`
identify that behaviour.

## Chosen design

Use one fixed-window accumulator in `LagoonLib`:

```text
window starts on first non-zero automated settlement
new usage = already-settled gross flow + new deposit assets + new redemption assets
accept when new usage <= maximum settlement amount
reject when new usage > maximum settlement amount
next non-zero settlement at/after window end starts at usage zero
```

This is not a sliding window and a successful settlement does not move the
window end. That keeps the policy small, predictable, and cheap to inspect.

Store the configured window duration, window start timestamp, and gross usage
in the existing singleton Lagoon diamond storage. Preserve the storage slot and
field order, appending only the new usage word. Rename source-level variables
and NatSpec from cooldown to window where doing so does not create another
onchain entry point. The existing Solidity
`whitelistLagoonWithSettlementLimitAndCooldown(...)`,
`getLagoonSettlementCooldownConfig(...)`, and
`LagoonSettlementCooldownSet` ABI signatures may remain as compatibility
surfaces because replacing them with duplicate wrapper functions would consume
the module's small EIP-170 margin. Their documentation and ABI argument names
must explicitly say that the value is a fixed budget-window duration, not a
minimum delay between calls.

Make `settlement_window` the canonical Python deployment and reporting name;
do not continue advertising `settlement_cooldown` in new configuration. This
feature is recent and unused outside its focused tests in this repository, so
prefer one clear public name over a permanent pair of Python aliases. Keep the
Solidity compatibility selectors and use the version getters to distinguish
old cooldown deployments from the new cumulative-budget semantics.

For bootstrap, do not add an exemption, a second configuration step, or an
unlimited interval. Configure the normal settlement budget during deployment
and apply it identically to every automated Lagoon settlement.

Do not add generic multi-vault accounting, bootstrap exemptions, or a Guard
function that lets an asset manager reset usage. Reapplying or disabling the
policy remains a Safe-governance action.

## Contract changes

### 1. Add cumulative state without changing the balance invariant

Files:

- `contracts/guard/src/lib/LagoonLib.sol`
- `contracts/guard/src/GuardV0Base.sol`
- `contracts/safe-integration/src/TradingStrategyModuleV0.sol`

Changes:

- Treat the configured positive duration as `settlementWindow`, with the
  existing 24-hour default renamed to `DEFAULT_LAGOON_SETTLEMENT_WINDOW` in
  source.
- Treat the persisted timestamp as the fixed window's start, not the most
  recent settlement timestamp, and append `settledAmountInWindow` to
  `LagoonStorage`.
- Include the cap, window duration, window start, and current usage in the
  opaque `SettlementSnapshot`, alongside the existing token balances.
- Leave deposit and redemption measurement unchanged:
  `grossSettlementAmount = depositAssets + redeemAssets`.
- For a non-zero gross amount:
  - consider a missing or expired window fresh;
  - use a subtraction-based limit check
    (`newGross > max - alreadyUsed`) so the cumulative check cannot overflow;
  - reject with an error that exposes already-used, new-gross, and maximum
    amounts;
  - otherwise persist the new start (only for a fresh window) and cumulative
    usage.
- At `block.timestamp >= windowStart + settlementWindow`, allow the next
  non-zero settlement to start a new window. The implementation must use the
  repository's Forge timestamp lint annotation at the comparison.
- For zero gross flow, emit the existing validation measurement if useful for
  diagnostics, but do not write start, usage, or expiry state. An empty call
  after expiry must not itself perform the reset; the next non-zero call does.
- Reconfiguring the same vault's policy through governance clears window start
  and usage. Calling legacy `whitelistLagoon()` clears all budget state and
  restores unlimited mode.
- Preserve the existing per-call direction checks and atomic rollback
  behaviour. Do not move enforcement before execution or attempt partial
  Lagoon settlement.

### 2. Make accumulated state observable

- Keep `getLagoonSettlementConfig()`'s five-value shape for callers that only
  need the static cap.
- Keep the existing three-value cooldown getter selector for ABI compatibility,
  but document and return `(settlementWindow, settledAmountInWindow,
  windowEndTimestamp)` on new-version modules. Use the new, unambiguous
  `LagoonSettlementWindowLimitExceeded(uint256 alreadyUsed,uint256 newGross,uint256 maxAmount)`
  for a cumulative-budget rejection. An expired window should report
  zero effective usage and no active end even if stale storage is left in place
  until the next non-zero settlement.
- Keep `getLagoonSettlementSafetyConfig()` compact. Preserve its existing tuple
  width if possible by replacing the old last/next-settlement meanings with
  cumulative usage and active window end. Callers must branch on the internal
  version before interpreting these fields.
- Add or revise a Lagoon library event so every accepted non-zero settlement
  exposes the call's gross flow, cumulative usage, cap, window start, and
  window end. Empty and rejected calls must not emit a budget-update event.
- Retain legacy error/event declarations when they are needed to decode older
  deployed modules, but stop emitting the cooldown-start event for new budget
  activity.
- Bump `GuardV0Base.getInternalVersion()` to 4 and
  `TradingStrategyModuleV0.getTradingStrategyModuleVersion()` to `v0.6`, with
  version-history comments that state the semantic change.

Before settling the getter tuple, compile both top-level contracts. If the
additional external getter would push `TradingStrategyModuleV0` over EIP-170,
reuse the existing tuple slots as described above rather than adding duplicate
cooldown/window methods.

## Foundry policy tests

Add `contracts/guard/test/LagoonSettlementBudget.t.sol`. Keep the test fully
local and deterministic: a small harness should call LagoonLib's real
configuration, snapshot, and post-call validation functions around a mock
settlement target. The mock token/Silo/vault need only reproduce the two
documented balance directions. Execute the mock settlement and validation in
one harness transaction so a failed post-call check proves atomic rollback.

Add these focused test functions (names may be adjusted to repository style):

1. `testMultipleSettlementsAccumulateBelowCap`
   - Configure `5_000e6` and one day.
   - Settle `1e6`, then `20e6` without advancing time.
   - Assert both succeed, one fixed window remains active, and usage is
     `21e6`.
2. `testExactCapSucceedsAndNextAmountRevertsAtomically`
   - Use multiple calls whose sum is exactly `5_000e6` and assert equality is
     accepted.
   - Attempt one more raw USDC unit.
   - Assert the cumulative-limit error and that token balances, start, usage,
     and window end are unchanged.
3. `testDepositAndRedemptionUseGrossFlow`
   - In one settlement move deposits and redemptions in opposite directions so
     the net Safe movement is small but their gross sum exceeds `5_000e6`.
   - Assert the transaction reverts and neither flow is committed.
4. `testExpiredWindowStartsFreshBudget`
   - Consume part or all of the allowance, warp to at least the fixed window
     end, and settle again.
   - Assert the new call succeeds with a new start and usage equal only to the
     new gross flow.
5. `testEmptySettlementDoesNotCreateExtendOrResetWindow`
   - Test an empty settlement before the first non-zero call, during an active
     window, and after expiry.
   - Assert start/end/usage are unchanged in all three cases and no
     budget-update event is emitted.

The Foundry suite is the authoritative, fast policy coverage. Python fork tests
should prove deployment wiring and real Lagoon/Lighter integration, not repeat
every arithmetic permutation.

## Python deployment and bootstrap changes

### 3. Rename the public duration and wire the new state

Files:

- `eth_defi/erc_4626/vault_protocol/lagoon/deployment.py`
- `eth_defi/erc_4626/vault_protocol/lagoon/config_event_scanner.py`
- `tests/lagoon/test_lagoon_max_settlement.py`
- `tests/lagoon/test_lagoon_config_event_scanner_scan_metadata.py`

Changes:

- Rename `DEFAULT_LAGOON_SETTLEMENT_COOLDOWN` to
  `DEFAULT_LAGOON_SETTLEMENT_WINDOW` and public
  `settlement_cooldown`/`lagoon_settlement_cooldown` arguments to
  `settlement_window`/`lagoon_settlement_window`.
- Validate that the window is a positive integer only when a cap is enabled.
  Preserve `None` as unlimited mode and zero as a valid zero-movement cap.
- Update `setup_guard()` to pass the duration to the existing compatible
  Solidity configuration selector and verify zero initial usage/no active
  window from the new-version getter semantics.
- Update whitelist summaries from `...s cooldown` to `...s settlement window`
  and raise `LAGOON_SETTLEMENT_LIMIT_INTERNAL_VERSION` to 4.
- Rename `LagoonSettlementLimitConfig.settlement_cooldown` to
  `settlement_window`; teach the event scanner to interpret the compatible
  configuration event argument as a duration. Preserve decoding of historical
  cooldown deployments. Because an old and new configuration event have the
  same signature and an event-only scan does not know the module version,
  render historical reports with neutral wording such as “configured duration”
  unless the version has been queried; do not mislabel an old deployment as a
  cumulative budget.
- Rewrite the existing Base-fork settlement test so it demonstrates two
  immediate below-cap calls accumulating usage, expiry reset, empty-call
  stability, over-budget atomic rollback, and direct Safe recovery. Remove all
  assertions that expect the second non-zero call to fail merely because time
  has not advanced.
- Keep the existing Base Lagoon fixture at its established fixed block, rather
  than changing unrelated Base fork tests. New Ethereum bootstrap coverage uses
  the canonical midnight shared-fork pattern and matching xdist group.
- Keep both deployment entry routes covered: `LagoonConfig` and direct keyword
  arguments.

### 4. Make the Lighter bootstrap sequence explicit

Files:

- `scripts/lagoon/lagoon-lighter-example.py`
- `eth_defi/lighter/README-lighter-guard.md`
- Add `tests/lagoon/test_lagoon_lighter_bootstrap.py`

For a fresh Lighter deployment, keep this orchestration in the single Lighter
entrypoint rather than changing generic Lagoon deployment for every strategy:

- Deploy the vault/module with Lighter whitelisting and the requested settlement
  cap active.
- Use the existing Lagoon funding flow for an exact 20-USDC initial
  subscription.
- Use the guarded Lighter approval/deposit helper for an exact 1-USDC account
  activation and assert the Safe now holds exactly 19 USDC.
- Immediately admit and settle the first normal investor request under that
  configured budget. Do not special-case bootstrap addresses or amounts in
  Solidity.

The new bootstrap integration test must follow the shared fixed-fork pattern
from `eth_defi/testing/anvil_fork_pool.py`: use `JSON_RPC_ETHEREUM`,
`ETHEREUM_MIDNIGHT_BLOCK`, `anvil_fork_pool`, the matching
`xdist_group("fork:ethereum:midnight")`, and snapshot/revert isolation. It
must exercise the real Ethereum Lighter proxy and the production deployment
helpers end to end:

- deploy one Lighter-enabled Lagoon vault;
- subscribe 20 USDC and activate Lighter with 1 USDC;
- record the real Lighter proxy's USDC balance before activation and assert its
  balance increases by exactly 1 USDC (it may already hold assets at the fixed
  fork block), while the Safe holds exactly 19 USDC;
- immediately request and settle a separate normal 20-USDC investor deposit;
- assert settlement succeeds, effective budget usage is exactly 40 USDC, the
  pending Silo is empty, and the Safe's raw USDC balance is exactly 39 USDC;
- finalise the investor deposit and reconcile claimed shares/underlying with
  the vault's onchain accounting.

This test is a real external integration against a fork of the actual Lighter
contract. Record its focused command/result in the pull-request comment as
required by the repository integration-test policy.

## Documentation and generated artefacts

### 5. Replace cooldown language and rebuild ABIs

Files:

- `contracts/guard/README.md`
- `contracts/safe-integration/src/TradingStrategyModuleV0.sol`
- `docs/README-contract-size.md`
- `eth_defi/lighter/README-lighter-guard.md`
- `CHANGELOG.md`
- generated files under `eth_defi/abi/guard/` and
  `eth_defi/abi/safe-integration/`

Changes:

- Describe an inclusive cumulative gross budget over a fixed settlement
  window, with an example showing 1 + 20 = 21 USDC usage.
- State explicitly that deposits and redemptions are accumulated gross, the
  window does not slide, equality succeeds, empty settlements do not mutate
  it, and direct Safe transactions remain outside the module policy.
- Document the Lighter bootstrap ordering and the 20/1/19 balances.
- Add a dated `fix:` changelog entry; this corrects an existing safety policy,
  not a new generic vault feature.
- Rebuild repo-owned artefacts with `make guard safe-integration`; never edit
  generated ABI JSON manually.
- Update measured GuardV0, TradingStrategyModuleV0, and LagoonLib sizes in
  `docs/README-contract-size.md`. Keep `via_ir = false` and fail the work if
  either deployable top-level contract exceeds 24,576 bytes.

Do not build Sphinx and do not edit generated `_autosummary*` files.

## Verification order

Run focused checks in this order, using an execution timeout of at least
180 seconds for pytest commands:

```shell
cd contracts/guard
forge fmt --check
forge test --match-path test/LagoonSettlementBudget.t.sol -vvv
```

```shell
make guard safe-integration
(cd contracts/guard && forge build --sizes)
(cd contracts/safe-integration && forge build --sizes)
```

```shell
source .local-test.env && poetry run pytest \
  tests/lagoon/test_lagoon_config_event_scanner_scan_metadata.py \
  tests/lagoon/test_lagoon_max_settlement.py \
  -v
```

```shell
source .local-test.env && poetry run pytest \
  tests/lagoon/test_lagoon_lighter_bootstrap.py \
  -v --log-cli-level=info
```

```shell
poetry run ruff format \
  eth_defi/erc_4626/vault_protocol/lagoon/deployment.py \
  eth_defi/erc_4626/vault_protocol/lagoon/config_event_scanner.py \
  tests/lagoon/test_lagoon_max_settlement.py \
  tests/lagoon/test_lagoon_config_event_scanner_scan_metadata.py \
  tests/lagoon/test_lagoon_lighter_bootstrap.py \
  scripts/lagoon/lagoon-lighter-example.py
poetry run ruff check \
  eth_defi/erc_4626/vault_protocol/lagoon/deployment.py \
  eth_defi/erc_4626/vault_protocol/lagoon/config_event_scanner.py \
  tests/lagoon/test_lagoon_max_settlement.py \
  tests/lagoon/test_lagoon_config_event_scanner_scan_metadata.py \
  tests/lagoon/test_lagoon_lighter_bootstrap.py \
  scripts/lagoon/lagoon-lighter-example.py
```

Before running pytest, copy `.local-test.env` from the main checkout if it is
missing in this worktree. Do not modify the file. Finish with `git status
--short` and `git submodule status`; only the files listed by this plan and the
expected generated Guard/Safe artefacts should change. In particular, do not
initialise, update, clean, or modify unrelated contract submodules.

## Acceptance criteria

- Two non-zero settlements in one window succeed when their cumulative gross
  flow is within the cap.
- Exactly 5,000 USDC succeeds; the next non-zero unit fails atomically without
  changing budget or Lagoon state.
- Deposit and redemption flow is accumulated gross, never net.
- The first non-zero settlement after expiry starts a fresh allowance.
- Empty settlements do not create, extend, or reset a window.
- Direct Safe governance settlement still bypasses and does not mutate the
  asset-manager budget.
- A fresh Lighter Lagoon bootstrap produces 20 USDC subscribed, 1 USDC at
  Lighter, and 19 USDC in the Safe while using the configured settlement budget.
- The first normal 20-USDC investor settlement succeeds immediately and leaves
  exactly 40 USDC of recorded usage in the 5,000-USDC window.
- Public Python configuration and human-readable reports use “settlement
  window”/“budget”, not “one-settlement cooldown”.
- GuardV0 and TradingStrategyModuleV0 remain deployable below the EIP-170 size
  limit, generated ABIs match source, and unrelated submodules are untouched.
