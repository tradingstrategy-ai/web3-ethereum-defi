# YieldNest ynRWAx vault security audit

**Methodology:** kadenzipfel/scv-scan 4-phase audit
**Scope:** Vault.sol, BaseVault.sol, VaultLib.sol, HooksLib.sol, LinearWithdrawalFeeLib.sol, Guard.sol, FeeMath.sol, LinearWithdrawalFee.sol, and all interfaces
**Solidity version:** ^0.8.24
**Date:** 2026-03-06

---

## Findings

### 1. Fee-on-transfer token deposits inflate total assets and dilute existing shareholders

**File:** `src/BaseVault.sol` L535-557
**Severity:** High

**Description:** The `_deposit` function increments `totalAssets` by the full `baseAssets` amount computed from the declared `assets` parameter, then calls `safeTransferFrom` to pull tokens from the depositor. If the deposited token charges a fee on transfer (e.g. USDT on some chains, deflationary tokens), the vault actually receives fewer tokens than `assets`, but `totalAssets` is inflated by the full undiscounted amount. This means the depositor receives shares valued at more than they actually contributed, directly diluting existing shareholders.

The vault supports multiple assets via `depositAsset()` and the asset list is admin-managed, so a fee-on-transfer token could be added either intentionally or by mistake.

**Code:**
```solidity
function _deposit(
    address asset_,
    address caller,
    address receiver,
    uint256 assets,
    uint256 shares,
    uint256 baseAssets
) internal virtual {
    if (!_getAssetStorage().assets[asset_].active) {
        revert AssetNotActive();
    }

    _addTotalAssets(baseAssets); // <-- incremented by full amount

    SafeERC20.safeTransferFrom(IERC20(asset_), caller, address(this), assets); // <-- actual received may be less
    _mint(receiver, shares);

    emit Deposit(caller, receiver, assets, shares);
    emit DepositAsset(caller, receiver, asset_, assets, baseAssets, shares);
}
```

**Recommendation:** Measure the actual balance difference before and after the transfer to determine the true received amount. Use this value for accounting and share calculation:
```solidity
uint256 balBefore = IERC20(asset_).balanceOf(address(this));
SafeERC20.safeTransferFrom(IERC20(asset_), caller, address(this), assets);
uint256 received = IERC20(asset_).balanceOf(address(this)) - balBefore;
// recalculate baseAssets and shares based on received
```
Alternatively, if fee-on-transfer tokens are explicitly unsupported, document this prominently and add a check or revert for tokens that deliver fewer tokens than requested.

---

### 2. Native ETH accounting manipulation via force-sent ETH

**File:** `src/library/VaultLib.sol` L374-389
**Severity:** Medium

**Description:** When `countNativeAsset` is enabled, `computeTotalAssets()` includes `address(this).balance` in the total base balance calculation. Anyone can force-send ETH to the vault contract -- for example via `selfdestruct` of another contract, or via coinbase reward targeting on certain chains -- bypassing the `receive()` function. This inflates `computeTotalAssets()` and therefore the share price.

An attacker could exploit this by:
1. Depositing into the vault at the current share price
2. Force-sending ETH to inflate `totalAssets`
3. Calling `processAccounting()` to update cached total
4. Redeeming shares at the now-inflated price

The cost of the attack is the force-sent ETH, and the profit depends on the attacker's share of the vault. This is a known griefing/manipulation vector for contracts that rely on `address(this).balance`.

**Code:**
```solidity
function computeTotalAssets() public view returns (uint256 totalBaseBalance) {
    IVault.VaultStorage storage vaultStorage = getVaultStorage();

    // Assumes native asset has same decimals as asset() (the base asset)
    totalBaseBalance = vaultStorage.countNativeAsset ? address(this).balance : 0;

    IVault.AssetStorage storage assetStorage = getAssetStorage();
    address[] memory assetList = assetStorage.list;
    uint256 assetListLength = assetList.length;

    for (uint256 i = 0; i < assetListLength; i++) {
        uint256 balance = IERC20(assetList[i]).balanceOf(address(this));
        if (balance == 0) continue;
        totalBaseBalance += convertAssetToBase(assetList[i], balance, Math.Rounding.Floor);
    }
}
```

