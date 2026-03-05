# EulerEarn security audit report (kadenzipfel/scv-scan)

**Target:** `src/EulerEarn.sol` (930 lines, Solidity 0.8.26)
**Methodology:** 4-phase systematic scan against 36 vulnerability classes

---

## Confirmed findings

### 1. Inconsistent use of SafeCast for type downcasts

**File:** `src/EulerEarn.sol` L415, L847
**Severity:** Low

**Description:** The contract imports and uses OpenZeppelin's `SafeCast` library for `uint112` conversions in some locations (e.g., line 433 uses `.toUint112()`), but uses raw `uint112()` casts in the `reallocate` function (line 415) and `_withdrawStrategy` function (line 847). While the subtraction itself is checked by Solidity 0.8.x's built-in overflow protection, the subsequent downcast from `uint256` to `uint112` would silently truncate if the result exceeds `type(uint112).max`. In practice, since `config[id].balance` is already stored as `uint112`, the subtraction result should always fit. However, the inconsistency is a code quality concern and violates the defensive programming principle established elsewhere in the contract.

**Code:**
```solidity
// Line 415 - raw cast in reallocate()
config[id].balance = uint112(supplyShares - withdrawnShares);

// Line 847 - raw cast in _withdrawStrategy()
config[id].balance = uint112(config[id].balance - withdrawnShares);

// Line 433 - SafeCast used correctly in the same function
config[id].balance = (supplyShares + suppliedShares).toUint112();
```

**Recommendation:** Use `SafeCast.toUint112()` consistently for all `uint112` conversions, as is already done on line 433. Replace raw `uint112()` casts with `.toUint112()` to ensure truncation is caught immediately rather than silently corrupting state:
```solidity
config[id].balance = (supplyShares - withdrawnShares).toUint112();
```

---

### 2. Silent failure in strategy deposit and withdrawal via empty catch blocks

**File:** `src/EulerEarn.sol` L824-L828, L845-L849
**Severity:** Low

**Description:** The `_supplyStrategy` and `_withdrawStrategy` functions use `try/catch {}` with empty catch blocks when interacting with strategy vaults. While this is intentionally designed to make the vault resilient to individual strategy failures, the completely empty catch blocks mean that **all** error types are silently swallowed, including unexpected errors that might indicate a serious problem (e.g., strategy vault returning manipulated values, or ERC4626 compliance failures). No event is emitted on failure, making it impossible to detect and respond to strategy issues off-chain.

In `_withdrawStrategy` specifically, if a strategy vault's `withdraw` call consistently fails silently, the function will iterate through all remaining vaults and ultimately revert with `NotEnoughLiquidity`, even though the vault may hold sufficient assets across strategies. This creates a potential griefing vector where a single malfunctioning strategy vault (that reports available assets via `maxWithdraw` but then reverts on actual withdrawal) could reduce the effective liquidity available to users.

**Code:**
```solidity
// _supplyStrategy (L824-828)
try id.deposit(toSupply, address(this)) returns (uint256 suppliedShares) {
    config[id].balance = (config[id].balance + suppliedShares).toUint112();
    assets -= toSupply;
} catch {}

// _withdrawStrategy (L845-849)
try id.withdraw(toWithdraw, address(this), address(this)) returns (uint256 withdrawnShares) {
    config[id].balance = uint112(config[id].balance - withdrawnShares);
    assets -= toWithdraw;
} catch {}
```

**Recommendation:** Emit an event in the catch block to enable off-chain monitoring of strategy failures. Consider differentiating between expected failures (e.g., insufficient liquidity) and unexpected failures:
```solidity
} catch (bytes memory reason) {
    emit EventsLib.StrategyDepositFailed(id, toSupply, reason);
}
```

---

### 3. Potential ERC4626 standard deviation with Permit2 deposit flow

**File:** `src/EulerEarn.sol` L697-L708
**Severity:** Informational

**Description:** The `_deposit` function overrides ERC4626's standard deposit flow by using `safeTransferFromWithPermit2` instead of the standard `safeTransferFrom`. This is documented in the code (line 119-120: "the contract deviates slightly from the ERC2612 standard"), but integrators who expect standard ERC4626 behaviour may encounter unexpected failures if they have not granted Permit2 approvals. Standard ERC20 `approve` will not work unless the `SafeERC20Permit2Lib` library falls back to standard transfers when Permit2 is not available.

**Code:**
```solidity
function _deposit(address caller, address receiver, uint256 assets, uint256 shares) internal override {
    IERC20(asset()).safeTransferFromWithPermit2(caller, address(this), assets, permit2Address);
    _mint(receiver, shares);
    // ...
}
```

**Recommendation:** This is an acknowledged design decision. Ensure documentation clearly states that depositors must approve via Permit2 or standard ERC20 approval (if supported by the library fallback). Consider adding a NatSpec comment on the public `deposit` and `mint` functions explicitly documenting this requirement.

---

