# EulerEarn combined audit report

**Contract:** `src/EulerEarn.sol` (930 lines, Solidity 0.8.26)
**Description:** ERC4626-compliant yield aggregator vault forked from Morpho with EVC integration. Accepts user deposits and allocates them across multiple ERC4626 strategy vaults.
**Skills applied:** audit-math-precision, audit-state-validation, audit-reentrancy, audit-oracle, audit-lending

---

## Summary

| Severity | Count |
|----------|-------|
| High     | 2     |
| Medium   | 5     |
| Low      | 4     |

---

## Findings

### [H-01] `try/catch` in `_withdrawStrategy` silently swallows failures, potentially trapping user funds

**Skill:** audit-reentrancy, audit-state-validation
**Severity:** High
**Location:** `EulerEarn.sol` lines 838-856, `_withdrawStrategy()`

**Description:**

When a user calls `withdraw()` or `redeem()`, the function `_withdrawStrategy()` iterates through the withdraw queue and attempts to withdraw from each strategy vault. The external `id.withdraw()` call is wrapped in a `try/catch` block (line 846) that silently catches all errors and continues to the next vault. If a strategy vault reverts for any reason (reentrancy guard, paused state, temporary liquidity issue, malicious revert), the withdrawal from that vault is silently skipped. The function only reverts with `NotEnoughLiquidity` at line 855 if `assets != 0` after iterating all vaults.

The critical issue is that `lastTotalAssets` is already updated (decremented) at line 730 *before* `_withdrawStrategy` is called. If the underlying `id.withdraw()` returns fewer actual tokens than expected (e.g., due to rounding or a vault that reverts), the accounting becomes inconsistent: `lastTotalAssets` has been decremented by the full `assets` amount, but the vault may not have received all those tokens.

Furthermore, a malicious strategy vault could selectively revert withdrawals, causing a denial-of-service for users attempting to withdraw. If enough strategies revert, users cannot withdraw their funds at all despite their shares representing real value.

**Vulnerable code:**

```solidity
// Line 730: lastTotalAssets updated BEFORE withdrawal
_updateLastTotalAssets(lastTotalAssets.zeroFloorSub(assets));

// Line 838-856: _withdrawStrategy
function _withdrawStrategy(uint256 assets) internal {
    for (uint256 i; i < withdrawQueue.length; ++i) {
        IERC4626 id = withdrawQueue[i];
        uint256 toWithdraw = UtilsLib.min(maxWithdrawFromStrategy(id), assets);
        if (toWithdraw > 0) {
            // Using try/catch to skip vaults that revert.
            try id.withdraw(toWithdraw, address(this), address(this)) returns (uint256 withdrawnShares) {
                config[id].balance = uint112(config[id].balance - withdrawnShares);
                assets -= toWithdraw;
            } catch {}
        }
        if (assets == 0) return;
    }
    if (assets != 0) revert ErrorsLib.NotEnoughLiquidity();
}
```

**Impact:**

A single malicious or malfunctioning strategy vault in the withdraw queue can block all user withdrawals if no other vaults have sufficient liquidity. Combined with the `_supplyStrategy` try/catch pattern (line 824-828), a malicious strategy could also prevent deposits from being distributed properly. While the `NotEnoughLiquidity` revert at line 855 prevents users from losing funds outright (the transaction reverts entirely if insufficient assets are recovered), the denial-of-service vector is real.

**Proof of concept:**

1. Allocator sets up withdraw queue: [VaultA, MaliciousVault, VaultB]
2. User has 100 tokens worth of shares. VaultA has 30 liquidity, MaliciousVault has 50 (but always reverts on withdraw), VaultB has 20.
3. User calls `withdraw(100)`. VaultA provides 30, MaliciousVault reverts silently, VaultB provides 20.
4. Only 50 recovered, `assets = 50 != 0`, so the entire transaction reverts with `NotEnoughLiquidity`.
5. User cannot withdraw despite 100 tokens of real value existing across strategies.

**Recommendation:**

This is a design trade-off acknowledged by the protocol (the try/catch is intentional to handle temporarily unavailable vaults). However, the allocator/guardian should have an emergency mechanism to force-remove a malicious strategy from the withdraw queue even when it holds a balance (currently `updateWithdrawQueue` requires either zero balance or a passed timelock via `removableAt`). Consider adding a guardian-callable emergency withdrawal path that bypasses the try/catch graceful handling.

---

