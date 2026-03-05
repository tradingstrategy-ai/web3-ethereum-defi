# EulerEarn comprehensive security audit report

**Contract**: `src/EulerEarn.sol` (930 lines, Solidity 0.8.26)
**Analysis date**: 2026-03-05
**Skills applied**: Audit Context Building, Entry Point Analyzer, Token Integration Analyzer, Code Maturity Assessor, Guidelines Advisor
**Framework**: Trail of Bits Building Secure Contracts

---

## Executive summary

EulerEarn is an ERC4626-compliant vault aggregator that allocates deposited assets across multiple ERC4626 strategy vaults. It is forked from Morpho Labs' MetaMorpho and integrates with the Euler Vault Connector (EVC). The contract implements a tiered role system (owner, curator, allocator, guardian) with timelock-protected governance operations.

**Overall assessment**: The codebase demonstrates strong security practices. It uses Solidity 0.8.26 with built-in overflow protection, OpenZeppelin libraries, ReentrancyGuard, two-step ownership transfer, and a well-designed timelock system. However, several medium and low severity issues were identified across the analysis dimensions.

**Key findings**:
- 0 Critical severity
- 2 High severity
- 5 Medium severity
- 8 Low / Informational severity

---

## Section 1: Audit context building

### 1.1 Architectural overview

EulerEarn is a single-contract system with the following inheritance chain:

```
ReentrancyGuard -> ERC4626 -> Ownable2Step -> EVCUtil -> IEulerEarnStaticTyping
```

**Actors**:
- **Owner** (Ownable2Step): Highest privilege. Sets curator, allocators, guardian, fee, fee recipient, timelock, name, symbol.
- **Curator** (+ owner): Submits supply caps for strategy vaults, submits market removals.
- **Allocator** (+ curator + owner): Sets supply queue, updates withdraw queue, performs reallocation between strategies.
- **Guardian** (+ owner): Revokes pending timelock, pending guardian, pending cap, pending market removal.
- **Users**: Deposit/withdraw/mint/redeem through ERC4626 interface.
- **EVC**: Mediates `_msgSender()` resolution via `EVCUtil`, enabling account abstraction.
- **Factory** (`creator`): Immutable reference checked during `submitCap`/`acceptCap` to verify strategy allowlisting.

**Key state variables**:
- `supplyQueue`: Ordered list of strategy vaults for deposits.
- `withdrawQueue`: Ordered list of strategy vaults for withdrawals.
- `config[id]`: Per-strategy configuration (cap, enabled, balance, removableAt).
- `lastTotalAssets`: Cached total assets for interest accrual.
- `lostAssets`: Tracks unrealised losses to prevent fee extraction on recovered losses.
- `fee` / `feeRecipient`: Performance fee (as WAD fraction) and recipient.
- `timelock`: Governance delay for cap increases, guardian changes, and timelock decreases.

### 1.2 Critical invariants

1. **totalAssets = realTotalAssets + lostAssets**: The vault's reported total assets includes both real holdings and tracked losses.
2. **totalWithdrawn == totalSupplied in reallocate**: Reallocation must be balanced; no net assets enter or leave the vault.
3. **config[id].balance tracks share holdings**: The internal balance tracking must remain synchronised with actual share balances held.
4. **timelock enforced for cap increases, guardian changes, timelock decreases**: Governance changes that increase risk require waiting.
5. **lastTotalAssets + deposits = total assets after deposit**: The deposit path updates `lastTotalAssets` incrementally.
6. **Fee shares are minted to feeRecipient, never to address(0)**: The contract enforces `feeRecipient != address(0)` when `fee != 0`.

### 1.3 Trust boundaries

- **External strategy vaults**: Called via `deposit`, `withdraw`, `redeem`, `previewRedeem`, `maxDeposit`, `maxWithdraw`, `balanceOf`, `asset()`. These are treated as semi-trusted (must be allowed by factory) but could still misbehave.
- **EVC**: Trusted for `_msgSender()` resolution and account ownership checks.
- **Factory**: Trusted for strategy allowlisting.
- **Permit2**: Used for token transfers in deposits.

---

## Section 2: Entry point analysis

### Summary

