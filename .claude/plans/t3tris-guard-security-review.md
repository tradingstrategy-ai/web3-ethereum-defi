# T3tris guard security review

- Date: 2026-11-14.
- Plan: [T3tris deposit and redemption support](t3tris-deposit-redeem-support.md), step 7.
- Scope: `contracts/guard/src/GuardV0Base.sol` and
  `contracts/guard/script/ComputeSelectors.s.sol` in commits `84596e7b4`
  (calldata validators) and `03841b30b` (T3tris admission), with
  `tests/guard/test_guard_simple_vault_t3tris.py` for test adequacy.
- Reviewed revision: `a1f96cd4c9ae6ea86a8a0f3183fbe87b5260bae4`.
- Brief SHA-256: `aa86b11ca9f60ea5dfdb5846e71e294b4ec0d891c650d730805efc20a87c7d09`.
  The brief states the compromised-asset-manager threat model, the T3tris
  facts, a five-area checklist and the test results the reviewers could not
  reproduce read-only.
- Reviewers, run in parallel, grounded in the worktree and read-only:
  - Codex CLI 0.167.1, model `gpt-6-astra` (smoke-tested), 28 commands.
    Invocation: `timeout 900 codex exec --json --sandbox read-only -m gpt-6-astra "<brief>" < /dev/null`.
    Completion: final `turn.completed` event.
  - Claude Code 2.1.322, model `claude-opus-5-5` (smoke-tested and confirmed
    in the result's model usage), high effort, 21 turns. Invocation:
    `timeout 900 claude -p "<brief>" --model claude-opus-5-5 --effort high --permission-mode dontAsk --allowedTools "Bash,Read,Grep,Glob" --disallowedTools "Edit,Write,MultiEdit,Skill,Task" --output-format stream-json --verbose --no-session-persistence < /dev/null`.
    Completion: final `result` event, `subtype=success`, `is_error=false`.
- Both reviewers confirmed the worktree, branch and the two scoped diffs.

Result: no critical, high or medium finding from either reviewer. The review
passes the plan's bar once the low finding is fixed, which it now is.

## Disposition

Each finding was checked against the code before acting.

| Finding | Assessment | Action |
| --- | --- | --- |
| Codex I-1 / Claude L-1: missing negative cases for share-token `approve` and `transferFrom`, `withdraw`, both `mint` overloads, the Permit2 `deposit` overload and a non-governance `whitelistT3tris()` call | Valid test gap. The guard already rejects all of them; the tests would catch a regression | Added to `test_t3tris_guard_rejects_redirection` |
| Codex I-1 / Claude L-1: no malformed-calldata cases | Valid test gap | Added a short payload, a dirty address word, `unsafe` encoded as 2 on two calls and an out-of-range `permit2Data` offset |
| Codex I-1: auto-claim of an earlier settled request is not exercised | Valid. The manager never builds this sequence, but a guarded asset manager can | Added `test_t3tris_new_request_auto_claims_to_custody` for both directions through `performCall()` |
| Claude L-1: rejection cases run only through `SimpleVaultV0` | Same `GuardV0Base` code in the Safe module; low value | Not adopted. The Safe module runs the full positive lifecycle |
| Claude I-1: whitelist paths do not check the guard version | Valid operational gap: an old guard reverts the whole batch | `setup_guard()` and `scripts/lagoon/whitelist-vaults.py` assert internal version 5 or newer before any T3tris broadcast |
| Claude I-1: stale `== 2` version checks in `eth_defi/enzyme/generic_adapter_vault.py` and `eth_defi/foundry/forge.py` | Pre-existing and unrelated to this change | Not changed |
| Claude I-2: `whitelistERC4626()` on the same vault adds back the synchronous exits and share-token call sites | Valid, governance-only | Documented in the `whitelistT3tris()` NatSpec and `contracts/guard/README.md`; the Python paths pick exactly one whitelist |
| Claude I-3: approval destinations are global, so standing allowances to an upgradeable vault depend on its admin keys | Pre-existing design shared by every vault whitelist | Accepted; recorded here |
| Claude I-4: `permit2Data` equivalence assumes the vault uses the standard ABI decoder | Cannot be verified without T3tris source | Accepted; recorded here |
| Claude I-4: `claimDeposit` reuses `_validate_NaraUnstake` | Correct but misleading name | Renamed to `_validate_SingleReceiver`; bytecode unchanged |

After the fixes in commit `69ab8729d`:

- The rebuilt `GuardV0` and `TradingStrategyModuleV0` bytecode is identical
  to the reviewed revision (23,942 and 21,978 bytes).
- `forge test`: 18 passed. T3tris guard tests: 5 passed, twice, with identical
  amounts. `tests/guard` and `tests/safe-integration`: 158 passed, 6 skipped,
  1 failed; the failure is the pre-existing
  `test_lagoon_hypercore_vault_whitelisting_is_batched` upstream
  `eth_getLogs` range limit, which also fails on the parent of `84596e7b4`.
- Blocking-only re-review of `69ab8729d` with the same Codex model and
  invocation pattern: "No blocking findings."

## Codex output (verbatim)

No critical, high, medium or low findings. One informational test-coverage finding.

**I-1 — Missing boundary tests** — `tests/guard/test_guard_simple_vault_t3tris.py:294`

The rejection list omits share-token `approve(vault, amount)` and standard `withdraw(amount, receiver, owner)`. It also lacks malformed-calldata cases and a lifecycle exercising a request’s automatic claim of an earlier settlement.

**Exploit path:** none demonstrated; the current guard blocks the omitted selectors. The missing approval case would detect a regression allowing the asset manager to grant access to custody shares. Existing lifecycle tests explicitly claim before making another request, so they do not exercise automatic payouts.

**Smallest fix:** add these selector rejection cases, malformed address/bool/dynamic-offset cases, and a settled-but-unclaimed request followed by another request through `performCall()`.

Checked areas:

- **Calldata refactor — no finding.** All 17 changes are parameter-location substitutions; decode tuples, checks and revert strings are unchanged. All callers pass the dispatcher’s calldata slice. The affected decodes contain only static types: short tuples and dirty address words retain ABI validation; trailing bytes retain existing acceptance. No dynamic-offset decoder was changed. The Atoma exact-32-byte check remains unchanged at `GuardV0Base.sol:793`. This is source-level equivalence analysis, not differential execution.

- **T3tris validation — no finding.** Independently recomputed all four selectors using `cast sig`: `afd933fb`, `de56603c`, `5fb8c2cf`, `9cb4b041`. Their signatures and decode order match `IVault.json`. All seven address positions are checked, including deposit `receiver` and redemption `previousClaimReceiver`. Owners must belong to `allowedReceivers`; they are not hardcoded to custody, consistent with the supplied threat model. All three `unsafe` arguments must be false, and deposit `permit2Data` must decode to empty bytes. ABI decoding checks dynamic bounds; noncanonical encodings that decode successfully do not bypass these value checks.

- **Whitelisting — no finding under the stated configuration.** `GuardV0Base.sol:1062` is `onlyGuardOwner`. It adds precisely four vault call sites, the vault as an approval destination, and underlying-token transfer/approval sites. Marking the share token as an allowed asset does **not** register its transfer or approval selectors. The call-site gate at line 731 remains decisive.

  Whitelists are additive: also calling `whitelistERC4626()` enables standard deposit/withdraw/redeem and share transfer/approval, alongside its legacy selectors. Supported calls then become executable subject to their existing receiver, owner and spender checks; unsupported calls merely pass guard admission and reach the target. It does not register T3tris unsafe exits or settlement. With `anyAsset=true`, share approvals become reachable without registration, but spender checks remain; this configuration is explicitly excluded from the threat model.

- **Integration — no finding.** Neither commit changes storage declarations, inheritance or library dependencies. The Safe module forwards the same calldata it validates using `CALL`. Existing deployed bytecode is unaffected. The internal version correctly changes from 4 to 5. Reported runtime sizes leave **634 bytes** for `TradingStrategyModuleV0` and **2,598 bytes** for `GuardV0` below EIP-170; I did not rebuild or independently measure them.

- **Tests — coverage otherwise matches the checklist.** Every address position, each `unsafe=true` position and nonempty Permit2 data have negative cases. Both custody implementations run the positive lifecycle through `performCall()`. Rejection tests directly invoke `validateCall()` and cover settlement, decreases, forced deposit claims, unsafe exits, multicall and several unregistered standard selectors.

Confirmed the requested working directory and branch; the last four commits are `a1f96cd4c`, `03841b30b`, `84596e7b4`, and `674d29237`. Reviewed both scoped diffs without edits, tests, builds or agents.

**Verdict:** No exploitable custody-redirection or request-stranding path was identified in these changes under the supplied trust assumptions. The implementation is suitable for approval, with the informational coverage gap recorded. Runtime confidence relies on the implementing agent’s reported results; this review independently checked source, ABI positions, selectors and admission paths.

## Claude output (verbatim)

No critical, high or medium issues turned up in either commit. I found one low-severity test gap and a few informational points. I worked from the source only: as instructed I did not run tests or build the contracts, so the pass counts and sizes below are the implementing agent's.

### Findings

**L-1: tests don't cover share-token `approve`/`transferFrom` or a non-owner `whitelistT3tris` call** (`tests/guard/test_guard_simple_vault_t3tris.py:292-313`)
- The vault is an allowed approval destination, and `whitelistT3tris` also calls `allowAsset(vault)` (`GuardV0Base.sol:1068-1069`). If a later change registered `SEL_APPROVE` on the vault, the manager could approve the custody's vault shares to *any* approval destination, such as a router or another vault. Only the spender is checked, and the share token would already count as an allowed asset. No current test would catch that, because the test only checks share-token `transfer`.
- Smallest fix: add `f.approve(<any allowed spender>, 1)` and `f.transferFrom(me, outsider, 1)` to the `unregistered` list. Also assert that `guard.functions.whitelistT3tris(...).transact({"from": manager})` reverts.
- Smaller gaps:
  - The rejection test runs only through `SimpleVaultV0`; the Safe module is exercised only on the positive lifecycle. The validation code is the same `GuardV0Base`, so this is low risk.
  - `mint(uint256,address)`, `withdraw(...)`, `deposit(address,uint256,bytes)` and `mint(address,uint256,bytes)` are not listed as unregistered. The call-site check rejects them anyway.
  - No test feeds malformed calldata: a short payload, an address with dirty high bits, `unsafe` encoded as `2`, or a `permit2Data` offset or length that runs past the end. The decoder rejects all of these, but nothing locks that behaviour in.

**I-1: the Python whitelist paths don't check the guard's version** (`scripts/lagoon/whitelist-vaults.py:74`, `eth_defi/erc_4626/vault_protocol/lagoon/deployment.py:2154`)
- On an existing module at version 4 or lower, `whitelistT3tris` does not exist. The call reverts, and because each script batches its calls (one MultiSend, one multicall per chunk), every other vault in that batch fails too. This fails closed, so it's an operational problem rather than a security one.
- Fix: before encoding the call, assert `getInternalVersion() >= 5` whenever a `T3trisVault` is present.
- Separately, two old checks still expect version `== 2` and were already wrong at version 4: an assert at `eth_defi/enzyme/generic_adapter_vault.py:393` and a docstring example at `eth_defi/foundry/forge.py:229`.

**I-2: calling `whitelistERC4626()` on the same vault adds back what `whitelistT3tris` leaves out** (`GuardV0Base.sol:1093-1127`)
- It would register `deposit(uint256,address)`, `withdraw`/`redeem(uint256,address,address)`, and share-token `transfer` and `approve`. In order:
  - `deposit` reverts while the vault is closed (`maxDeposit` is 0).
  - `withdraw`/`redeem` still have their receiver and owner checked.
  - `transfer` is limited to withdraw destinations, which governance sets.
  - `approve` is limited to approval destinations; with `allowAsset(vault)` this makes the shares tradable through existing swap integrations.
- None of these opens a path to custody outside the governance-trusted set. But the synchronous exits the T3tris policy leaves out on purpose come back without the `unsafe` pinning, if the vault treats them that way.
- The Python code picks exactly one whitelist function per vault. A one-line note in the `whitelistT3tris` NatSpec and `contracts/guard/README.md` saying "do not combine with `whitelistERC4626`" would be enough.

**I-3: approval destinations are global** (`GuardV0Base.sol:1068`)
- Once the vault is an approval destination, the manager can approve *any* whitelisted token with a registered `approve` to it, not just the vault's asset.
- The T3tris vault is a UUPS proxy (upgradeable by its admin) and exposes `sweep(address)`. Standing allowances are therefore exposed to the T3tris upgrade and admin keys, which the stated threat model does not explicitly list as trusted.
- Every other vault whitelist works the same way, so this was not introduced here. I'm recording it so the trust assumption is explicit.

**I-4: the `permit2Data` check assumes the vault uses the standard decoder** (`GuardV0Base.sol:1003-1008`)
- The guard follows the ABI offset, so a non-canonical encoding is judged exactly as Solidity's ABI decoder would judge it.
- Equivalence with the vault holds only if the unverified T3tris implementation also decodes with the standard Solidity decoder. That cannot be checked from source.
- Cosmetic: `claimDeposit` reuses `_validate_NaraUnstake` (`GuardV0Base.sol:841`). It is correct (one address, "Receiver not whitelisted"), but only the name is misleading.

### Checklist areas with no finding

1. **Calldata refactor.** The diff changes only the parameter location on 17 validators, and every caller passes the `bytes calldata` slice `callDataWithSelector[4:]` (`GuardV0Base.sol:715`).
   - The old code copied that same slice into memory, so the decoder sees identical bounds.
   - With solc 0.8.26 and ABI coder v2, decoding from calldata and from memory apply the same checks: minimum head size, address and bool range checks, and the same handling of trailing data. Short input, dirty bits and out-of-range bools are rejected identically.
   - All 17 refactored validators decode static types only; the one dynamic `bytes` decode is the new T3tris validator.
   - No memory caller remains; `validate_cctpDepositForBurn` already took calldata.
   - The Atoma `length == 32` check (`GuardV0Base.sol:793`) and every revert string are unchanged.

2. **T3tris validation.**
   - I computed the four selectors from `IVault.json` with `cast sig`, and they match `0xafd933fb`, `0xde56603c`, `0x5fb8c2cf` and `0x9cb4b041`.
   - None of the four collides with any other `SEL_*` constant in `contracts/guard/src` or `contracts/safe-integration/src`.
   - The decode types and positions match the ABI: `(address,bool,uint256,bytes)`, `(address)`, `(address,address,address,bool,uint256)` and `(address,address,bool)`.
   - Every address is checked at its correct position, including the two auto-claim receivers: `receiver` on `requestDeposit` and `previousClaimReceiver` on `requestRedeem`.
   - `owner` goes through `_requireAllowedOwner`. Allowed receivers are only the custody address: `SimpleVaultV0.sol:43`, and the Safe at `deployment.py:2074`.
   - `unsafe` is pinned to false on all three calls that have it, and `permit2Data` must be empty.
   - Left unregistered: `decrease*`, `forceClaimDeposit`, `settle*`, `unsafeRedeem`, `unsafeWithdraw`, `claimFees` and `multicall`.

3. **Whitelisting.**
   - `whitelistT3tris` is `external onlyGuardOwner`. The `allowCallSite` calls inside it re-check `onlyGuardOwner` with the same `msg.sender`, and calls through `multicall` keep the sender.
   - It registers exactly the four T3tris call sites, plus `transfer` and `approve` on the denomination token only.
   - The share token's `transfer`/`approve` are not registered.
   - With `anyAsset` off, the call-site check rejects every other selector. With `anyAsset` on, only `approve` skips the call-site check, and the spender check still applies (this predates the change).

4. **Integration.**
   - Neither commit adds a state variable; the new selectors are file-level constants. Storage layout and library links are unchanged.
   - Existing deployments are untouched: their bytecode has no T3tris branch, so they reject these selectors with "Unknown function selector".
   - The internal version goes from 4 to 5 with a comment, and the only `>=` consumer (the Lagoon settlement check) still passes.
   - The reported sizes are below the EIP-170 limit: `TradingStrategyModuleV0` 23,942 bytes (634 to spare) and `GuardV0` 21,978 bytes.

5. **Tests.**
   - Every validated address position has a negative case: 5 receiver and 2 owner substitutions.
   - All four flag cases are covered: `unsafe=true` on each of the three calls, and a non-empty `permit2Data`.
   - The positive lifecycle runs through `performCall()` on both `SimpleVaultV0` and the Safe module.
   - Eleven unregistered selectors are rejected. The missing cases are listed under L-1.

### Verdict

Both commits are sound under the stated threat model. The calldata refactor changes only where the input is read from. Each validator still sees the same slice, with the same decode checks and revert strings. The T3tris policy checks every address that can receive or give up value, including both auto-claim receivers, and pins `unsafe` and `permit2Data`. `whitelistT3tris` is restricted to governance and registers only the four request/claim selectors, without the share token's `transfer`/`approve`. I found no path for a compromised asset manager to move, redirect or strand custody assets beyond the amount-unbounded requests the guard already accepts by design. Before shipping, I recommend adding the L-1 negative cases (share-token `approve`/`transferFrom` and a non-owner `whitelistT3tris`) and the I-1 version check.