### [H-02] Unsafe `uint112` downcast in `_withdrawStrategy` can silently truncate share balance

**Skill:** audit-math-precision
**Severity:** High
**Location:** `EulerEarn.sol` line 847

**Description:**

In `_withdrawStrategy()`, the share balance update uses a raw `uint112()` cast:

```solidity
config[id].balance = uint112(config[id].balance - withdrawnShares);
```

Unlike other locations in the contract which use `SafeCast.toUint112()` (e.g., line 433 in `reallocate`: `(supplyShares + suppliedShares).toUint112()`), this line uses an unchecked downcast. If `config[id].balance - withdrawnShares` produces a value that overflows a `uint112` (which can happen if `withdrawnShares > config[id].balance` due to rounding or share price changes), the cast will silently truncate, corrupting the balance tracking.

In contrast, line 415 in `reallocate` also uses a raw `uint112()` cast:
```solidity
config[id].balance = uint112(supplyShares - withdrawnShares);
```

Both of these raw casts are inconsistent with the safe casting used elsewhere and could lead to corrupted balance tracking if the underlying strategy vault's share accounting diverges from expectations.

**Impact:**

If a strategy vault's share accounting causes `withdrawnShares` to exceed `config[id].balance` (possible due to share price rounding, donations to the strategy vault, or strategy vault bugs), the subtraction underflows. In Solidity 0.8.26, the subtraction itself would revert due to underflow (which is actually protective). However, the `uint112()` cast on the result is still unsafe -- if the result of the subtraction is valid but exceeds `type(uint112).max`, it would silently truncate, permanently corrupting the balance.

**Proof of concept:**

While the underflow protection of Solidity 0.8+ makes the most dangerous scenario (withdrawnShares > balance) revert safely, the inconsistency with `SafeCast` usage elsewhere indicates this is an oversight. If future changes modify the surrounding code (e.g., using `unchecked` blocks for gas optimisation), this raw cast becomes immediately dangerous.

**Recommendation:**

Replace raw `uint112()` casts with `SafeCast.toUint112()` consistently:

```solidity
// Line 847
config[id].balance = (config[id].balance - withdrawnShares).toUint112();

// Line 415
config[id].balance = (supplyShares - withdrawnShares).toUint112();
```

---

### [M-01] `lastTotalAssets` drift creates cumulative `lostAssets` inflation

**Skill:** audit-math-precision, audit-state-validation
**Severity:** Medium
**Location:** `EulerEarn.sol` lines 705-708, 727-730, 898-928

**Description:**

The contract acknowledges in comments (lines 705-706 and 727-728) that `lastTotalAssets + assets` (after deposit) or `lastTotalAssets - assets` (after withdraw) "may be a little above `totalAssets()`" and that "this can lead to a small accrual of `lostAssets` at the next interaction."

The issue is that `lostAssets` only increases and never decreases (except when `realTotalAssets` grows to exceed `lastTotalAssets - lostAssets`). Every deposit and withdrawal creates a small positive delta between `lastTotalAssets` and `realTotalAssets` because:

1. In `_deposit()` (line 707): `lastTotalAssets` is set to `lastTotalAssets + assets`, but the actual supply to strategy may deposit slightly fewer assets due to rounding, or may not deposit at all if all caps are reached (the excess stays as idle balance in the vault, which is NOT counted in `realTotalAssets` since that only sums `expectedSupplyAssets()` for withdraw queue entries).

2. In `_withdraw()` (line 730): `lastTotalAssets` is decremented by `assets`, but the actual withdraw from strategy may have rounding differences.

The `_accruedFeeAndAssets()` function (line 911) checks:
```solidity
if (realTotalAssets < lastTotalAssetsCached - lostAssets) {
    newLostAssets = lastTotalAssetsCached - realTotalAssets;
}
```

This means `lostAssets` absorbs the drift, inflating `newTotalAssets = realTotalAssets + newLostAssets` above `realTotalAssets`. Since `newTotalAssets` is used for share price calculations, this creates a persistent (though small) positive bias in the share price that is never corrected.

Over many interactions with many small rounding differences, this can accumulate.

**Impact:**

The share price becomes slightly inflated over time. New depositors pay a marginally higher price per share, while existing holders benefit from the artificial inflation. The magnitude per interaction is typically 1 wei per strategy deposit/withdraw, but over thousands of interactions this accumulates. Performance fees are also charged on the inflated `totalInterest` (line 920), extracting a small additional fee on phantom gains.