**Recommendation:** Track incoming ETH explicitly using a storage counter incremented in the `receive()` function, rather than relying on `address(this).balance`. Alternatively, use WETH (wrapped ETH) instead of counting raw native ETH balance.

---

### 3. `processAccounting()` is permissionless and enables share price manipulation via sandwiching

**File:** `src/BaseVault.sol` L933-935, `src/library/VaultLib.sol` L394-432
**Severity:** Medium

**Description:** `processAccounting()` is callable by anyone (no access control modifier). It recomputes and updates the cached `totalAssets` value, which directly affects share pricing for deposits and withdrawals. When the vault is in cached mode (`alwaysComputeTotalAssets = false`), the cached total can become stale. An attacker can sandwich a `processAccounting()` call:

1. If the vault's actual assets have appreciated since the last accounting update, `totalAssets` is stale-low
2. Attacker deposits at the stale-low share price (getting more shares than they should)
3. Attacker or anyone calls `processAccounting()`, updating `totalAssets` to the higher current value
4. Attacker redeems at the now-higher share price for a profit

This is exacerbated because `processAccounting()` also triggers hooks (`beforeProcessAccounting` / `afterProcessAccounting`) which could have side effects.

**Code:**
```solidity
function processAccounting() public virtual nonReentrant {
    _processAccounting();
}
```

**Recommendation:** Restrict `processAccounting()` to a dedicated role (e.g. `ACCOUNTING_ROLE`) or at least add a cooldown/frequency limit. Alternatively, consider always computing total assets in real-time for deposit/withdrawal operations even when using the cached mode for read-only queries.

---

### 4. Guard only validates ADDRESS parameters; UINT256 parameters are silently skipped

**File:** `src/module/Guard.sol` L9-29
**Severity:** Medium

**Description:** The `validateCall` function iterates over `paramRules` but only validates parameters of type `ADDRESS`. When a `paramRules[i].paramType` is `UINT256`, the loop simply moves to the next iteration without performing any check. This means the processor guard cannot enforce constraints on numeric parameters (e.g. maximum amounts, expected values), which could allow the PROCESSOR_ROLE to pass arbitrary values for unvalidated numeric arguments.

Additionally, the parameter extraction at L24 uses fixed offsets (`4 + i * 32`) which only works for simple ABI-encoded types. For functions with dynamic types (arrays, bytes, strings), the calldata layout uses offsets and the actual data is elsewhere, meaning the guard would read the offset pointer rather than the actual parameter value.

**Code:**
```solidity
function validateCall(address target, uint256 value, bytes calldata data) internal view {
    bytes4 funcSig = bytes4(data[:4]);

    IVault.FunctionRule storage rule = VaultLib.getProcessorStorage().rules[target][funcSig];

    if (!rule.isActive) revert RuleNotActive(target, funcSig);

    IValidator validator = rule.validator;
    if (address(validator) != address(0)) {
        validator.validate(target, value, data);
        return;
    }

    for (uint256 i = 0; i < rule.paramRules.length; i++) {
        if (rule.paramRules[i].paramType == IVault.ParamType.ADDRESS) {
            address addressValue = abi.decode(data[4 + i * 32:], (address));
            _validateAddress(addressValue, rule.paramRules[i]);
            continue;
        }
        // UINT256 parameters fall through with no validation
    }
}
```

**Recommendation:** Add validation logic for `UINT256` parameters (e.g. min/max range checks) or, if UINT256 validation is not needed, document this explicitly. For functions with dynamic types in the ABI, use the `IValidator` pattern rather than the fixed-offset parameter extraction. Consider adding a `value` check as well, since the ETH value sent with the call is also not validated by the guard.