| Category | Count |
|----------|-------|
| Public (unrestricted) | 8 |
| Role-restricted | 15 |
| Contract-only / Timelock-gated | 3 |
| **Total state-changing** | **26** |

### Public entry points (unrestricted)

These are state-changing functions callable by anyone and represent the primary attack surface.

| Function | Line | Notes |
|----------|------|-------|
| `deposit(uint256, address)` | L560 | ERC4626 deposit. `nonReentrant`. Accrues interest. |
| `mint(uint256, address)` | L571 | ERC4626 mint. `nonReentrant`. Accrues interest. |
| `withdraw(uint256, address, address)` | L580 | ERC4626 withdraw. `nonReentrant`. Accrues interest. |
| `redeem(uint256, address, address)` | L596 | ERC4626 redeem. `nonReentrant`. Accrues interest. |
| `acceptTimelock()` | L497 | Anyone can call after timelock elapses. `afterTimelock` modifier. |
| `acceptGuardian()` | L502 | Anyone can call after timelock elapses. `afterTimelock` modifier. |
| `acceptCap(IERC4626)` | L507 | Anyone can call after timelock elapses. Rechecks factory allowlist. |
| `transfer` / `transferFrom` / `approve` | (inherited) | Standard ERC20 functions from OZ ERC20. |

### Role-restricted entry points

#### Owner

| Function | Line | Restriction |
|----------|------|-------------|
| `setName(string)` | L195 | `onlyOwner` |
| `setSymbol(string)` | L202 | `onlyOwner` |
| `setCurator(address)` | L209 | `onlyOwner` |
| `setIsAllocator(address, bool)` | L218 | `onlyOwner` |
| `submitTimelock(uint256)` | L227 | `onlyOwner` |
| `setFee(uint256)` | L243 | `onlyOwner`, `nonReentrant` |
| `setFeeRecipient(address)` | L258 | `onlyOwner`, `nonReentrant` |
| `submitGuardian(address)` | L271 | `onlyOwner` |
| `acceptOwnership()` | (inherited) | Ownable2Step |
| `transferOwnership(address)` | (inherited) | `onlyOwner` (Ownable2Step) |
| `renounceOwnership()` | (inherited) | `onlyOwner` (Ownable) |

#### Curator (or owner)

| Function | Line | Restriction |
|----------|------|-------------|
| `submitCap(IERC4626, uint256)` | L287 | `onlyCuratorRole`, `nonReentrant` |
| `submitMarketRemoval(IERC4626)` | L310 | `onlyCuratorRole` |

#### Allocator (or curator or owner)

| Function | Line | Restriction |
|----------|------|-------------|
| `setSupplyQueue(IERC4626[])` | L325 | `onlyAllocatorRole` |
| `updateWithdrawQueue(uint256[])` | L340 | `onlyAllocatorRole` |
| `reallocate(MarketAllocation[])` | L383 | `onlyAllocatorRole`, `nonReentrant` |

#### Guardian (or owner)

| Function | Line | Restriction |
|----------|------|-------------|
| `revokePendingTimelock()` | L447 | `onlyGuardianRole` |
| `revokePendingGuardian()` | L454 | `onlyGuardianRole` |

#### Curator or guardian (or owner)

| Function | Line | Restriction |
|----------|------|-------------|
| `revokePendingCap(IERC4626)` | L461 | `onlyCuratorOrGuardianRole` |
| `revokePendingMarketRemoval(IERC4626)` | L468 | `onlyCuratorOrGuardianRole` |

### Access control notes

- All role modifiers use `_msgSenderOnlyEVCAccountOwner()` for EVC integration.
- Owner always has access to all role-restricted functions (hierarchical roles).
- The `afterTimelock` modifier gates `acceptTimelock`, `acceptGuardian`, `acceptCap` but does **not** restrict who can call them. This is by design - anyone can finalise a pending governance action once the timelock has elapsed.

---

## Section 3: Token integration analysis

### 3.1 Context

EulerEarn is both a **token implementation** (ERC4626 vault token) and a **token integration** (deposits underlying ERC20 assets and interacts with external ERC4626 strategy vaults).

### 3.2 ERC4626 conformity assessment