**Recommendation:**

This is a known design trade-off documented in the code comments. The impact is minimal for typical usage patterns. To mitigate accumulation in high-frequency vaults, consider implementing a periodic `lostAssets` reconciliation mechanism callable by the allocator that resets `lostAssets` when `realTotalAssets >= lastTotalAssets`.

---

### [M-02] Idle vault balance not tracked in `realTotalAssets`, causing accounting discrepancy

**Skill:** audit-state-validation, audit-lending
**Severity:** Medium
**Location:** `EulerEarn.sol` lines 903-908, 616-620

**Description:**

The `_accruedFeeAndAssets()` function computes `realTotalAssets` by summing `expectedSupplyAssets()` for each vault in the withdraw queue:

```solidity
uint256 realTotalAssets;
for (uint256 i; i < withdrawQueue.length; ++i) {
    IERC4626 id = withdrawQueue[i];
    realTotalAssets += expectedSupplyAssets(id);
}
```

This calculation does NOT include any idle balance of the underlying asset held directly by the EulerEarn contract (i.e., `IERC20(asset()).balanceOf(address(this))`). However, after `_deposit()` calls `_supplyStrategy()`, if `_supplyStrategy` reverts for all vaults (all caps reached) or if the try/catch silently skips all vaults, the deposited assets remain idle in the contract. The `lastTotalAssets` is then set to `lastTotalAssets + assets` (line 707), but the idle balance is not reflected in `realTotalAssets`.

On the next `_accrueInterest()` call, the condition `realTotalAssets < lastTotalAssetsCached - lostAssets` triggers, and `lostAssets` increases to cover the discrepancy. This means deposits that fail to reach strategies are treated as "lost" from the protocol's accounting perspective, even though the tokens are sitting safely in the contract.

**Impact:**

Users who deposit when all strategy caps are reached will see their deposits effectively treated as lost assets in the accounting. While the tokens are recoverable (they can be allocated by the allocator via `reallocate()`), the intermediate accounting state incorrectly reports these assets as losses. Performance fees may also be slightly affected since `totalInterest` computation depends on `newTotalAssets`.

A `_supplyStrategy` that fails to deposit due to the `AllCapsReached` revert (line 834) would cause the entire deposit to revert. However, if the try/catch swallows some deposits but not all, partial idle balances can accumulate.

**Recommendation:**

Include the vault's own asset balance in `realTotalAssets`:
```solidity
uint256 realTotalAssets = IERC20(asset()).balanceOf(address(this));
for (uint256 i; i < withdrawQueue.length; ++i) {
    // ...
}
```

Alternatively, document this as an explicit design decision and ensure allocators promptly reallocate idle funds.

---

### [M-03] Read-only reentrancy risk: `totalAssets()` and share price can return stale values during strategy vault callbacks

**Skill:** audit-reentrancy
**Severity:** Medium
**Location:** `EulerEarn.sol` lines 616-620, 655-667, 825-828, 846-849

**Description:**

The `totalAssets()`, `_convertToShares()`, and `_convertToAssets()` view functions read `lastTotalAssets` and iterate withdraw queue vaults to compute share prices. During a deposit or withdrawal, the contract makes external calls to strategy vaults (`id.deposit()` at line 825, `id.withdraw()` at line 846). These external calls occur while `lastTotalAssets` has already been updated (line 707 for deposits, line 730 for withdrawals), but `config[id].balance` updates happen inside the try/catch *after* the external call returns.

If a strategy vault has a callback mechanism (e.g., ERC777-like hooks, EVC operator calls, or if the strategy vault itself makes calls back to EulerEarn during its deposit/withdraw), the re-entered view functions would read:
- An already-updated `lastTotalAssets` (reflecting the new deposit/withdrawal)
- Partially updated `config[id].balance` values (some vaults updated, current vault not yet updated)

This creates a window where `totalAssets()` returns an inconsistent value.

The `nonReentrant` modifier on `deposit()`, `withdraw()`, `redeem()`, and `mint()` prevents re-entering these state-changing functions. However, the view functions (`totalAssets()`, `convertToShares()`, `convertToAssets()`, `maxDeposit()`, `maxMint()`, `maxWithdraw()`, `maxRedeem()`) are NOT protected by `nonReentrant` and can be called during this window.

**Impact:**