### 4. Frontrunning risk on share price during deposit and withdrawal

**File:** `src/EulerEarn.sol` L560-L611
**Severity:** Informational

**Description:** The `deposit`, `mint`, `withdraw`, and `redeem` functions do not include slippage protection parameters (e.g., `minSharesOut` for deposits, `maxSharesIn` for withdrawals). An MEV searcher could observe a pending deposit transaction, manipulate the share price by depositing/withdrawing to strategy vaults (if they are also an allocator), and extract value. However, this is standard ERC4626 behaviour, and slippage protection is typically implemented at the router level rather than in the vault itself. The `nonReentrant` modifier and the use of `lastTotalAssets` for share price calculation (rather than live `totalAssets()`) significantly limit the attack surface.

**Code:**
```solidity
function deposit(uint256 assets, address receiver) public override nonReentrant returns (uint256 shares) {
    _accrueInterest();
    shares = _convertToSharesWithTotals(assets, totalSupply(), lastTotalAssets, Math.Rounding.Floor);
    if (shares == 0) revert ErrorsLib.ZeroShares();
    _deposit(_msgSender(), receiver, assets, shares);
}
```

**Recommendation:** This is a known characteristic of ERC4626 vaults. Consider documenting that integrators should use a router contract with slippage protection for end-user-facing interactions. The use of `lastTotalAssets` rather than live `totalAssets()` is already a strong mitigation.

---

## Discarded candidates (false positives)

### DoS with block gas limit (loops over queues)
**Lines:** 641, 812, 839, 861, 905
**Reason:** The `withdrawQueue` and `supplyQueue` arrays are bounded by `ConstantsLib.MAX_QUEUE_LENGTH` (checked on line 785 and line 328). This is an admin-controlled, capped array. False positive per the reference.

### Reentrancy via strategy vault external calls
**Lines:** 825, 846, 409, 411, 431
**Reason:** All public entry points (`deposit`, `mint`, `withdraw`, `redeem`, `reallocate`, `setFee`, `setFeeRecipient`, `submitCap`) use the `nonReentrant` modifier from OpenZeppelin's `ReentrancyGuard`. False positive per the reference.

### Timestamp dependence
**Lines:** 187, 317, 368
**Reason:** `block.timestamp` is used for timelock enforcement with time windows of hours/days (bounded by `POST_INITIALIZATION_MIN_TIMELOCK` and `MAX_TIMELOCK`). A 15-second manipulation is irrelevant at this scale. False positive per the reference.

### Insufficient access control on acceptTimelock/acceptGuardian/acceptCap
**Lines:** 497-511
**Reason:** These functions are intentionally permissionless by design. Security is enforced by: (1) only the owner can `submitTimelock` / `submitGuardian` (setting the pending value), and (2) the `afterTimelock` modifier ensures the timelock period has elapsed. Anyone can finalize the pending change after the timelock, which is the intended governance pattern (also used in Morpho Blue). False positive per the reference.

### Shadowing state variables (_name, _symbol)
**Lines:** 104, 107
**Reason:** Solidity >= 0.6.0 disallows state variable shadowing with a compiler error. The contract compiles on 0.8.26, so these are not actually shadowing the parent's variables. The contract re-declares `_name` and `_symbol` to override ERC20's behaviour, and overrides the `name()` and `symbol()` view functions accordingly. False positive per the reference.

### Unsafe low-level call (staticcall)
**Line:** 776
**Reason:** The `staticcall` return value is properly checked (`success` boolean and `result.length >= 32`). On failure, `permit2` defaults safely to `address(0)`. False positive per the reference.

### Unsafe downcast uint64(block.timestamp + timelock)
**Line:** 317
**Reason:** `block.timestamp` currently fits in ~31 bits, and `timelock` is bounded by `MAX_TIMELOCK`. The sum will fit in `uint64` for centuries. While using SafeCast would be more defensive, this is not practically exploitable. Discarded as not actionable.

### Lack of precision in fee calculation
**Line:** 923
**Reason:** The fee calculation uses `mulDiv` from OpenZeppelin's Math library, which provides full-precision multiplication before division. The code acknowledges that `feeAssets` may round to 0 for very small interest amounts. False positive per the reference.

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 0     |
| High     | 0     |
| Medium   | 0     |
| Low      | 2     |
| Info     | 2     |

**Overall assessment:** EulerEarn demonstrates strong security practices. The contract uses OpenZeppelin's `ReentrancyGuard` on all external state-changing entry points, employs `Ownable2Step` for secure ownership transfers, enforces timelock governance for sensitive parameter changes, bounds queue lengths to prevent gas limit DoS, and uses `SafeERC20` for token interactions. The two Low findings (inconsistent SafeCast usage and silent catch blocks) are code quality concerns rather than exploitable vulnerabilities. The codebase shows evidence of being forked from Morpho's well-audited MetaMorpho vault, with Euler-specific adaptations (EVC integration, Permit2 support) that are implemented correctly.