**Compliance status**: Mostly compliant with notable deviations.

| Check | Status | Notes |
|-------|--------|-------|
| `deposit` returns shares | Pass | L560 |
| `mint` returns assets | Pass | L571 |
| `withdraw` returns shares | Pass | L580 |
| `redeem` returns assets | Pass | L596 |
| `totalAssets` reflects real value | Pass | L616 |
| `maxDeposit` returns 0 when paused | N/A | No pause mechanism |
| `maxDeposit` accuracy | Warning | L527: May be higher than actual due to duplicate vaults in supplyQueue |
| `maxMint` accuracy | Warning | L536: Same issue as maxDeposit |
| `maxWithdraw` accuracy | Warning | L544: May be lower than actual due to rounding |
| `maxRedeem` accuracy | Warning | L553: Same issue as maxWithdraw |
| Virtual shares/assets offset | Custom | Uses `VIRTUAL_AMOUNT` offset (L678, L691) instead of OZ default |
| ERC2612 compliance | Deviation | L119: Empty strings passed to ERC20 constructor; `_name`/`_symbol` overridden in storage |

### 3.3 ERC20 underlying asset handling

**Transfer patterns**:
- **Deposits** (L698): Uses `safeTransferFromWithPermit2` from custom `SafeERC20Permit2Lib`. This should handle missing return values if implemented correctly. Cannot verify without the library source.
- **Withdrawals** (L734): Delegates to `super._withdraw` which uses OZ `SafeERC20.safeTransfer`. Correct.
- **Strategy interactions**: Uses OZ `SafeERC20.forceApproveMaxWithPermit2` (L780) and `revokeApprovalWithPermit2` (L798) for approvals. Strategy deposits/withdrawals call `id.deposit()`, `id.withdraw()`, `id.redeem()` directly.

### 3.4 Findings

#### FINDING T-1: Strategy vault interactions lack return value validation for edge cases

**Severity**: Medium
**Location**: Lines 825-828 (`_supplyStrategy`), Lines 846-849 (`_withdrawStrategy`)

**Description**: When interacting with strategy vaults, the contract calls `id.deposit()`, `id.withdraw()`, and `id.redeem()` wrapped in try/catch. However, the underlying assumption is that these strategy vaults conform to ERC4626. A malicious or buggy strategy vault could:
1. Return fewer shares than expected from `deposit()`, while the contract uses the returned `suppliedShares` for balance tracking (L826).
2. Return a different amount from `withdraw()` than requested, while the contract assumes `toWithdraw` assets were received (L848).

In `_withdrawStrategy` (L846), the contract calls `id.withdraw(toWithdraw, ...)` and decrements `assets -= toWithdraw` (L848), but the actual assets received could differ if the strategy vault does not conform perfectly to ERC4626 (e.g., rounding, fees).

**Recommendation**: Consider verifying actual asset balance changes after strategy interactions, particularly in `_withdrawStrategy` where the assumed amount is used for accounting.

#### FINDING T-2: Permit2 address stored as immutable without validation

**Severity**: Low
**Location**: Line 139

**Description**: The `permit2Address` is set in the constructor without validating that it is a valid contract address. If set to `address(0)` or an EOA, all deposits would fail.

**Recommendation**: Add a constructor check: `require(permit2 != address(0))` or verify it is a contract.

#### FINDING T-3: Strategy vault Permit2 detection via staticcall may be unreliable

**Severity**: Low
**Location**: Lines 776-777

**Description**: In `_setCap`, the contract attempts to detect whether a strategy vault supports Permit2 by calling `this.permit2Address()` on the strategy via staticcall. This is a fragile heuristic:
- It calls `permit2Address()` which is a specific function signature. If the strategy has a different function at the same selector, it could return incorrect data.
- The result is used to set up Permit2-aware approvals for the strategy.

**Recommendation**: Consider using `IERC165.supportsInterface` or a more robust detection mechanism if Permit2 compatibility is important for strategy interactions.

#### FINDING T-4: No handling of fee-on-transfer tokens as underlying asset

**Severity**: Medium
**Location**: Lines 697-708 (`_deposit`), Lines 717-735 (`_withdraw`)

