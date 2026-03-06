# YieldNest ynRWAx vault security audit report

**Target:** YieldNest ynRWAx Vault (Solidity 0.8.24)
**Methodology:** QuillAI/QuillShield OWASP Smart Contract Top 10
**Date:** 2026-03-06
**Auditor:** Claude Opus 4.6 (automated)

## Executive summary

The YieldNest ynRWAx vault is a multi-asset ERC4626-compatible vault with role-based access control, a configurable withdrawal fee system, a hooks mechanism for extensibility, and a processor/guard system for permissioned external calls. The codebase demonstrates generally competent engineering with proper use of OpenZeppelin upgradeability primitives, reentrancy guards on all user-facing mutative functions, SafeERC20 throughout, and namespaced storage patterns.

The audit identified **3 High**, **5 Medium**, and **6 Low/Informational** findings across the OWASP Smart Contract Top 10 categories.

---

## Table of contents

1. [SC-01: Reentrancy](#sc-01-reentrancy)
2. [SC-02: Oracle manipulation / flash loan attacks](#sc-02-oracle-manipulation--flash-loan-attacks)
3. [SC-03: Access control](#sc-03-access-control)
4. [SC-04: Signature replay](#sc-04-signature-replay)
5. [SC-05: Proxy upgrade safety](#sc-05-proxy-upgrade-safety)
6. [SC-06: Arithmetic / precision loss](#sc-06-arithmetic--precision-loss)
7. [SC-07: State validation / invariants](#sc-07-state-validation--invariants)
8. [SC-08: DoS / griefing](#sc-08-dos--griefing)
9. [SC-09: External call safety](#sc-09-external-call-safety)
10. [SC-10: Governance / semantic guard analysis](#sc-10-governance--semantic-guard-analysis)

---

## SC-01: Reentrancy

### Analysis

All user-facing mutative functions (`deposit`, `mint`, `withdraw`, `redeem`, `depositAsset`, `processAccounting`) carry the `nonReentrant` modifier from OpenZeppelin's `ReentrancyGuardUpgradeable`. The constructor calls `__ReentrancyGuard_init()`.

**Call graph for external interactions:**

| Function | External calls | `nonReentrant` | CEI compliant |
|----------|---------------|----------------|---------------|
| `deposit()` | `safeTransferFrom`, hooks (before/after) | Yes | Partial (see F-01) |
| `mint()` | `safeTransferFrom`, hooks (before/after) | Yes | Partial (see F-01) |
| `withdraw()` | `IStrategy.withdraw`, hooks (before/after) | Yes | Yes (burn before transfer) |
| `redeem()` | `IStrategy.withdraw`, hooks (before/after) | Yes | Yes |
| `depositAsset()` | `safeTransferFrom`, hooks (before/after) | Yes | Partial (see F-01) |
| `processAccounting()` | hooks, `IProvider.getRate`, `balanceOf` | Yes | N/A (no value transfer) |
| `processor()` | Arbitrary `.call` to targets | No | N/A (guarded by role) |
| `mintShares()` | None (only `_mint`) | No | N/A (hooks-only caller) |
| `withdrawAsset()` | `safeTransfer` | No | Yes (burn before transfer) |

### F-01: Hooks receive control before state is fully committed (cross-function via hooks)

**Severity:** LOW
**Variant:** Callback (via hooks system)
**Guard status:** Mitigated by `nonReentrant`

**Description:**
In `_depositAsset()` (BaseVault.sol:503-524), the `beforeDeposit` hook is called before `_deposit()` executes `_addTotalAssets`, `safeTransferFrom`, and `_mint`. The hooks contract receives execution control before the vault's state has been updated. Similarly, `afterDeposit` is called after state updates but the hooks contract could read stale state from other contracts.

However, since `nonReentrant` is applied to all entry points, the hooks contract cannot re-enter any vault function during execution. This mitigates the cross-function reentrancy risk.

**CEI violation:**
- `beforeDeposit` hook at BaseVault.sol:517 executes before state changes at lines 547-550.
- This is by design (pre-hooks are meant to run before state changes).

**Impact:** Minimal under current architecture because `nonReentrant` prevents re-entry. Risk increases if future integrations rely on vault state during hook execution.

**Recommendation:** Document that hooks MUST NOT assume vault state is consistent during `before*` callbacks. Consider adding a comment to this effect.

### F-02: `processor()` lacks `nonReentrant` modifier

**Severity:** MEDIUM
**OWASP category:** SC-01 (Reentrancy)
**Location:** `BaseVault.sol:956-963`

**Description:**
The `processor()` function makes arbitrary external calls to target contracts via `.call{value: values[i]}(data[i])` inside a loop (VaultLib.sol:448-456). While it is protected by `PROCESSOR_ROLE` access control and the `Guard.validateCall` mechanism, it does not carry `nonReentrant`. A malicious or compromised target contract called by the processor could re-enter `processAccounting()` (which IS `nonReentrant`, so this specific path is blocked) but could re-enter `processor()` itself recursively.

**Exploit scenario:**
1. A PROCESSOR_ROLE holder calls `processor([targetA], [0], [data])`.
2. `targetA` is a contract whose function is whitelisted in processor rules.
3. `targetA`'s function calls back into the vault's `processor()` function.
4. Since the caller is still PROCESSOR_ROLE and `processor()` has no reentrancy guard, the re-entrant call succeeds.
5. The nested call can execute additional whitelisted actions with potentially stale assumptions about vault state.

**Impact:** Conditional on processor rule configuration. A whitelisted target that calls back could execute additional operations in an inconsistent state. Severity is medium because the PROCESSOR_ROLE is trusted and targets are whitelisted.

**Recommendation:** Add `nonReentrant` to the `processor()` function.

### F-03: `withdrawAsset()` lacks `nonReentrant` modifier

**Severity:** LOW
**OWASP category:** SC-01 (Reentrancy)
**Location:** `BaseVault.sol:613-629`

**Description:**
`withdrawAsset()` is protected by `ASSET_WITHDRAWER_ROLE` but lacks `nonReentrant`. It calls `_withdrawAsset()` which calls `SafeERC20.safeTransfer`. With standard ERC-20 tokens this is safe, but if an ERC-777 compatible token were added as an asset, the transfer could trigger a `tokensReceived` callback.

**Impact:** Low because asset additions are controlled by `ASSET_MANAGER_ROLE` and the function is restricted to `ASSET_WITHDRAWER_ROLE`. However, defence in depth recommends the guard.

**Recommendation:** Add `nonReentrant` to `withdrawAsset()`.

---

## SC-02: Oracle manipulation / flash loan attacks

### Analysis

The vault relies on an external `IProvider` contract for asset-to-base conversion rates. The `IProvider.getRate(asset)` function is the sole oracle dependency.

**Oracle dependency map:**

```
convertAssetToBase() -> IProvider.getRate(asset_)  [VaultLib.sol:227]
convertBaseToAsset() -> IProvider.getRate(asset_)  [VaultLib.sol:245]
  -> convertToShares() -> convertAssetToBase()
  -> convertToAssets() -> convertBaseToAsset()
    -> deposit/mint/withdraw/redeem (all paths)
```

**Trust level:** Unknown (depends entirely on the IProvider implementation).

### F-04: Complete dependence on unvalidated external rate provider

**Severity:** HIGH
**OWASP category:** SC-02 (Oracle manipulation)
**Location:** `VaultLib.sol:221-229`, `VaultLib.sol:239-247`

**Description:**
The vault performs zero validation on the rate returned by `IProvider.getRate(asset)`. There is no check for:
- Rate being zero (division by zero in `convertBaseToAsset` when rate is used as denominator)
- Rate being unreasonably high or low (circuit breaker / bounds check)
- Rate staleness (no timestamp or freshness check)
- Rate manipulation resistance (depends entirely on provider implementation)

The `convertAssetToBase` function at VaultLib.sol:228 computes:
```solidity
baseAssets = assets.mulDiv(rate, 10 ** (getAssetStorage().assets[asset_].decimals), rounding);
```

If `rate == 0`, `baseAssets` will be 0 for any input, allowing an attacker to deposit assets and receive disproportionate shares (since `convertToShares` uses `baseAssets.mulDiv(totalSupply + 1, totalAssets + 1, rounding)` and `baseAssets = 0` would yield 0 shares -- actually this protects against the zero rate on deposit but could cause issues on withdrawal paths).

More critically, in `convertBaseToAsset` at VaultLib.sol:246:
```solidity
assets = baseAssets.mulDiv(10 ** (getAssetStorage().assets[asset_].decimals), rate, rounding);
```

If `rate == 0`, this would revert with a division-by-zero in `Math.mulDiv`, bricking all withdrawals and view functions that convert to asset units.

If the provider returns a manipulated rate (e.g., via flash loan manipulation of an underlying AMM pool), the vault's share pricing can be exploited.

**Exploit scenario (rate manipulation):**
1. Attacker manipulates the external rate source (if it depends on a spot price).
2. Attacker deposits at an artificially favourable rate, receiving excess shares.
3. Rate returns to normal.
4. Attacker withdraws at the true rate, extracting value.

**Missing validations:**
- `rate > 0` check
- Rate bounds / circuit breaker
- Staleness check (no `updatedAt` or heartbeat)
- Multi-oracle consensus

**Recommendation:**
- Add `require(rate > 0, "ZeroRate")` to `convertAssetToBase` and `convertBaseToAsset`.
- Consider implementing rate bounds (min/max acceptable rate) configurable by admin.
- Consider adding a rate deviation circuit breaker that pauses the vault if rates move beyond a threshold.

### F-05: `computeTotalAssets()` uses `balanceOf(address(this))` -- donation attack surface

**Severity:** MEDIUM
**OWASP category:** SC-02 (Flash loan / oracle manipulation)
**Location:** `VaultLib.sol:374-389`

**Description:**
`computeTotalAssets()` iterates over all assets and reads `IERC20(assetList[i]).balanceOf(address(this))` to compute the vault's total value. When `countNativeAsset` is true, it also includes `address(this).balance`.

This creates a donation attack surface: anyone can directly transfer tokens to the vault (bypassing `deposit()`) or send native ETH to the `receive()` function, inflating `computeTotalAssets()` without minting corresponding shares. This is relevant when `alwaysComputeTotalAssets` is enabled or when `processAccounting()` is called.

**Impact:**
- When `alwaysComputeTotalAssets == true`: every share price calculation uses the inflated total, allowing a first-depositor inflation attack variant.
- When `alwaysComputeTotalAssets == false` (cached mode): impact is limited to the next `processAccounting()` call, which updates the cached value.

The vault uses a `+1` offset in share conversion (`shares.mulDiv(totalAssets + 1, totalSupply + 1, rounding)`) which provides some protection against the classic ERC4626 inflation attack, but the offset of 1 is insufficient for large donation amounts.

**Exploit scenario:**
1. Vault is newly initialised with `alwaysComputeTotalAssets == true`.
2. Attacker deposits 1 wei, receiving 1 share.
3. Attacker donates 1,000,000 tokens directly to the vault.
4. `computeTotalAssets()` now returns ~1,000,000 tokens.
5. Next depositor deposits 500,000 tokens: `shares = 500000 * (1 + 1) / (1000000 + 1) = 0` (rounds to zero).
6. Depositor's tokens are trapped with zero shares.

**Recommendation:**
- Increase the virtual offset in `convertToShares`/`convertToAssets` (e.g., use `10**decimalsOffset()` as in OpenZeppelin's ERC4626 implementation).
- Consider requiring a minimum initial deposit or seeding the vault with dead shares.
- When `alwaysComputeTotalAssets == true`, the donation surface is always live; document this risk.

---

## SC-03: Access control

### Analysis

The vault uses OpenZeppelin's `AccessControlUpgradeable` with a comprehensive set of roles:

| Role | Purpose | Functions |
|------|---------|-----------|
| `DEFAULT_ADMIN_ROLE` | Role admin for all roles | Inherited OZ functions |
| `PROCESSOR_ROLE` | Execute arbitrary whitelisted calls | `processor()` |
| `PAUSER_ROLE` | Pause the vault | `pause()` |
| `UNPAUSER_ROLE` | Unpause the vault | `unpause()` |
| `PROVIDER_MANAGER_ROLE` | Set the rate provider | `setProvider()` |
| `BUFFER_MANAGER_ROLE` | Set the buffer strategy | `setBuffer()` |
| `ASSET_MANAGER_ROLE` | Add/update/delete assets | `addAsset()`, `updateAsset()`, `deleteAsset()`, `setAlwaysComputeTotalAssets()` |
| `PROCESSOR_MANAGER_ROLE` | Configure processor rules | `setProcessorRule()`, `setProcessorRules()` |
| `HOOKS_MANAGER_ROLE` | Set hooks contract | `setHooks()` |
| `ASSET_WITHDRAWER_ROLE` | Withdraw specific assets | `withdrawAsset()` |
| `FEE_MANAGER_ROLE` | Set withdrawal fees | `setBaseWithdrawalFee()`, `overrideBaseWithdrawalFee()` |

### F-06: `processAccounting()` is callable by anyone

**Severity:** LOW
**OWASP category:** SC-03 (Access control)
**Location:** `BaseVault.sol:933-935`

**Description:**
`processAccounting()` is `public` with only `nonReentrant` -- no role restriction. Any external caller can trigger a recalculation of the vault's cached `totalAssets`. While this function merely syncs the cached value to the computed value, the timing of this call can have economic implications:

- If rates have changed unfavourably, calling `processAccounting()` updates the share price, potentially causing depositors or withdrawers to get a worse deal.
- If rates have moved favourably, delaying `processAccounting()` preserves stale pricing.

**Impact:** Low. The function updates cached state to reflect reality, so calling it is generally benign. However, MEV bots could sandwich `processAccounting()` calls for profit.

**Recommendation:** Consider whether `processAccounting()` should be role-restricted or whether it is acceptable as a public keeper function.

### F-07: `mintShares()` relies on caller identity check against mutable `hooks()` address

**Severity:** MEDIUM
**OWASP category:** SC-03 (Access control)
**Location:** `BaseVault.sol:970-976`

**Description:**
`mintShares()` mints arbitrary shares to any recipient, guarded only by:
```solidity
if (msg.sender != address(hooks())) {
    revert CallerNotHooks();
}
```

The hooks address is mutable via `setHooks()` (restricted to `HOOKS_MANAGER_ROLE`). If the HOOKS_MANAGER_ROLE is compromised or granted to a malicious actor, they can:
1. Deploy a malicious hooks contract that calls `mintShares(attacker, huge_amount)`.
2. Call `setHooks(malicious_hooks_address)`.
3. Trigger any vault operation that invokes hooks.
4. The malicious hooks contract mints unlimited shares to the attacker.
5. Attacker redeems shares for vault assets.

**Impact:** Complete vault drain if HOOKS_MANAGER_ROLE is compromised. The `mintShares` function bypasses all deposit/fee/pause logic.

**Recommendation:**
- Consider adding additional guards to `mintShares()` such as a maximum mint amount per call, or requiring the vault to be in a specific state.
- Ensure HOOKS_MANAGER_ROLE is behind a timelock so users can react to malicious hooks changes.
- Consider emitting an event from `mintShares()` for monitoring.

### F-08: `PROVIDER_MANAGER_ROLE` can set a malicious provider to manipulate all share pricing

**Severity:** HIGH (centralisation risk)
**OWASP category:** SC-03 (Access control)
**Location:** `BaseVault.sol:781-783`, `VaultLib.sol:350-357`

**Description:**
The `setProvider()` function allows the `PROVIDER_MANAGER_ROLE` to change the rate provider at any time with no timelock, delay, or validation beyond a zero-address check. A malicious or compromised provider can return arbitrary rates, allowing:

- Inflated rates on deposit: attacker deposits cheap assets, gets excess shares.
- Deflated rates on withdrawal: existing shareholders receive less than entitled.
- Zero rate: bricks withdrawals (division by zero in `convertBaseToAsset`).

**Impact:** Complete vault drain or permanent DoS depending on the manipulated rate values. This is the single most critical trust assumption in the system.

**Recommendation:**
- Place `setProvider()` behind a timelock.
- Validate the new provider by calling `getRate()` on at least one asset and checking the result is reasonable.
- Consider a two-step provider change (propose + accept after delay).

---

## SC-04: Signature replay

### Analysis

The vault inherits `ERC20PermitUpgradeable` from OpenZeppelin, which provides EIP-2612 permit functionality with EIP-712 domain separator support. This is the only signature verification in the contract.

**Findings:** No issues. OpenZeppelin's implementation handles:
- EIP-712 domain separator with `chainId` and `verifyingContract`
- Sequential per-user nonces
- Deadline / expiry check
- `ECDSA.recover` (not raw `ecrecover`)
- Domain separator recalculation on chain fork

**Verdict:** PASS -- no signature replay vulnerabilities identified.

---

## SC-05: Proxy upgrade safety

### Analysis

The vault uses the **Transparent Proxy** pattern (imports `TransparentUpgradeableProxy` and `ProxyAdmin` from OpenZeppelin in Common.sol).

**Checklist:**

| Check | Status | Notes |
|-------|--------|-------|
| `_disableInitializers()` in constructor | PASS | `BaseVault.sol:1003-1005`: `constructor() { _disableInitializers(); }` |
| `initializer` modifier on `initialize()` | PASS | `Vault.sol:45`: `function initialize(...) external virtual initializer` |
| EIP-1967 storage slots for proxy state | PASS | Uses OZ `TransparentUpgradeableProxy` |
| Namespaced storage (ERC-7201) | PASS | All storage uses assembly-computed slots |
| No constructor state in implementation | PASS | Only `_disableInitializers()` in constructor |
| Gap variables in base contracts | N/A | Uses namespaced storage pattern instead |

### F-09: Custom storage slot comments do not match declared hash preimages

**Severity:** INFORMATIONAL
**OWASP category:** SC-05 (Proxy upgrade safety)
**Location:** `VaultLib.sol:38-66`

**Description:**
The storage slot comments claim specific preimages but there is no compile-time verification. For example:

```solidity
// keccak256("yieldnest.storage.vault")
$.slot := 0x22cdba5640455d74cb7564fb236bbbbaf66b93a0cc1bd221f1ee2a6b2d0a2427
```

And then:

```solidity
// keccak256("yieldnest.storage.vault")  // <-- Same comment for ProcessorStorage!
$.slot := 0x52bb806a272c899365572e319d3d6f49ed2259348d19ab0da8abccd4bd46abb5
```

The comment for `getProcessorStorage()` at VaultLib.sol:62 says `keccak256("yieldnest.storage.vault")` -- the same preimage as `getVaultStorage()` -- but the actual slot value is different, meaning the comment is incorrect (the actual preimage is likely `keccak256("yieldnest.storage.processor")` or similar).

**Impact:** No runtime impact (the slot values are hard-coded correctly). However, incorrect comments could confuse future developers during upgrades and lead to storage collision if someone trusts the comments.

**Recommendation:** Verify and correct all storage slot comments. Add compile-time assertions (e.g., Foundry test) that validate `keccak256(preimage) == stored_slot_value`.

---

## SC-06: Arithmetic / precision loss

### Analysis

The vault uses OpenZeppelin's `Math.mulDiv` with explicit rounding throughout. Solidity 0.8.24 provides checked arithmetic by default.

**Rounding direction analysis:**

| Operation | Function | Rounding | Correct direction |
|-----------|----------|----------|-------------------|
| Deposit (assets -> shares) | `_convertToShares` | Floor | Yes (fewer shares minted) |
| Mint (shares -> assets) | `_convertToAssets` | Ceil | Yes (more assets required) |
| Withdraw (assets -> shares) | `previewWithdraw` -> `_convertToShares` | Ceil | Yes (more shares burned) |
| Redeem (shares -> assets) | `_convertToAssets` | Floor | Yes (fewer assets returned) |
| Fee on raw | `FeeMath.feeOnRaw` | Ceil | Yes (higher fee collected) |
| Fee on total | `FeeMath.feeOnTotal` | Ceil | Yes (higher fee collected) |
| Sub total assets on withdraw | `_convertAssetToBase` | Floor | Partial (see F-10) |
| Add total assets on deposit | baseAssets from `_convertToShares` | Floor | Yes |

### F-10: Rounding mismatch in `_subTotalAssets` during withdrawal creates persistent accounting drift

**Severity:** MEDIUM
**OWASP category:** SC-06 (Arithmetic / precision loss)
**Location:** `BaseVault.sol:591`, `BaseVault.sol:652`

**Description:**
During deposits, `_addTotalAssets(baseAssets)` adds the `baseAssets` value that was computed during `_convertToShares` (which always rounds `baseAssets` down, per VaultLib.sol:311: `baseAssets = convertAssetToBase(asset_, assets, rounding)` where `rounding` is Floor for deposits).

During withdrawals in `_withdraw()` (BaseVault.sol:591):
```solidity
_subTotalAssets(_convertAssetToBase(asset(), assets, Math.Rounding.Floor));
```

The withdrawal subtracts a *freshly recomputed* base asset value (also rounded Floor). However, the `assets` amount used here comes from `previewWithdraw` or `previewRedeem`, which apply fee logic and different rounding. The base amount subtracted may not exactly match what was originally added during the corresponding deposit, due to:
1. Rate changes between deposit and withdrawal.
2. Rounding in fee calculations.
3. The fresh recomputation using current rates vs. the historical rate at deposit time.

Over many deposit/withdrawal cycles, this creates a persistent drift between `vaultStorage.totalAssets` and the actual value computed by `computeTotalAssets()`.

**Impact:** The drift is corrected when `processAccounting()` is called, but between calls, the cached `totalAssets` may diverge from reality, causing slightly incorrect share pricing. The code comment at VaultLib.sol:270 acknowledges this: "May revert on underflow when withdrawn assets are valued higher than stored total assets."

**Recommendation:**
- Call `processAccounting()` periodically (this is the intended design).
- Consider documenting the expected magnitude of drift per deposit/withdrawal cycle.
- The `subTotalAssets` underflow protection mentioned in the comment relies on "seeding the vault to create enough of a buffer for error" -- ensure this seeding is performed and documented in deployment procedures.

### F-11: Fee calculation can produce zero for dust amounts

**Severity:** LOW
**OWASP category:** SC-06 (Arithmetic / precision loss)
**Location:** `FeeMath.sol:32-34`

**Description:**
The `feeOnRaw` function computes:
```solidity
return amount.mulDiv(fee, BASIS_POINT_SCALE, Math.Rounding.Ceil);
```

With `BASIS_POINT_SCALE = 1e8`, the fee rounds up (Ceil), which prevents zero fees for any non-zero amount when `fee > 0`. Specifically, `mulDiv(amount, fee, 1e8, Ceil)` will return at least 1 when `amount * fee > 0`.

**Verdict:** The Ceil rounding correctly prevents dust-amount fee bypass. No issue found.

### F-12: `baseWithdrawalFee` can be set to `BASIS_POINT_SCALE` (100%), enabling confiscation

**Severity:** LOW
**OWASP category:** SC-06 (Arithmetic / input validation)
**Location:** `LinearWithdrawalFeeLib.sol:57`, `LinearWithdrawalFeeLib.sol:69`

**Description:**
The validation allows `baseWithdrawalFee_ <= FeeMath.BASIS_POINT_SCALE` (i.e., up to 100% inclusive). A 100% withdrawal fee means users cannot withdraw any assets -- all withdrawal value is taken as fees. Similarly, per-user overrides can be set to 100%.

**Impact:** The FEE_MANAGER_ROLE can set confiscatory fees. This is a centralisation risk.

**Recommendation:** Consider enforcing a maximum fee cap (e.g., 10% or 20%) to limit the damage from a compromised FEE_MANAGER_ROLE.

---

## SC-07: State validation / invariants

### Analysis

**Key invariants:**

| Invariant | Maintained | Notes |
|-----------|-----------|-------|
| `totalAssets (cached) ~= computeTotalAssets()` | Approximately | Drift corrected by `processAccounting()` |
| `totalSupply = sum(balances)` | Yes | Maintained by OZ ERC20 |
| `asset list indices consistent` | Yes | See F-13 |
| `baseWithdrawalFee <= BASIS_POINT_SCALE` | Yes | Checked in setter |

### F-13: Asset deletion can corrupt the `hasAsset()` check for the moved asset

**Severity:** LOW
**OWASP category:** SC-07 (State validation)
**Location:** `VaultLib.sol:184-211`

**Description:**
`deleteAsset()` uses the swap-and-pop pattern:
```solidity
assetStorage.list[index] = assetStorage.list[assetStorage.list.length - 1];
assetStorage.list.pop();
delete assetStorage.assets[asset_];
```

It then updates the moved asset's index:
```solidity
if (index < assetStorage.list.length) {
    address movedAsset = assetStorage.list[index];
    assetStorage.assets[movedAsset].index = index;
}
```

The `hasAsset()` function checks:
```solidity
return assetStorage.list[assetParams.index] == asset_;
```

This works correctly for the moved asset because its index is updated. For the deleted asset, `assetStorage.assets[asset_]` is deleted (all fields zeroed), so `assetParams.index == 0` and `assetStorage.list[0]` is the base asset. Unless the deleted asset happens to be the base asset (which is prevented by `if (index == 0) revert BaseAsset()`), `hasAsset(deletedAsset)` will correctly return false.

However, if a new asset is later added with address identical to a previously deleted non-base asset, and that new asset is added at index 0... this cannot happen because index 0 is always occupied by the base asset.

**Verdict:** The logic is correct upon closer analysis. No vulnerability.

### F-14: `withdrawAsset()` does not subtract from `totalAssets` using the actual withdrawn base value

**Severity:** MEDIUM
**OWASP category:** SC-07 (State invariant violation)
**Location:** `BaseVault.sol:640-664`

**Description:**
In `_withdrawAsset()` (BaseVault.sol:652):
```solidity
_subTotalAssets(_convertAssetToBase(asset_, assets, Math.Rounding.Floor));
```

This subtracts the base-equivalent of the withdrawn asset amount from `totalAssets`. However, the share conversion at line 623:
```solidity
(shares,) = _convertToShares(asset_, assets, Math.Rounding.Ceil);
```

...converts to shares using Ceil rounding, meaning the user burns slightly more shares than the Floor-rounded base asset subtraction would suggest. Over time, this means `totalAssets` is slightly under-subtracted relative to the shares burned, causing a gradual increase in share price (beneficial to remaining shareholders but inconsistent).

**Impact:** Minor accounting discrepancy that accumulates over many `withdrawAsset` operations. Corrected by `processAccounting()`.

**Recommendation:** This appears to be by design (vault-favourable rounding). Document this explicitly.

---

## SC-08: DoS / griefing

### Analysis

### F-15: Guard `_isInArray()` uses unbounded linear search

**Severity:** LOW
**OWASP category:** SC-08 (DoS)
**Location:** `Guard.sol:22-28`, `Guard.sol:35-42`

**Description:**
The `validateCall()` function in `Guard.sol` iterates over `rule.paramRules` and for each ADDRESS param, calls `_isInArray()` which performs a linear search over the allow list:

```solidity
for (uint256 i = 0; i < array.length; i++) {
    if (array[i] == value) return true;
}
```

If a processor rule has a very large allow list, the gas cost of validation increases linearly. Since processor rules are set by `PROCESSOR_MANAGER_ROLE`, this is admin-controlled.

Additionally, the outer loop in `validateCall` at line 22:
```solidity
for (uint256 i = 0; i < rule.paramRules.length; i++) {
```

Only processes ADDRESS-type parameters. UINT256 parameters are silently skipped (no validation for them), which may be intentional but should be documented.

**Impact:** Low. The PROCESSOR_MANAGER_ROLE controls the allow list sizes.

**Recommendation:** Consider documenting maximum recommended allow list sizes. Consider using a mapping-based allowlist for O(1) lookups if large lists are expected.

### F-16: `computeTotalAssets()` iterates over all assets, including inactive ones

**Severity:** LOW
**OWASP category:** SC-08 (DoS / gas griefing)
**Location:** `VaultLib.sol:374-389`

**Description:**
`computeTotalAssets()` iterates over the entire asset list and queries `balanceOf` and `getRate` for each asset. As assets are added (by ASSET_MANAGER_ROLE), the gas cost of this function grows linearly. The function skips assets with zero balance (`if (balance == 0) continue`), but still calls `balanceOf` for every asset.

This affects `processAccounting()` and (when `alwaysComputeTotalAssets == true`) every deposit/withdraw/mint/redeem operation.

**Impact:** With many assets, operations become expensive. An extreme number of assets could theoretically exceed the block gas limit, bricking the vault. However, asset addition is admin-controlled.

**Recommendation:** Consider limiting the maximum number of assets or providing a paginated accounting mechanism.

### F-17: Native ETH force-feeding via `receive()` or selfdestruct

**Severity:** LOW
**OWASP category:** SC-08 (DoS / force-feeding)
**Location:** `BaseVault.sol:1010-1012`, `VaultLib.sol:378`

**Description:**
The vault has a `receive()` function that accepts ETH and emits `NativeDeposit`. When `countNativeAsset == true`, the native balance is included in `computeTotalAssets()` via `address(this).balance`.

Anyone can force-send ETH to the vault via:
1. The `receive()` function (any amount).
2. `selfdestruct` from another contract (bypasses receive).
3. Coinbase transaction targeting.

This inflates `computeTotalAssets()` without minting shares, donating value to existing shareholders (or enabling inflation attacks as described in F-05).

**Impact:** Low when `countNativeAsset == false`. Medium when `countNativeAsset == true` combined with `alwaysComputeTotalAssets == true`.

**Recommendation:** Consider tracking native deposits via an internal counter rather than relying on `address(this).balance`.

---

## SC-09: External call safety

### Analysis

### F-18: `processor()` return data from untrusted targets is unbounded

**Severity:** LOW
**OWASP category:** SC-09 (External call safety)
**Location:** `VaultLib.sol:451`

**Description:**
The processor function captures full return data from external calls:
```solidity
(bool success, bytes memory returnData_) = targets[i].call{value: values[i]}(data[i]);
```

A target contract could return extremely large data (return data bomb), consuming excessive gas for memory allocation and copying. Since targets are whitelisted via processor rules, this requires a whitelisted contract to be malicious.

**Impact:** Low due to whitelisting. A malicious whitelisted target could cause the processor transaction to run out of gas.

**Recommendation:** Consider using assembly to limit return data size, or document that whitelisted targets must be audited for return data behaviour.

### F-19: No fee-on-transfer token protection in deposits

**Severity:** LOW
**OWASP category:** SC-09 (External call safety / token integration)
**Location:** `BaseVault.sol:535-557`

**Description:**
The `_deposit()` function at BaseVault.sol:549:
```solidity
SafeERC20.safeTransferFrom(IERC20(asset_), caller, address(this), assets);
```

The vault credits `baseAssets` (derived from `assets`) to `totalAssets` and mints `shares` based on the full `assets` amount. If a fee-on-transfer token is added as an asset, the vault would receive less than `assets` tokens but credit the full amount, creating an accounting surplus that benefits future depositors at the expense of existing shareholders.

**Impact:** Depends on whether fee-on-transfer tokens are ever added. The ASSET_MANAGER_ROLE controls asset additions. Standard RWA tokens (the vault's intended use case) typically do not have transfer fees.

**Recommendation:** Either:
- Document that fee-on-transfer tokens are not supported and must never be added.
- Implement balance-before-after pattern in `_deposit()`.

### SafeERC20 usage

The vault correctly uses `SafeERC20.safeTransferFrom` and `SafeERC20.safeTransfer` for all token operations, handling tokens with missing return values (e.g., USDT).

### Return value checking

All low-level `.call` return values in the processor are checked:
```solidity
if (!success) {
    revert IVault.ProcessFailed(data[i], returnData_);
}
```

**Verdict:** PASS for return value checking.

---

## SC-10: Governance / semantic guard analysis

### Analysis

**Guard-state consistency matrix:**

| State variable | `deposit` | `mint` | `withdraw` | `redeem` | `depositAsset` | `withdrawAsset` | `processor` | `processAccounting` |
|---------------|-----------|--------|-----------|---------|---------------|----------------|------------|-------------------|
| `paused` check | Yes | Yes | Yes | Yes | Yes | Yes | No | No |
| `nonReentrant` | Yes | Yes | Yes | Yes | Yes | No | No | Yes |
| `asset.active` check | Yes | Yes | N/A | N/A | Yes | N/A | N/A | N/A |

### F-20: `processor()` is not gated by `paused` state

**Severity:** HIGH
**OWASP category:** SC-10 (Governance / semantic guard analysis)
**Location:** `BaseVault.sol:956-963`

**Description:**
All user-facing deposit/withdraw functions check `if (paused()) revert Paused()`. However, the `processor()` function, which can execute arbitrary whitelisted calls from the vault's address, does not check the paused state.

This means that even when the vault is paused (typically in response to an emergency), the PROCESSOR_ROLE can still execute external calls. These calls could include:
- Transferring tokens out of the vault via whitelisted ERC20 transfer rules.
- Interacting with external DeFi protocols.
- Moving assets between strategies.

**Pattern evidence:**
- `deposit()` checks `paused` before modifying state (YES)
- `mint()` checks `paused` before modifying state (YES)
- `withdraw()` checks `paused` before modifying state (YES)
- `redeem()` checks `paused` before modifying state (YES)
- `depositAsset()` checks `paused` before modifying state (YES)
- `withdrawAsset()` checks `paused` before modifying state (YES)
- `processor()` does NOT check `paused` (NO)

**Guard frequency:** 6/7 state-modifying functions (85.7%) check `paused`.

**Impact:** During a security incident where the vault is paused, the PROCESSOR_ROLE can still move assets. This could be:
- **Intentional:** to allow emergency asset recovery or strategy adjustments while paused.
- **Unintentional:** allowing a compromised PROCESSOR_ROLE to drain assets while the pause prevents users from withdrawing.

**Recommendation:**
- If intentional, document explicitly why `processor()` bypasses the pause check.
- If not intentional, add `if (paused()) revert Paused()` to `processor()`.
- Consider a separate "emergency processor" role that works when paused, and have the regular `processor()` respect the pause.

### F-21: `mintShares()` is not gated by `paused` state

**Severity:** MEDIUM
**OWASP category:** SC-10 (Governance / semantic guard analysis)
**Location:** `BaseVault.sol:970-976`

**Description:**
`mintShares()` can mint unlimited shares to any address when called by the hooks contract, regardless of whether the vault is paused. Combined with the hooks system, this means that even during a pause, the hooks contract can inflate the share supply.

**Impact:** If the hooks contract has its own logic that is supposed to be paused but is not, shares could be minted during emergency conditions.

**Recommendation:** Add a `paused` check to `mintShares()`, or document that the hooks contract is responsible for its own pause logic.

---

## Findings summary

| ID | Title | Severity | OWASP category | Location |
|----|-------|----------|---------------|----------|
| F-01 | Hooks receive control before state is fully committed | Low | SC-01 | BaseVault.sol:517 |
| F-02 | `processor()` lacks `nonReentrant` modifier | Medium | SC-01 | BaseVault.sol:956 |
| F-03 | `withdrawAsset()` lacks `nonReentrant` modifier | Low | SC-01 | BaseVault.sol:613 |
| F-04 | Complete dependence on unvalidated external rate provider | High | SC-02 | VaultLib.sol:221-247 |
| F-05 | `computeTotalAssets()` donation attack surface | Medium | SC-02 | VaultLib.sol:374-389 |
| F-06 | `processAccounting()` is callable by anyone | Low | SC-03 | BaseVault.sol:933 |
| F-07 | `mintShares()` relies on mutable hooks address for authorisation | Medium | SC-03 | BaseVault.sol:970-976 |
| F-08 | Provider change has no timelock -- enables instant rate manipulation | High | SC-03 | VaultLib.sol:350-357 |
| F-09 | Incorrect storage slot comment for ProcessorStorage | Informational | SC-05 | VaultLib.sol:62 |
| F-10 | Rounding mismatch in withdrawal totalAssets subtraction | Medium | SC-06 | BaseVault.sol:591 |
| F-11 | Fee Ceil rounding prevents dust bypass | N/A (Pass) | SC-06 | FeeMath.sol:32-34 |
| F-12 | Withdrawal fee can be set to 100% (confiscation) | Low | SC-06 | LinearWithdrawalFeeLib.sol:57 |
| F-13 | Asset deletion swap-and-pop logic is correct | N/A (Pass) | SC-07 | VaultLib.sol:184-211 |
| F-14 | `withdrawAsset()` accounting discrepancy | Medium | SC-07 | BaseVault.sol:640-664 |
| F-15 | Guard allow list linear search is unbounded | Low | SC-08 | Guard.sol:22-42 |
| F-16 | `computeTotalAssets()` iterates all assets | Low | SC-08 | VaultLib.sol:374-389 |
| F-17 | Native ETH force-feeding inflates total assets | Low | SC-08 | BaseVault.sol:1010-1012 |
| F-18 | Processor return data bomb from whitelisted targets | Low | SC-09 | VaultLib.sol:451 |
| F-19 | No fee-on-transfer token protection | Low | SC-09 | BaseVault.sol:549 |
| F-20 | `processor()` not gated by `paused` state | High | SC-10 | BaseVault.sol:956-963 |
| F-21 | `mintShares()` not gated by `paused` state | Medium | SC-10 | BaseVault.sol:970-976 |

### Severity distribution

| Severity | Count |
|----------|-------|
| High | 3 |
| Medium | 5 |
| Low | 8 |
| Informational | 1 |
| Pass (no issue) | 2 |

---

## Centralisation risks

The vault has significant centralisation through its role system. The following roles, if compromised, could cause material harm:

| Role | Impact if compromised |
|------|----------------------|
| `DEFAULT_ADMIN_ROLE` | Can grant any role -- complete control |
| `PROVIDER_MANAGER_ROLE` | Can set malicious rate provider -- manipulate all share pricing |
| `HOOKS_MANAGER_ROLE` | Can set malicious hooks -- mint unlimited shares via `mintShares()` |
| `PROCESSOR_ROLE` | Can execute whitelisted calls from vault -- move assets |
| `FEE_MANAGER_ROLE` | Can set 100% withdrawal fee -- lock user funds |
| `BUFFER_MANAGER_ROLE` | Can change buffer to malicious contract -- redirect withdrawals |

**Recommendation:** All critical roles should be behind timelocks (e.g., OpenZeppelin `TimelockController`, which is already imported) and ideally require multi-sig approval.

---

## Appendix: methodology

This audit applied the QuillAI/QuillShield methodology across all 10 OWASP Smart Contract Top 10 categories:

1. **Reentrancy (SC-01):** Built call graphs for all external interactions, verified CEI compliance and nonReentrant coverage.
2. **Oracle manipulation / flash loans (SC-02):** Mapped all oracle dependencies, assessed manipulation resistance and validation checks.
3. **Access control (SC-03):** Enumerated all roles and their capabilities, assessed privilege escalation paths.
4. **Signature replay (SC-04):** Verified EIP-712 domain separator, nonce management, and ecrecover safety.
5. **Proxy upgrade safety (SC-05):** Verified initialisation safety, storage layout, and constructor behaviour.
6. **Arithmetic / precision (SC-06):** Checked rounding directions, division-before-multiplication, ERC4626 inflation, unsafe casting, and fee calculations.
7. **State validation / invariants (SC-07):** Inferred state invariants and tested each function for violations.
8. **DoS / griefing (SC-08):** Checked for unbounded loops, force-feeding, timestamp griefing, and storage bloat.
9. **External call safety (SC-09):** Verified return value checking, SafeERC20 usage, fee-on-transfer compatibility, and return data bombs.
10. **Governance / semantic guards (SC-10):** Built guard-state consistency matrix and identified pattern violations.