An external protocol integrating with EulerEarn that reads `totalAssets()` or share conversion functions during a strategy vault's callback could receive an inconsistent value. This is the classic "read-only reentrancy" pattern. The impact depends on how external protocols use these values. For protocols using EulerEarn share prices for collateral valuation, this could enable price manipulation.

The risk is mitigated by:
1. The `nonReentrant` guard prevents re-entering state-changing functions.
2. Strategy vaults are whitelisted by the factory (`isStrategyAllowed`), reducing the risk of malicious callbacks.
3. EVC's own reentrancy protections may provide additional safety.

**Recommendation:**

Document the read-only reentrancy limitation for integrators. Consider adding a view-function reentrancy check (a `locked` flag readable by view functions) so integrators can detect inconsistent state. Alternatively, expose a `getPriceSafe()` function that reverts when the reentrancy lock is held.

---

### [M-04] Fee accrual rounding: `feeAssets` can round to zero, allowing fee-free yield harvesting

**Skill:** audit-math-precision
**Severity:** Medium
**Location:** `EulerEarn.sol` lines 921-928

**Description:**

The fee calculation in `_accruedFeeAndAssets()`:

```solidity
uint256 totalInterest = newTotalAssets - lastTotalAssetsCached;
if (totalInterest != 0 && fee != 0) {
    uint256 feeAssets = totalInterest.mulDiv(fee, WAD);
    feeShares = _convertToSharesWithTotals(feeAssets, totalSupply(), newTotalAssets - feeAssets, Math.Rounding.Floor);
}
```

The comment at line 922 acknowledges: "It is acknowledged that `feeAssets` may be rounded down to 0 if `totalInterest * fee < WAD`."

With `WAD = 1e18`, if `totalInterest * fee < 1e18`, then `feeAssets = 0`. For example, with a 10% fee (`fee = 0.1e18 = 1e17`), any `totalInterest < 10` will produce `feeAssets = 0`. For a 1% fee (`fee = 1e16`), any `totalInterest < 100` will produce `feeAssets = 0`.

A frequent caller (or bot) could trigger `_accrueInterest()` via `deposit()`/`withdraw()` calls at high frequency, ensuring `totalInterest` stays below the threshold between each accrual. This allows yield to be harvested without paying the performance fee.

**Impact:**

For vaults with low-decimal underlying assets (e.g., USDC with 6 decimals), a `totalInterest` of 100 represents 0.0001 USDC -- negligible. For high-decimal assets (18 decimals), `totalInterest` of 100 is 100 wei -- also negligible. The practical impact is limited because the gas cost of triggering frequent accruals far exceeds the fee savings. However, for vaults with extremely high TVL and low fee rates, the cumulative fee bypass could become material if accruals are triggered by normal user activity rather than targeted attacks.

**Recommendation:**

This is a known and acknowledged trade-off (documented in the comment). The impact is negligible for practical fee rates and interaction frequencies. No action required unless the protocol intends to support very low fee rates (< 0.1%) on high-TVL vaults.

---

### [M-05] Strategy vault `previewRedeem` as price oracle is manipulable via donations

**Skill:** audit-oracle
**Severity:** Medium
**Location:** `EulerEarn.sol` lines 392-393, 487-494

**Description:**

The contract uses `id.previewRedeem(supplyShares)` to determine the current value of its holdings in each strategy vault. This is used in:
- `expectedSupplyAssets()` (line 493) -- called throughout for accounting
- `reallocate()` (line 393) -- for determining current supply and computing withdrawal/supply amounts
- `maxWithdrawFromStrategy()` (line 488) -- for withdrawal limits
- `_accruedFeeAndAssets()` (line 907) -- for computing `realTotalAssets`

`previewRedeem` is a view function on the strategy vault that returns the expected asset amount for a given share amount. For most ERC4626 vaults, this is based on `totalAssets / totalSupply`, which can be manipulated via direct token transfers ("donations") to the strategy vault.

If an attacker donates tokens directly to a strategy vault, `previewRedeem` returns an inflated value. This inflates `realTotalAssets` in EulerEarn, which in turn:
1. Inflates the EulerEarn share price for all holders
2. Generates `totalInterest` that triggers performance fee extraction
3. Can manipulate `reallocate()` decisions

**Impact:**

An attacker who donates to a strategy vault can:
1. Inflate the EulerEarn share price temporarily, then withdraw at the inflated rate
2. Force performance fee extraction on phantom gains (griefing the fee recipient with eventually-lost fees, or the vault holders via dilution)