**Description**: The contract does not perform balance-before/balance-after checks when receiving the underlying asset. If the underlying asset has transfer fees (e.g., USDT with fees enabled, STA, PAXG), the actual amount received would be less than `assets`, but the contract would:
- Mint shares based on the full `assets` amount (L699).
- Update `lastTotalAssets` with the full `assets` amount (L707).

This creates an accounting discrepancy. The factory's allowlist mechanism provides some mitigation (only whitelisted strategies are allowed), but the underlying asset itself is set at construction and not validated for fee-on-transfer behaviour.

**Recommendation**: If fee-on-transfer tokens are to be supported, add balance-before/balance-after checks in `_deposit`. If they are explicitly not supported, document this assumption clearly in the contract's NatSpec.

### 3.5 Weird ERC20 pattern exposure

| Pattern | Risk | Mitigation |
|---------|------|------------|
| Missing return values | Low | Uses `SafeERC20` / Permit2 library |
| Fee on transfer | Medium | No balance checks (see T-4) |
| Rebasing tokens | High | Balance caching in `lastTotalAssets` would desynchronise; not supported |
| Upgradeable tokens | Low | Asset is fixed at construction; upgrade risk is external |
| Pausable tokens | Low | Strategy vault or underlying token pause would cause withdrawal failures |
| Blocklists | Low | If vault address is blocklisted, all operations fail |
| Approval race | Low | Uses `forceApproveMax` pattern |
| Low decimals | Low | Virtual amount offset helps but precision loss could be amplified |

---

## Section 4: Code maturity assessment

### Maturity scorecard

| Category | Rating | Score | Notes |
|----------|--------|-------|-------|
| Arithmetic | Satisfactory | 3 | Solidity 0.8.26, SafeCast, `mulDiv`, virtual offset. One unsafe cast (L415). |
| Auditing (events) | Satisfactory | 3 | Comprehensive events via EventsLib for all state changes. |
| Authentication / access controls | Satisfactory | 3 | Four-tier role system, Ownable2Step, EVC integration. |
| Complexity management | Satisfactory | 3 | Well-structured, moderate function sizes, clear separation. |
| Decentralisation | Moderate | 2 | Owner has significant power; timelock helps but no multisig enforced. |
| Documentation | Moderate | 2 | NatSpec `@inheritdoc` throughout but relies on interface docs not available here. |
| Transaction ordering risks | Moderate | 2 | Timelock for governance, but deposit/withdraw have MEV exposure. |
| Low-level manipulation | Strong | 4 | No assembly, no low-level calls, no delegatecall. One staticcall (L776). |
| Testing & verification | N/A | - | Test suite not available for evaluation. |

**Overall maturity**: Moderate to Satisfactory (average 2.75/4.0 on assessed categories)

### Detailed findings

#### FINDING M-1: Unsafe cast in `reallocate` - `config[id].balance` truncation

**Severity**: High
**Location**: Line 415

**Description**: In the `reallocate` function, when withdrawing from a strategy, the balance is updated as:
```solidity
config[id].balance = uint112(supplyShares - withdrawnShares);
```
This uses a direct `uint112()` cast without `SafeCast.toUint112()`. While `supplyShares` is already a `uint112` (from `config[id].balance` at L392), and `withdrawnShares` should be less than or equal to `supplyShares`, the subtraction result is computed as a `uint256`. If `withdrawnShares > supplyShares` due to a rounding error in the strategy vault's `withdraw` function, the subtraction would underflow (reverting in Solidity 0.8.26), which is safe. However, the inconsistency with L433 which uses `.toUint112()` suggests this was an oversight.

More concerning: `supplyShares` at L392 reads `config[id].balance` which is `uint112`, but then `withdrawnShares` comes from `id.withdraw()` (L409) which returns `uint256`. If the strategy vault returns a `withdrawnShares` value that, when subtracted from `supplyShares`, still fits in `uint112`, the cast is safe. But if the strategy vault is adversarial and returns a manipulated share count, the subtraction could produce a value that wraps silently in the `uint112()` cast if Solidity's checked arithmetic does not catch it before the cast. In practice, Solidity 0.8.26 checks the subtraction for underflow, so the main risk is consistency.