---

### 5. Processor `call{value}` does not validate ETH value or target code existence

**File:** `src/library/VaultLib.sol` L441-458
**Severity:** Medium

**Description:** The `processor()` function makes low-level `.call{value}` to targets but does not verify that the target address has deployed code. Per EVM semantics, a `.call()` to an address with no code succeeds silently (returns `success = true` with empty return data). If a target address is whitelisted in the processor rules but later self-destructs or is misconfigured, the processor would send ETH to a codeless address and report success, permanently losing the funds.

Additionally, the `values[i]` ETH amount is not validated by the Guard -- only the function signature and address parameters are checked. The PROCESSOR_ROLE can send arbitrary ETH amounts with each call.

**Code:**
```solidity
function processor(address[] calldata targets, uint256[] memory values, bytes[] calldata data)
    public
    returns (bytes[] memory returnData)
{
    uint256 targetsLength = targets.length;
    returnData = new bytes[](targetsLength);

    for (uint256 i = 0; i < targetsLength; i++) {
        Guard.validateCall(targets[i], values[i], data[i]);

        (bool success, bytes memory returnData_) = targets[i].call{value: values[i]}(data[i]);
        if (!success) {
            revert IVault.ProcessFailed(data[i], returnData_);
        }
        returnData[i] = returnData_;
    }
    emit IVault.ProcessSuccess(targets, values, returnData);
}
```

**Recommendation:** Add a check that the target address has deployed code before making the call: `require(targets[i].code.length > 0, "no code at target")`. Consider also adding ETH value validation within the Guard or FunctionRule system.

---

### 6. Unbounded return data from processor calls may cause out-of-gas

**File:** `src/library/VaultLib.sol` L451
**Severity:** Low

**Description:** The `processor()` function captures return data from each `.call{value}` into `bytes memory returnData_`. Solidity automatically copies all return data into memory. If a whitelisted target returns an unexpectedly large amount of data, the memory expansion cost grows quadratically, potentially causing an out-of-gas revert. While the targets are whitelisted by the processor rule system, the return data size is unbounded and not controlled.

**Code:**
```solidity
(bool success, bytes memory returnData_) = targets[i].call{value: values[i]}(data[i]);
```

**Recommendation:** If the return data is not needed, use assembly to bound the `returndatacopy` to a maximum size. Alternatively, use `ExcessivelySafeCall` for the low-level calls to prevent memory expansion attacks.

---

### 7. `_feeOnRaw` and `_feeOnTotal` use `public` visibility with internal naming convention

**File:** `src/BaseVault.sol` L1021-1029, `src/Vault.sol` L68-82
**Severity:** Informational

**Description:** The functions `_feeOnRaw` and `_feeOnTotal` use underscore-prefixed names (the Solidity convention for internal/private functions) but are declared `public`. This is required by the `IVault` interface which declares these as `external` functions. While functionally correct, this creates confusion for auditors and developers who expect underscore-prefixed functions to be internal. The public visibility exposes fee calculation internals that could be used by attackers to optimise their withdrawal timing.

**Code:**
```solidity
// In BaseVault.sol
function _feeOnRaw(uint256 amount, address user) public view virtual override returns (uint256);
function _feeOnTotal(uint256 amount, address user) public view virtual override returns (uint256);

// In Vault.sol
function _feeOnRaw(uint256 amount, address user) public view override returns (uint256) {
    return __feeOnRaw(amount, user);
}
```

**Recommendation:** Rename the public functions to `feeOnRaw` and `feeOnTotal` (without the underscore prefix) and update the `IVault` interface accordingly. Keep internal helper functions with the underscore prefix.

---

### 8. `processAccounting()` triggers hooks callable by any external party

**File:** `src/library/VaultLib.sol` L394-432
**Severity:** Informational