The impact is mitigated by:
1. Strategy vaults that use `VIRTUAL_AMOUNT` or similar inflation attack protections reduce donation effectiveness
2. The donation cost makes this attack expensive relative to extractable value
3. EulerEarn's own `VIRTUAL_AMOUNT` provides some resistance at the aggregator level

However, strategy vaults with low TVL or no donation protection are vulnerable. The attack does not require the attacker to hold EulerEarn shares -- they can manipulate the accounting from outside.

**Recommendation:**

This is an inherent limitation of composing ERC4626 vaults. Mitigations include:
- Only whitelisting strategy vaults with robust donation protection
- Implementing a sanity check in `_accruedFeeAndAssets()` that caps `totalInterest` per accrual period (e.g., max 100% gain per interaction)
- Using time-weighted average share prices for strategy vaults rather than spot `previewRedeem`

---

### [L-01] `_setCap` uses `staticcall` to query permit2 address, silently defaults to `address(0)`

**Skill:** audit-state-validation
**Severity:** Low
**Location:** `EulerEarn.sol` lines 776-777

**Description:**

```solidity
(bool success, bytes memory result) = address(id).staticcall(abi.encodeCall(this.permit2Address, ()));
address permit2 = success && result.length >= 32 ? abi.decode(result, (address)) : address(0);
```

This queries the strategy vault for its `permit2Address`. If the call fails or returns less than 32 bytes, `permit2` defaults to `address(0)`. This is then passed to `forceApproveMaxWithPermit2()` and `revokeApprovalWithPermit2()`. If a strategy vault has a function with the same selector as `permit2Address()` but returns different data (selector collision), the decoded address could be incorrect, leading to approvals being set on the wrong address.

**Impact:**

The impact is low because:
1. Strategy vaults are whitelisted by the factory, reducing the risk of malicious selector collision
2. The default to `address(0)` is a safe fallback
3. The approval is for the strategy vault address, not an arbitrary address

**Recommendation:**

Consider adding a length check on the return data and validating the decoded address is not `address(0)` before using it for approval operations.

---

### [L-02] `setSupplyQueue` does not check for duplicate entries

**Skill:** audit-state-validation
**Severity:** Low
**Location:** `EulerEarn.sol` lines 325-337

**Description:**

The `setSupplyQueue()` function validates that each entry has a non-zero cap but does not check for duplicate entries:

```solidity
function setSupplyQueue(IERC4626[] calldata newSupplyQueue) external onlyAllocatorRole {
    uint256 length = newSupplyQueue.length;
    if (length > ConstantsLib.MAX_QUEUE_LENGTH) revert ErrorsLib.MaxQueueLengthExceeded();
    for (uint256 i; i < length; ++i) {
        if (config[newSupplyQueue[i]].cap == 0) revert ErrorsLib.UnauthorizedMarket(newSupplyQueue[i]);
    }
    supplyQueue = newSupplyQueue;
}
```

In contrast, `updateWithdrawQueue()` (line 340) uses a `seen[]` array to prevent duplicates. If the supply queue contains duplicates, `_supplyStrategy()` may attempt to deposit into the same vault twice, potentially exceeding the cap check (`supplyAssets + suppliedAssets > supplyCap`) on the second iteration because `config[id].balance` was already updated in the first iteration.

The `maxDeposit()` view function also iterates the supply queue and would double-count the available capacity for a duplicated vault, returning a higher value than actually depositible.

**Impact:**

This is an allocator-role-only function (trusted role). The primary impact is incorrect `maxDeposit()` reporting and potential cap-check bypasses for duplicated vaults during `_supplyStrategy()`. However, since `_supplyStrategy` updates `config[id].balance` after each successful deposit, the second deposit attempt for a duplicated vault would see the updated balance and correctly compute remaining capacity, so the cap cannot actually be exceeded beyond what it allows.

**Recommendation:**

Add a duplicate check to `setSupplyQueue()` similar to `updateWithdrawQueue()`, or document that duplicates are the allocator's responsibility.

---

### [L-03] `_supplyStrategy` try/catch swallows all errors including out-of-gas

**Skill:** audit-reentrancy, audit-state-validation
**Severity:** Low
**Location:** `EulerEarn.sol` lines 824-828

**Description:**

```solidity
try id.deposit(toSupply, address(this)) returns (uint256 suppliedShares) {
    config[id].balance = (config[id].balance + suppliedShares).toUint112();
    assets -= toSupply;
} catch {}
```