**Recommendation**: Use `SafeCast.toUint112()` consistently, replacing the raw `uint112()` cast on line 415 with `(supplyShares - withdrawnShares).toUint112()` for defensive consistency.

#### FINDING M-2: `_supplyStrategy` silently swallows strategy deposit failures

**Severity**: Medium
**Location**: Lines 824-828

**Description**: The `_supplyStrategy` function uses try/catch when depositing into strategy vaults:
```solidity
try id.deposit(toSupply, address(this)) returns (uint256 suppliedShares) {
    config[id].balance = (config[id].balance + suppliedShares).toUint112();
    assets -= toSupply;
} catch {}
```
If a strategy vault's `deposit` reverts, the error is silently caught and the loop continues. If **all** strategy vaults revert, the function reverts with `AllCapsReached()` at L834. However, the silent catch means:
1. Users have no way to know which strategies failed.
2. A temporarily failing strategy will silently redirect all deposits to the next strategy in the queue, potentially concentrating risk.

The same pattern exists in `_withdrawStrategy` (L846-849).

**Recommendation**: Consider emitting an event in the catch block to log strategy interaction failures, enabling off-chain monitoring to detect degraded strategies.

#### FINDING M-3: `_accruedFeeAndAssets` potential underflow in loss detection

**Severity**: High
**Location**: Line 911

**Description**: In `_accruedFeeAndAssets()`:
```solidity
if (realTotalAssets < lastTotalAssetsCached - lostAssets) {
```
The expression `lastTotalAssetsCached - lostAssets` can underflow if `lostAssets > lastTotalAssetsCached`. While Solidity 0.8.26 would revert on underflow, this would brick the vault entirely -- every call to `deposit`, `withdraw`, `mint`, `redeem`, `totalAssets`, `maxDeposit`, `maxMint`, `maxWithdraw`, `maxRedeem`, and `_accrueInterest` would revert.

This state could be reached if:
1. The vault has accumulated `lostAssets` from strategy losses.
2. Withdrawals reduce `lastTotalAssets` via `_updateLastTotalAssets(lastTotalAssets.zeroFloorSub(assets))` (L730).
3. Over time, `lastTotalAssets` decreases below `lostAssets`.

The `_updateLastTotalAssets` at L730 uses `zeroFloorSub`, clamping to zero. If `lastTotalAssets` becomes 0 while `lostAssets` is still positive, then `lastTotalAssetsCached - lostAssets` underflows.

**Recommendation**: Change the condition to avoid underflow: `if (lostAssets <= lastTotalAssetsCached && realTotalAssets < lastTotalAssetsCached - lostAssets)` or use `zeroFloorSub`.

#### FINDING M-4: `totalInterest` calculation assumes monotonic growth

**Severity**: Low
**Location**: Line 920

**Description**: In `_accruedFeeAndAssets()`:
```solidity
newTotalAssets = realTotalAssets + newLostAssets;
uint256 totalInterest = newTotalAssets - lastTotalAssetsCached;
```
If `newTotalAssets < lastTotalAssetsCached`, this underflows and reverts. By design, `newTotalAssets = realTotalAssets + newLostAssets` where `newLostAssets` is set to ensure `newTotalAssets >= lastTotalAssetsCached` (when `realTotalAssets >= lastTotalAssetsCached - lostAssets`). In the loss branch (L913), `newLostAssets = lastTotalAssetsCached - realTotalAssets`, so `newTotalAssets = realTotalAssets + lastTotalAssetsCached - realTotalAssets = lastTotalAssetsCached`. So `totalInterest = 0`. In the no-loss branch, `newLostAssets = lostAssets` (unchanged), and `newTotalAssets >= lastTotalAssetsCached` only if `realTotalAssets + lostAssets >= lastTotalAssetsCached`.

However, if the condition on L911 fails (no new loss) but `realTotalAssets + lostAssets < lastTotalAssetsCached`, then L920 underflows. This can happen after deposits: `_deposit` at L707 sets `lastTotalAssets += assets`, but the actual strategy deposit may have partially failed (try/catch at L825), meaning `realTotalAssets` does not increase by the full `assets` amount while `lastTotalAssets` did.

