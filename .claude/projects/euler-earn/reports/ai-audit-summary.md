# EulerEarn AI mega-audit summary

**Contract:** EulerEarn.sol (930 lines, Solidity 0.8.26)
**Address:** `0x5c1EB5003bA931cCa7CA0fbf7901eF94037064bc` (Ethereum mainnet)
**Instance:** Arb Capital USDT Core (`arbcapUSDTcore`), underlying: USDT
**Type:** ERC-4626 yield aggregator vault (forked from Morpho MetaMorpho, with EVC integration)
**Date:** 2026-03-05

**Tools used:** 7 skill-based auditing pipelines run in parallel

| Skill repo | Stars | Approach |
|------------|------:|----------|
| [trailofbits/skills](https://github.com/trailofbits/skills) | 3,274 | 5 skills: context building, entry point analysis, token integration, code maturity, guidelines |
| [pashov/skills](https://github.com/pashov/skills) | 156 | 4-agent parallelised attack vector scan + adversarial reasoning |
| [kadenzipfel/scv-scan](https://github.com/kadenzipfel/scv-scan) | 77 | 4-phase scan against 36 vulnerability classes |
| [forefy/.context](https://github.com/forefy/.context) | 70 | Multi-expert Solidity audit with protocol-specific checks |
| [quillai-network/qs_skills](https://github.com/quillai-network/qs_skills) | 62 | 6 skills: state invariants, arithmetic, reentrancy, external calls, DoS, signatures |
| [auditmos/skills](https://github.com/auditmos/skills) | 0 | 5 skills: math precision, state validation, reentrancy, oracle, lending |
| [Cyfrin/solskill](https://github.com/Cyfrin/solskill) | 96 | Production code quality and best practices assessment |

---

## Overall assessment

**Security posture:** Moderate -- well-architected contract with strong foundational defences, but several medium-severity issues related to accounting edge cases, silent failure handling, and token compatibility assumptions.

**Code quality:** High -- consistent use of custom errors, comprehensive events, tiered role-based access control, ReentrancyGuard on critical paths, and virtual share offset for inflation protection.

**Deployment risk:** HIGH -- this specific deployment has all privileged roles controlled by a single EOA with zero timelock.

---

## Deduplicated findings

### High severity

| # | Finding | Source files | Tools that found it |
|---|---------|-------------|---------------------|
| H-1 | **Potential underflow in `_accruedFeeAndAssets` can brick the vault.** At L911, `lastTotalAssetsCached - lostAssets` underflows if `lostAssets > lastTotalAssets`, permanently bricking all vault operations. Reachable via H-3 (silent deposit failures) combined with user withdrawals that reduce `lastTotalAssets`. Fix: use `realTotalAssets + lostAssets < lastTotalAssetsCached` instead. | L911 | trailofbits (H-1), forefy (M-1), quillai (related via C2) |
| H-2 | **Reverting strategy vault in `_accruedFeeAndAssets` blocks all operations.** If any strategy vault's `previewRedeem()` reverts (paused, self-destructed, upgraded), ALL deposits, withdrawals, and view functions are blocked. Recovery requires timelock-gated market removal, during which vault is frozen. | L904-908 | quillai (5.2), auditmos (H-01) |
| H-3 | **Accounting desynchronisation via silent strategy deposit failures.** `_supplyStrategy` try/catch silently swallows failures, but `lastTotalAssets` is always incremented by full deposit amount at L707. Idle assets are invisible to `_accruedFeeAndAssets`, permanently inflating `lostAssets`. | L811-835, L697-708 | forefy (H-1), trailofbits (M-2/M-4) |

### Medium severity

| # | Finding | Source files | Tools that found it |
|---|---------|-------------|---------------------|
| M-1 | **Unsafe uint112 cast in `_withdrawStrategy` try body not caught by catch.** At L847, `uint112(config[id].balance - withdrawnShares)` is inside the try success block. Reverts here propagate (NOT caught by catch), defeating the skip-misbehaving-vaults intent. A strategy vault returning rounding-inflated shares causes withdrawal DoS. Same pattern at L415 in `reallocate`. | L847, L415 | pashov ([80]), kadenzipfel (L-1), forefy (M-2), trailofbits (H-2), quillai (2.1/2.2), auditmos (H-02), cyfrin |
| M-2 | **Fee-on-transfer tokens cause accounting insolvency.** No balance-before-after pattern in `_deposit`. If underlying asset has transfer fees, vault credits more shares than assets received. | L698-699 | quillai (4.1), trailofbits (T-4) |
| M-3 | **Rebasing tokens break `lostAssets` tracking.** Negative rebasing causes permanent `lostAssets` inflation; `lostAssets` never decreases even after positive rebase recovery. | L911-917 | quillai (4.2) |
| M-4 | **Read-only reentrancy via stale `totalAssets()` during strategy interactions.** During deposit/withdraw, view functions return transitional state with partially-updated balances. External protocols using EulerEarn for pricing could be misled. | L616-620 | pashov ([60]), quillai (3.3), auditmos (M-03) |
| M-5 | **Silent try/catch blocks prevent off-chain monitoring.** Empty `catch {}` at L828 and L849 swallow ALL errors including unexpected ones. No events emitted on failure, making it impossible to detect strategy issues. | L824-828, L846-849 | kadenzipfel (L-2), trailofbits (M-3), auditmos (L-03), cyfrin |
| M-6 | **Strategy vault return values trusted without verification.** `deposit()` and `withdraw()` return values used directly for balance tracking without post-condition checks. A buggy strategy could return 0 shares for a real deposit. | L825-828, L846-849 | trailofbits (T-1), quillai (4.3) |
| M-7 | **`lostAssets` monotonically increases, causing permanent `totalAssets()` inflation.** By design, `lostAssets` never decreases. Over many interactions, rounding artefacts accumulate. After strategy loss/recovery cycles, fee recipient is charged on recovery. | L911-917 | quillai (C2), auditmos (M-01) |

### Low severity

| # | Finding | Source files | Tools that found it |
|---|---------|-------------|---------------------|
| L-1 | **Supply queue allows duplicate entries, inflating `maxDeposit()`.** `setSupplyQueue` validates non-zero caps but not uniqueness. Documented in NatSpec. | L325-337 | pashov ([55]), forefy (L-1), trailofbits (L-5) |
| L-2 | **Fee changes not timelocked.** Owner can set fee to MAX_FEE immediately. `_accrueInterest()` pre-call mitigates transition but not ongoing elevated fees. | L243-255 | forefy (L-3) |
| L-3 | **`renounceOwnership()` inherited but not overridden.** Accidental call permanently removes owner with no recovery. | inherited | trailofbits (G-2) |
| L-4 | **Permit2 address not validated in constructor.** No zero-address check. If set to address(0), all deposits fail. | L139 | trailofbits (T-2) |
| L-5 | **Permit2 detection via `staticcall` may be unreliable.** Function selector collision possible when checking strategy vault Permit2 support. | L776-777 | trailofbits (T-3), auditmos (L-01) |
| L-6 | **Missing `nonReentrant` on queue management functions.** `updateWithdrawQueue` calls `expectedSupplyAssets` (external call). Theoretical re-entry path exists but requires malicious whitelisted strategy. | L340-380 | forefy (L-2), trailofbits (G-1), cyfrin |
| L-7 | **Market removal may abandon small share balances.** `expectedSupplyAssets` using `previewRedeem` may round to 0 for small balances, allowing removal while shares remain. | L365-373 | trailofbits (M-5) |

### Informational / gas optimisations

| # | Finding | Tools that found it |
|---|---------|---------------------|
| I-1 | Use `ReentrancyGuardTransient` instead of `ReentrancyGuard` to save ~2,900 gas per guarded call (Solidity 0.8.26 supports transient storage). | cyfrin |
| I-2 | Cache `supplyQueue.length` and `withdrawQueue.length` in local variables to avoid repeated SLOAD. | cyfrin |
| I-3 | Use `calldata` instead of `memory` for string parameters in `setName` and `setSymbol`. | cyfrin |
| I-4 | ERC4626 deviation: `_deposit` uses Permit2 instead of standard `safeTransferFrom`. Documented. | kadenzipfel (Info-3) |
| I-5 | `acceptTimelock`/`acceptGuardian`/`acceptCap` callable by anyone after timelock elapses. Intentional Morpho design pattern. | forefy (M-3 QUESTIONABLE) |
| I-6 | ERC4626 rounding directions all correct (vault-favourable). First-depositor inflation attack mitigated by VIRTUAL_AMOUNT. | quillai (2.3/2.5), kadenzipfel |
| I-7 | Permit2 signature security correctly delegated to audited Permit2 contract. | quillai (6.1/6.2) |

### Deployment-specific findings

| # | Finding | Severity |
|---|---------|----------|
| D-1 | **All privileged roles (owner, curator, guardian, fee recipient) controlled by a single EOA** (`0xAbcA0e...`). The guardian Safe is 1-of-1 with this same EOA as sole signer. | HIGH |
| D-2 | **Timelock is 0 seconds.** Governance changes take effect immediately with no delay for depositors to react. | HIGH |

---

## Findings heatmap by tool

| Finding | pashov | scv-scan | forefy | trailofbits | quillai | auditmos | cyfrin |
|---------|:------:|:--------:|:------:|:-----------:|:-------:|:--------:|:------:|
| H-1 Underflow bricks vault | | | M-1 | H-1 | related | | |
| H-2 Reverting strategy DoS | | | | | 5.2 | H-01 | |
| H-3 Silent deposit failure accounting | | | H-1 | M-2/M-4 | | | |
| M-1 Unsafe uint112 in try body | [80] | L-1 | M-2 | H-2 | 2.1/2.2 | H-02 | yes |
| M-2 Fee-on-transfer tokens | | | | T-4 | 4.1 | | |
| M-3 Rebasing tokens | | | | | 4.2 | | |
| M-4 Read-only reentrancy | [60] | | | | 3.3 | M-03 | |
| M-5 Silent catch blocks | | L-2 | | M-3 | | L-03 | yes |
| M-6 Unverified return values | | | | T-1 | 4.3 | | |
| M-7 lostAssets inflation | | | | | C2 | M-01 | |
| L-1 Supply queue duplicates | [55] | | L-1 | L-5 | | | |
| L-2 Fee not timelocked | | | L-3 | | | | |
| L-3 renounceOwnership | | | | G-2 | | | |

**Agreement statistics:**
- **M-1 (unsafe uint112 cast):** Found by ALL 7 tools -- strongest consensus finding
- **M-5 (silent catch blocks):** Found by 5 tools
- **M-4 (read-only reentrancy):** Found by 3 tools
- **L-1 (supply queue duplicates):** Found by 3 tools
- **H-1 (underflow bricking vault):** Found by 2 tools
- **H-2, H-3, M-2, M-3, M-6, M-7:** Found by 1-2 tools each

---

## Recommended priority fixes

1. **[CRITICAL]** Fix L911 underflow: `if (realTotalAssets + lostAssets < lastTotalAssetsCached)` instead of `if (realTotalAssets < lastTotalAssetsCached - lostAssets)`
2. **[HIGH]** Use SafeCast consistently: replace `uint112(...)` with `.toUint112()` at L415 and L847
3. **[HIGH]** Add try/catch to `_accruedFeeAndAssets` strategy iteration to prevent single-strategy DoS
4. **[MEDIUM]** Emit events in catch blocks for off-chain monitoring
5. **[MEDIUM]** Document unsupported token types (fee-on-transfer, rebasing) in NatSpec
6. **[LOW]** Override `renounceOwnership()` to revert
7. **[LOW]** Add duplicate check in `setSupplyQueue`
8. **[GAS]** Switch to `ReentrancyGuardTransient`, cache array lengths

### Deployment-specific

9. **[CRITICAL]** Use a multisig with >1 signer for all privileged roles
10. **[CRITICAL]** Set a non-zero timelock (minimum 24 hours recommended)