The empty `catch {}` block catches all errors, including:
- Out-of-gas errors (which may indicate a deeper problem)
- Reentrancy guard failures (which could indicate an attack)
- Unexpected reverts from malicious strategy vaults

While this is intentional (to gracefully skip unavailable vaults), catching all errors means that important failure signals are silently swallowed. A strategy vault that consistently fails will never be detected through this code path.

**Impact:**

Low, since the allocator can monitor strategy vault health off-chain. However, silent failures make debugging harder and could mask reentrancy attacks.

**Recommendation:**

Consider emitting an event in the `catch` block to make failures visible on-chain:
```solidity
catch (bytes memory reason) {
    emit EventsLib.StrategyDepositFailed(id, toSupply, reason);
}
```

---

### [L-04] `reallocate` enforces `totalWithdrawn == totalSupplied` but not net-zero across individual strategies

**Skill:** audit-state-validation
**Severity:** Low
**Location:** `EulerEarn.sol` lines 383-442

**Description:**

The `reallocate()` function enforces a global invariant at line 441:
```solidity
if (totalWithdrawn != totalSupplied) revert ErrorsLib.InconsistentReallocation();
```

However, within the loop, the `allocation.assets == type(uint256).max` case (line 421) allows supplying `totalWithdrawn - totalSupplied` to the last vault in the allocation array. This is designed as a convenience feature, but it means the allocator must carefully order allocations: withdrawals first, then the `type(uint256).max` supply at the end.

If the allocator provides allocations in an incorrect order (supply with `type(uint256).max` before withdrawals), the `totalWithdrawn.zeroFloorSub(totalSupplied)` at line 422 would return 0 (since nothing has been withdrawn yet), and the vault would supply 0 to that strategy, silently ignoring the allocation.

**Impact:**

Low impact; this is a usability issue for the allocator role (trusted). Incorrect allocation ordering leads to a no-op rather than a loss of funds. The global balance check at line 441 ensures no assets are lost.

**Recommendation:**

Document the required allocation ordering (withdrawals before max-supply entries) or add a validation that `type(uint256).max` is only used in the last allocation entry.

---

## Checklist verification

### Math precision checklist

| Item | Status | Notes |
|------|--------|-------|
| Multiplication before division | PASS | All `mulDiv` calls use OpenZeppelin `Math.mulDiv()` which handles this correctly |
| Checks for rounding to zero | PARTIAL | Fee rounding to zero is acknowledged (line 922 comment). `ZeroShares` check at line 565 prevents zero-share deposits. No explicit minimum deposit amount. |
| Token amounts scaled to common precision | PASS | Single underlying asset; no cross-token decimal scaling needed. `VIRTUAL_AMOUNT` provides consistent offset. |
| No double-scaling | PASS | No evidence of double-scaling |
| Consistent precision scaling | PASS | `_convertToSharesWithTotals` and `_convertToAssetsWithTotals` are used consistently everywhere |
| SafeCast for downcasting | PARTIAL | `.toUint112()` used in some places (lines 433, 826) but raw `uint112()` cast at lines 415 and 847 [H-02] |
| Protocol fees round up | PARTIAL | Fee shares use `Math.Rounding.Floor` (line 927), meaning fees round *down*. This favours users over the protocol. Acceptable design choice per ERC4626 convention but noted. |
| Decimal assumptions documented | PASS | No hardcoded decimal assumptions |
| Interest calculations use correct time units | N/A | No time-based interest calculation; fees are based on delta of totalAssets |
| Token pair directions consistent | N/A | Single underlying asset |

### State validation checklist