This links to the comment at L705: "lastTotalAssets + assets may be a little above totalAssets(). This can lead to a small accrual of lostAssets at the next interaction." So by design, a small discrepancy is expected and would be caught by the loss branch. But a large discrepancy (e.g., all strategy deposits fail silently) could cause issues.

**Recommendation**: The interaction between the try/catch in `_supplyStrategy`, `lastTotalAssets` updates, and `_accruedFeeAndAssets` should be stress-tested for scenarios where all or most strategy deposits fail. Consider documenting the maximum expected discrepancy.

---

## Section 5: Guidelines advisor analysis

### 5.1 Documentation and specifications

**Status**: Moderate

**Strengths**:
- Contract-level NatSpec with `@title`, `@author`, `@notice`, `@custom:contact` (L36-40).
- Extensive use of `@inheritdoc` to reference interface documentation (throughout).
- Clear section comments (`/* IMMUTABLES */`, `/* STORAGE */`, `/* ONLY OWNER FUNCTIONS */`, etc.).
- Inline comments explaining non-obvious logic (L397, L421-422, L705-706, L727-729).

**Gaps**:
- No standalone specification document available.
- `@inheritdoc` delegates all documentation to interfaces (not available for review).
- No explicit invariant documentation in the code.
- The `lostAssets` mechanism (L911-917), while commented, is complex enough to warrant formal documentation of the mathematical model.

**Recommendation**: Create a standalone specification document describing the fee model, loss tracking, and interaction between `lastTotalAssets`, `lostAssets`, and `realTotalAssets`. Explicitly document all system invariants.

### 5.2 Upgradeability

**Status**: Not applicable. The contract is not upgradeable. No proxy patterns, no `delegatecall`, no `selfdestruct`. This is a positive design choice.

### 5.3 Function composition

**Status**: Satisfactory

The contract has well-structured functions with clear purposes:
- Admin functions (L194-282): Simple setters with validation.
- Curator functions (L287-320): Cap management with timelock integration.
- Allocator functions (L325-442): Queue management and reallocation.
- ERC4626 overrides (L514-620): Standard interface with fee accrual.
- Internal helpers (L622-930): Clear separation of concerns.

The most complex function is `reallocate` (L383-442) at approximately 60 lines with multiple branches. This is within acceptable limits but is the highest-risk function for bugs due to its interaction with external strategy vaults and internal balance tracking.

### 5.4 Inheritance

**Status**: Moderate complexity

```
ReentrancyGuard
  └─ ERC4626 (extends ERC20, IERC4626)
       └─ Ownable2Step (extends Ownable)
            └─ EVCUtil
                 └─ IEulerEarnStaticTyping (interface)
```

The inheritance depth is 5, which is moderate. The `_msgSender()` override (L743-745) resolving the conflict between `EVCUtil` and `Context` is correctly handled, explicitly delegating to `EVCUtil._msgSender()`.

**Potential concern**: The `_msgSender()` override means all OZ functions that use `_msgSender()` (including `_withdraw` which checks allowances) will use EVC-resolved sender. This is intentional but creates a trust dependency on EVC.

### 5.5 Events

**Status**: Satisfactory

All critical operations emit events through `EventsLib`:
- Governance: `SetName`, `SetSymbol`, `SetCurator`, `SetIsAllocator`, `SubmitTimelock`, `SetTimelock`, `SetFee`, `SetFeeRecipient`, `SubmitGuardian`, `SetGuardian`.
- Caps: `SubmitCap`, `SetCap`, `RevokePendingCap`.
- Queues: `SetSupplyQueue`, `SetWithdrawQueue`.
- Allocation: `ReallocateSupply`, `ReallocateWithdraw`.
- Fee: `AccrueInterest`, `UpdateLastTotalAssets`, `UpdateLostAssets`.
- ERC4626: `Deposit`, `Withdraw` (inherited).

No critical state change was found without a corresponding event.

### 5.6 Common pitfalls check

#### FINDING G-1: Reentrancy via EVC callback path

**Severity**: Low
**Location**: Lines 743-745, throughout