**Description:** Since `processAccounting()` is permissionless, any external caller can trigger the `beforeProcessAccounting` and `afterProcessAccounting` hooks. While the hooks contract is admin-configured and validated, this allows untrusted callers to invoke potentially gas-expensive hook logic at will, or to trigger hook side-effects at times not anticipated by the vault operator. This could be used for griefing if the hooks contract performs expensive operations.

**Code:**
```solidity
function processAccounting() public {
    IVault _vault = IVault(address(this));
    // ...
    HooksLib.beforeProcessAccounting(hooks_, ...);
    // ...
    HooksLib.afterProcessAccounting(hooks_, ...);
}
```

**Recommendation:** Consider gating `processAccounting()` behind a role or adding a minimum time interval between calls to prevent griefing.

---

## Vulnerability types reviewed and not found

The following vulnerability types from the scv-scan cheatsheet were reviewed and no instances were found in the codebase:

| Vulnerability type | Notes |
|---|---|
| Reentrancy | All entry points use `nonReentrant`; `withdrawAsset` is role-gated. State updates follow checks-effects-interactions. |
| Overflow/underflow | Solidity ^0.8.24 with checked arithmetic. Assembly blocks only used for storage slot assignment (no arithmetic). |
| Delegatecall to untrusted callee | No `delegatecall` in user-facing code. Library calls are compile-time linked. |
| Authorization via tx.origin | Not used anywhere. |
| Signature malleability / ecrecover | ERC20Permit uses OpenZeppelin's ECDSA which handles malleability. No custom signature logic. |
| Hash collision (abi.encodePacked) | Not used in the vault contracts. |
| Uninitialized storage pointer | Solidity ^0.8.24 prevents this. |
| Shadowing state variables | No variable shadowing detected across the inheritance chain. |
| Incorrect constructor | Uses `_disableInitializers()` in constructor and `initializer` modifier correctly. |
| Incorrect inheritance order | `BaseVault is IVault, ERC20PermitUpgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable` follows the correct most-base-to-most-derived order. |
| Arbitrary storage location | Assembly blocks use hardcoded keccak256 slot values; no user-controlled slots. |
| Asserting contract from code size | Not used. |
| msg.value reuse in loops | `msg.value` only used in `receive()` for event emission, not in loops. |
| Weak randomness sources | No randomness logic. |
| Unencrypted private data | No secrets stored on-chain. |
| Deprecated functions | None used. |
| Unsupported opcodes | `pragma ^0.8.24` may emit PUSH0 but this is standard for mainnet post-Shanghai. |
| Timestamp dependence | `block.timestamp` only used in event emission in `processAccounting`, not for logic. |
| Missing signature replay protection | ERC20Permit uses EIP-712 with domain separator and nonces. |
| Insufficient gas griefing | No meta-transaction or relayer patterns. |
| Off-by-one errors | Loop boundaries are correct (`i < length`). |
| Lack of precision | Uses OpenZeppelin `Math.mulDiv` with explicit rounding throughout. |
| Assert violation | No `assert()` statements in the codebase. |
| Unused variables | No significant unused variables detected. |

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 1 |
| Medium | 3 |
| Low | 1 |
| Informational | 2 |

### Key findings

1. **High -- Fee-on-transfer token accounting mismatch** (Finding 1): The vault does not measure actual received token amounts, allowing fee-on-transfer tokens to inflate the total assets and dilute existing shareholders. This is the most impactful finding as it can lead to direct value extraction.

2. **Medium -- Native ETH manipulation** (Finding 2): Force-sent ETH inflates `computeTotalAssets()` when `countNativeAsset` is enabled, enabling share price manipulation.

3. **Medium -- Permissionless processAccounting** (Finding 3): Anyone can trigger accounting updates, enabling sandwich attacks around share price changes in cached mode.

4. **Medium -- Guard skips UINT256 validation** (Finding 4): The processor guard silently ignores numeric parameters and does not handle dynamic ABI-encoded types correctly.

5. **Medium -- No target code existence check in processor** (Finding 5): Low-level calls to addresses without code succeed silently, potentially losing ETH.