| Item | Status | Notes |
|------|--------|-------|
| Multi-step processes verify previous steps | PASS | `afterTimelock` modifier checks `validAt != 0` and `block.timestamp >= validAt` |
| Arrays validated for length | PASS | `MAX_QUEUE_LENGTH` enforced. Empty arrays are handled gracefully (loops simply don't execute). |
| Inputs validated for edge cases | PARTIAL | `setSupplyQueue` does not check for duplicates [L-02] |
| Return values checked | PASS | Strategy vault return values (shares) are used in balance updates |
| State transitions atomic | PASS | `nonReentrant` guard and proper ordering ensure atomicity |
| ID existence verified | PASS | `config[id].enabled` checked where needed |
| Array parameters have matching length | N/A | No multi-array parameters |
| Access control on admin functions | PASS | Proper modifier hierarchy: `onlyOwner`, `onlyCuratorRole`, `onlyAllocatorRole`, `onlyGuardianRole` |
| State variables updated before external calls | PARTIAL | `_withdraw()` updates `lastTotalAssets` before external calls, but `_deposit()` updates after [M-03] |
| Pause mechanisms | N/A | No explicit pause mechanism |

### Reentrancy checklist

| Item | Status | Notes |
|------|--------|-------|
| CEI pattern | PARTIAL | `_deposit()` calls `_supplyStrategy()` (external) before `_updateLastTotalAssets()` but this is intentional. `_withdraw()` updates `lastTotalAssets` first, then calls `_withdrawStrategy()`. Mixed pattern. |
| NonReentrant modifiers | PASS | Applied to all state-changing ERC4626 functions (deposit, mint, withdraw, redeem, setFee, setFeeRecipient, submitCap, reallocate) |
| Token assumptions | PASS | Uses SafeERC20 throughout, permit2 integration for transfers |
| Cross-function analysis | PARTIAL | View functions not protected by nonReentrant [M-03] |
| Read-only safety | PARTIAL | View functions can return inconsistent values during reentrancy [M-03] |

### Oracle checklist

| Item | Status | Notes |
|------|--------|-------|
| Stale price checks | N/A | No Chainlink or external oracle integration |
| L2 sequencer check | N/A | No oracle dependency |
| Feed-specific heartbeats | N/A | |
| Oracle precision | N/A | |
| Price feed addresses | N/A | |
| Oracle revert handling | PARTIAL | Strategy vault `previewRedeem` calls are not wrapped in try/catch (lines 393, 488, 493, 907). If a strategy vault's `previewRedeem` reverts, it DoS's the entire vault. |
| Depeg monitoring | N/A | |
| Min/max validation | N/A | |
| TWAP usage | N/A | Uses `previewRedeem` spot values [M-05] |
| Price direction | N/A | |
| Circuit breaker checks | N/A | |

### Lending checklist

| Item | Status | Notes |
|------|--------|-------|
| Liquidation timing | N/A | Not a lending protocol |
| Collateral integrity | N/A | |
| Loan closure | N/A | |
| Symmetric pause | N/A | No pause mechanism |
| Token restrictions | PASS | Strategy vaults whitelisted by factory; disabling a market follows proper timelock |
| Grace period | N/A | |
| Liquidation shares | N/A | |
| Repayment routing | N/A | |
| Minimum loan size | N/A | |
| Interest precision | PARTIAL | Fee rounding to zero acknowledged [M-04] |
| Pool parameters | N/A | |
| Atomic accounting | PARTIAL | `lastTotalAssets` drift causes `lostAssets` inflation [M-01] |
| Outstanding loans | N/A | Strategy vault balances tracked via `config[id].balance` |

---

## Design observations (informational, not findings)

1. **VIRTUAL_AMOUNT inflation protection:** The contract uses `ConstantsLib.VIRTUAL_AMOUNT` as an offset in share/asset conversions (lines 678, 691). This is a standard mitigation against the ERC4626 inflation attack (first depositor attack). The implementation follows the virtual shares/assets pattern correctly.

2. **Ownable2Step ownership:** The contract inherits from OpenZeppelin's `Ownable2Step`, providing secure two-step ownership transfer. This is best practice and does not need to be flagged.

3. **EVC integration:** The `_msgSender()` override (line 743) uses `EVCUtil._msgSender()`, which returns the authenticated account from the EVC. The `_msgSenderOnlyEVCAccountOwner()` pattern in modifiers ensures only account owners (not operators) can call privileged functions.

4. **Fee architecture:** Performance fees are calculated on total interest (increase in `totalAssets`) since the last accrual. Fee shares are minted to `feeRecipient`, diluting existing holders proportionally. The fee is capped at `ConstantsLib.MAX_FEE` (line 245). The `_accrueInterest()` call at the start of deposit/mint/withdraw/redeem ensures fee accrual happens before share price changes.

5. **lostAssets design:** The `lostAssets` mechanism is a deliberate design choice to handle strategy vault losses without immediately impacting the share price. When `realTotalAssets` drops below `lastTotalAssets - lostAssets`, the difference is absorbed into `lostAssets`. This means unrealised losses do not immediately reduce share price, providing a buffer against temporary strategy losses.