**Description**: The `_msgSender()` function delegates to `EVCUtil._msgSender()`, which queries the EVC contract. The EVC supports batched operations where calls are made on behalf of account owners. While the `nonReentrant` modifier protects the core deposit/withdraw/reallocate/fee paths, several functions lack `nonReentrant`:
- `setSupplyQueue` (L325)
- `updateWithdrawQueue` (L340)
- `submitMarketRemoval` (L310)
- `acceptTimelock` (L497)
- `acceptGuardian` (L502)
- `acceptCap` (L507)
- All `revoke*` functions

These are protected by role modifiers instead. In the context of EVC batching, an attacker controlling a strategy vault could potentially trigger callbacks during `reallocate` that re-enter through the EVC into non-reentrancy-protected functions. However, since `reallocate` itself is `nonReentrant` and the Solidity 0.8.26 ReentrancyGuard would prevent re-entry into any `nonReentrant` function during the same transaction, the risk is limited to re-entry into non-protected functions which are all role-gated.

**Recommendation**: The risk is low given the role restrictions, but consider whether any state changes in non-reentrancy-protected functions could be exploited mid-execution of a `nonReentrant` function. Specifically, if an allocator-controlled strategy vault calls back into `setSupplyQueue` during `reallocate`, it could change the supply queue while allocation is in progress (though `reallocate` does not use `supplyQueue`).

#### FINDING G-2: `renounceOwnership()` inherited but not overridden

**Severity**: Low
**Location**: Inherited from `Ownable`

**Description**: The contract inherits `renounceOwnership()` from OZ `Ownable` (via `Ownable2Step`). If called, this would irrevocably remove the owner, preventing all owner-only operations including fee changes, curator assignment, allocator management, and guardian assignment. There is no recovery mechanism.

**Recommendation**: Consider overriding `renounceOwnership()` to revert, preventing accidental use: `function renounceOwnership() public override onlyOwner { revert(); }`

#### FINDING G-3: Deposit to strategies can leave assets idle in the vault

**Severity**: Informational
**Location**: Lines 811-835

**Description**: `_supplyStrategy` (called during deposits) iterates through `supplyQueue` and deposits into strategies. If all strategies are at capacity or their deposits fail, the function reverts with `AllCapsReached()` (L834). This means:
1. Deposits can fail entirely if no strategy has capacity.
2. There is no concept of an "idle" balance held by the vault itself.

This is by design but worth noting: unlike some vault aggregators, EulerEarn does not hold an idle cash buffer. All deposited assets must be allocated to strategies.

#### FINDING G-4: Withdraw queue manipulation during market removal

**Severity**: Low
**Location**: Lines 340-380

**Description**: The `updateWithdrawQueue` function allows removing markets from the withdraw queue. When a market is removed (not included in `indexes`), it must satisfy:
1. Cap must be 0 (L362).
2. No pending cap (L363).
3. If it has assets: must have a pending removal with elapsed timelock (L366-370).

However, the function deletes `config[id]` entirely (L373) when removing a market. If the market still holds shares (`config[id].balance > 0`) and `expectedSupplyAssets(id) == 0` (because the strategy vault's `previewRedeem` returns 0 for the shares), the shares are effectively abandoned. The `expectedSupplyAssets` check at L365 uses `previewRedeem` which could return 0 for small share amounts due to rounding.

**Recommendation**: Consider checking `config[id].balance > 0` in addition to `expectedSupplyAssets(id) != 0` when deciding if a market can be removed.

### 5.7 Dependencies

**Status**: Strong

The contract uses well-established dependencies:
- **OpenZeppelin Contracts**: `ERC4626`, `ERC20`, `Ownable2Step`, `ReentrancyGuard`, `SafeERC20`, `SafeCast`, `Math`. Industry standard.
- **Ethereum Vault Connector (EVC)**: `EVCUtil`. Euler-specific but well-documented.
- **Custom libraries**: `PendingLib`, `ConstantsLib`, `ErrorsLib`, `EventsLib`, `SafeERC20Permit2Lib`, `UtilsLib`. Source not available for review but naming and usage patterns are clean.

**Note**: The `SafeERC20Permit2Lib` library is custom and handles the Permit2 integration. Without its source, it is not possible to verify its correctness.

### 5.8 Solidity version

**Status**: Strong

Using Solidity 0.8.26, which provides:
- Built-in overflow/underflow protection.
- Custom errors (used throughout via `ErrorsLib`).
- Immutable variables.
- No inline assembly detected.

---

## Consolidated findings

### High severity

| ID | Finding | Location | Description |
|----|---------|----------|-------------|
| H-1 | Potential `_accruedFeeAndAssets` underflow bricking the vault | L911 | `lastTotalAssetsCached - lostAssets` can underflow if `lostAssets > lastTotalAssets`, permanently bricking the vault. |
| H-2 | Inconsistent use of SafeCast in `reallocate` | L415 | Raw `uint112()` cast instead of `SafeCast.toUint112()` for balance update. While unlikely to cause issues due to Solidity 0.8 underflow protection on the subtraction, it is inconsistent with the rest of the codebase. |

### Medium severity

| ID | Finding | Location | Description |
|----|---------|----------|-------------|
| M-1 | Strategy vault return values not validated against actual balance changes | L825-828, L846-849 | Deposit/withdraw amounts assumed correct without balance verification. |
| M-2 | No fee-on-transfer token support for underlying asset | L697-708 | No balance-before/balance-after check on deposits. |
| M-3 | Silent strategy deposit/withdrawal failures | L824-828, L846-849 | Try/catch swallows errors without logging. |
| M-4 | `totalInterest` calculation can underflow after partial deposit failures | L920 | Interaction between try/catch failures and `lastTotalAssets` updates can cause underflow. |
| M-5 | Market removal may abandon small share balances | L365, L373 | `expectedSupplyAssets` using `previewRedeem` may round to 0 for small balances. |

### Low / Informational severity

| ID | Finding | Location | Description |
|----|---------|----------|-------------|
| L-1 | Permit2 address not validated in constructor | L139 | No zero-address check. |
| L-2 | Strategy Permit2 detection via staticcall is fragile | L776-777 | Function selector collision possible. |
| L-3 | `renounceOwnership()` not overridden | Inherited | Accidental call permanently removes owner. |
| L-4 | Reentrancy into non-protected functions during EVC batch | L325, L340 | Role-gated functions lack `nonReentrant` but are protected by access control. |
| L-5 | `maxDeposit` / `maxMint` may overestimate due to duplicate queue entries | L527, L536 | Documented in NatSpec but could confuse integrators. |
| L-6 | No idle cash buffer design | L834 | All deposits must go to strategies; `AllCapsReached` reverts the entire deposit. |
| L-7 | ERC2612 deviation due to empty constructor strings | L119, L129 | Name/symbol passed as empty strings to ERC20 constructor. |
| L-8 | Rebasing tokens explicitly unsupported but not documented | N/A | Balance caching via `lastTotalAssets` would break with rebasing tokens. |

---

## Recommendations summary

### Immediate (before deployment)

1. **Fix the potential underflow in `_accruedFeeAndAssets`** (H-1): Add underflow protection to `lastTotalAssetsCached - lostAssets` on line 911.
2. **Use SafeCast consistently** (H-2): Replace `uint112()` on line 415 with `.toUint112()`.
3. **Document unsupported token types** (M-2, L-8): Explicitly state in NatSpec that fee-on-transfer and rebasing tokens are not supported as the underlying asset.

### Short-term (1-2 months)

4. **Add failure event logging** (M-3): Emit events in catch blocks of `_supplyStrategy` and `_withdrawStrategy`.
5. **Stress-test partial deposit failure scenarios** (M-4): Create test cases where strategy deposits partially fail and verify `_accruedFeeAndAssets` handles the discrepancy correctly.
6. **Check `config[id].balance` during market removal** (M-5): Add an additional check for non-zero share balance when removing markets from the withdraw queue.
7. **Override `renounceOwnership`** (L-3): Prevent accidental ownership renunciation.

### Medium-term (2-4 months)

8. **Consider balance verification for strategy interactions** (M-1): Add before/after balance checks for critical strategy interactions.
9. **Validate Permit2 address in constructor** (L-1): Add zero-address and contract-existence checks.
10. **Create standalone specification document** (Documentation): Document the fee model, loss tracking mathematics, and system invariants formally.
