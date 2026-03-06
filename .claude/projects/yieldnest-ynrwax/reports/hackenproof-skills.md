# YieldNest ynRWAx vault security audit report

**Skill used**: HackenProof Triage Marketplace
**Date**: 2026-03-06
**Auditor**: Claude Opus 4.6 (AI-assisted)
**Target**: YieldNest ynRWAx Vault (Solidity 0.8.24)
**Deployment**: TransparentUpgradeableProxy at `0x01Ba69727E2860b37bc1a2bd56999c1aFb4C15D8` (Ethereum mainnet)
**Implementation**: `0xb46D7014C1A29b6A82D8eCDE5aD29d5B09aC7A1b`

## Scope verification

The audit covers the following in-scope files:

| File | Path | Lines |
|------|------|-------|
| Vault.sol | `src/Vault.sol` | 109 |
| BaseVault.sol | `src/src/BaseVault.sol` | 1030 |
| VaultLib.sol | `src/src/library/VaultLib.sol` | 459 |
| HooksLib.sol | `src/src/library/HooksLib.sol` | 178 |
| LinearWithdrawalFeeLib.sol | `src/src/library/LinearWithdrawalFeeLib.sol` | 96 |
| Guard.sol | `src/src/module/Guard.sol` | 46 |
| FeeMath.sol | `src/src/module/FeeMath.sol` | 41 |
| LinearWithdrawalFee.sol | `src/src/module/LinearWithdrawalFee.sol` | 83 |
| IVault.sol | `src/src/interface/IVault.sol` | 182 |
| IHooks.sol | `src/src/interface/IHooks.sol` | 182 |
| IProvider.sol | `src/src/interface/IProvider.sol` | 6 |
| IValidator.sol | `src/src/interface/IValidator.sol` | 12 |
| IStrategy.sol | `src/src/interface/IStrategy.sol` | 21 |
| Common.sol | `src/src/Common.sol` | 30 |

OpenZeppelin library contracts (under `src/lib/`) are out of scope as they are well-audited third-party dependencies.

## Architecture summary

The vault follows a modular design pattern:

- **Vault.sol**: Top-level contract combining BaseVault and LinearWithdrawalFee
- **BaseVault.sol**: ERC4626-compatible multi-asset vault with role-based access control, hooks, and processor
- **VaultLib.sol**: Core library handling storage, accounting, asset management, and processor execution
- **Guard.sol**: Processor call validation with rule-based allow-listing
- **FeeMath.sol / LinearWithdrawalFeeLib.sol**: Withdrawal fee calculation with per-user overrides
- **HooksLib.sol**: Before/after hook dispatch for deposit, mint, redeem, withdraw, and accounting operations

The vault uses ERC7201 namespaced storage for proxy safety and OpenZeppelin's `TransparentUpgradeableProxy` pattern.

---

## Findings

### Finding 1: Public visibility on internal fee functions exposes vault to direct external calls

**Severity**: Low
**File**: `src/src/BaseVault.sol`, lines 1021-1029; `src/Vault.sol`, lines 68-82
**Classification**: fv-sol-4 (Bad access control)

**Description**:

The functions `_feeOnRaw` and `_feeOnTotal` in `BaseVault.sol` are declared as `public view virtual` despite using the conventional `_` prefix that denotes internal/private functions. They are then exposed in the `IVault` interface as external functions:

```solidity
// IVault.sol
function _feeOnRaw(uint256 amount, address user) external view returns (uint256);
function _feeOnTotal(uint256 amount, address user) external view returns (uint256);
```

```solidity
// BaseVault.sol
function _feeOnRaw(uint256 amount, address user) public view virtual override returns (uint256);
function _feeOnTotal(uint256 amount, address user) public view virtual override returns (uint256);
```

**Impact**: These are view functions, so no state mutation is possible. However, the naming convention violation (`_` prefix on public functions) breaks Solidity conventions and could mislead developers or integrating protocols into treating these as internal-only. The information leakage (fee structure per user) is intentional per the interface design, so the impact is informational.

**PoC**: Any external caller can invoke `vault._feeOnRaw(amount, userAddress)` to query the exact fee any user would pay, which is an expected public view function. No exploit path exists.

**Recommendation**: Rename to `feeOnRaw` and `feeOnTotal` (remove underscore prefix) in the interface, BaseVault, and Vault to align with Solidity naming conventions, or change visibility to `internal` and provide separate `external` getters.

---

### Finding 2: Processor storage slot comment incorrectly references vault namespace

**Severity**: None (Informational)
**File**: `src/src/library/VaultLib.sol`, lines 60-65
**Classification**: Documentation error

**Description**:

The `getProcessorStorage()` function has a comment stating the slot is derived from `keccak256("yieldnest.storage.vault")`, which is the same comment used for `getVaultStorage()`:

```solidity
function getProcessorStorage() public pure returns (IVault.ProcessorStorage storage $) {
    assembly {
        // keccak256("yieldnest.storage.vault")  // <-- INCORRECT COMMENT
        $.slot := 0x52bb806a772c899365572e319d3d6f49ed2259348d19ab0da8abccd4bd46abb5
    }
}
```

Verified computations:
- `keccak256("yieldnest.storage.vault")` = `0x22cdba...` (used correctly in `getVaultStorage()`)
- `keccak256("yieldnest.storage.processor")` = `0x81da35...` (does NOT match the hardcoded slot)
- The hardcoded processor slot `0x52bb80...` is unique and does not collide with any other slot

**Impact**: The hardcoded slot values are distinct and correct in practice, so there is no storage collision. The comment is simply wrong about the pre-image. The actual pre-image used to derive the processor slot is unclear from the source code.

**PoC**: Not applicable -- the slots do not collide. This is a documentation-only issue.

**Recommendation**: Correct the comment to reference the actual pre-image string used to derive `0x52bb806a...`, or document that it was derived from a different namespace string.

---

### Finding 3: Guard only validates ADDRESS parameter types, silently skips UINT256 validation

**Severity**: Medium
**File**: `src/src/module/Guard.sol`, lines 22-29
**Classification**: fv-sol-4 (Bad access control)

**Description**:

The `validateCall` function in `Guard.sol` iterates through parameter rules but only validates `ParamType.ADDRESS`. When a rule specifies `ParamType.UINT256`, the loop `continue`s without performing any validation:

```solidity
for (uint256 i = 0; i < rule.paramRules.length; i++) {
    if (rule.paramRules[i].paramType == IVault.ParamType.ADDRESS) {
        address addressValue = abi.decode(data[4 + i * 32:], (address));
        _validateAddress(addressValue, rule.paramRules[i]);
        continue;
    }
    // UINT256 param type silently passes with no validation
}
```

If a processor manager configures a `FunctionRule` with `ParamType.UINT256` entries (e.g. to restrict amounts), those rules will have no effect. The processor (PROCESSOR_ROLE holder) could pass any uint256 value and the Guard would not enforce it.

**Impact**: If the PROCESSOR_MANAGER_ROLE configures uint256 parameter rules expecting they will be enforced, they will not be. This could allow a PROCESSOR_ROLE holder to execute calls with unrestricted parameter values. However, the impact is constrained because:
1. This requires the PROCESSOR_MANAGER_ROLE to have mistakenly configured UINT256 rules believing they would be enforced
2. The PROCESSOR_ROLE is already a privileged role
3. A custom `IValidator` can be used instead for complex validation

This is a defence-in-depth gap rather than a direct exploit path against unprivileged users.

**PoC**:

```solidity
// Processor manager sets a rule with UINT256 param
IVault.ParamRule[] memory paramRules = new IVault.ParamRule[](1);
paramRules[0] = IVault.ParamRule({
    paramType: IVault.ParamType.UINT256,
    isArray: false,
    allowList: new address[](0)
});
vault.setProcessorRule(target, funcSig, IVault.FunctionRule({
    isActive: true,
    paramRules: paramRules,
    validator: IValidator(address(0))
}));

// Processor calls with any uint256 value -- Guard does NOT validate it
vault.processor(targets, values, data); // passes regardless of uint256 param value
```

**Recommendation**: Either implement UINT256 validation (e.g. min/max bounds, or allowlist of values) in the Guard, or explicitly document that UINT256 parameter rules are not supported and revert if a PROCESSOR_MANAGER_ROLE attempts to configure one. Also consider removing `ParamType.UINT256` from the enum if it is intentionally unsupported.

---

### Finding 4: `receive()` accepts ETH without bookkeeping update, creating accounting drift

**Severity**: Medium
**File**: `src/src/BaseVault.sol`, lines 1010-1012
**Classification**: fv-sol-2 (Precision errors / accounting)

**Description**:

The vault has a `receive()` function that accepts arbitrary ETH deposits:

```solidity
receive() external payable {
    emit NativeDeposit(msg.value);
}
```

When `countNativeAsset` is `true`, `computeTotalAssets()` includes `address(this).balance` in the total. However, ETH received via `receive()` does not trigger `_addTotalAssets()`, so the cached `totalAssets` in `VaultStorage` is not updated.

This means:
1. If `alwaysComputeTotalAssets` is `false` (cached mode), the ETH is invisible to `totalBaseAssets()` until `processAccounting()` is called
2. No shares are minted for the depositor, so the ETH effectively accrues to all existing share holders
3. Anyone can send ETH to inflate the vault's total assets when accounting is next processed

**Impact**: The drift between cached and real total assets causes share price inaccuracy between `processAccounting()` calls. When `countNativeAsset` is true and `alwaysComputeTotalAssets` is false, an attacker could send ETH to the vault to manipulate the exchange rate at the next `processAccounting()` call, potentially benefiting from front-running the accounting update. The magnitude depends on the vault's TVL relative to the donated ETH.

When `countNativeAsset` is false, the ETH is simply locked with no accounting impact (a different problem -- the ETH becomes unrecoverable unless the processor can send it out).

**PoC**:

```solidity
// Vault with countNativeAsset=true, alwaysComputeTotalAssets=false
// Attacker sends 10 ETH directly
(bool ok,) = address(vault).call{value: 10 ether}("");

// totalBaseAssets() still returns old cached value (no change)
uint256 cached = vault.totalBaseAssets(); // unchanged

// After processAccounting() is called, totalBaseAssets() jumps up
vault.processAccounting();
uint256 updated = vault.totalBaseAssets(); // now includes 10 ETH
// Share holders who deposited before get a windfall;
// attacker's ETH is distributed pro-rata with no shares minted
```

**Recommendation**: Either (a) remove the `receive()` function and require ETH deposits to go through a controlled pathway that updates accounting, (b) call `_addTotalAssets()` in the receive function (but this creates a share dilution issue since no shares are minted), or (c) document this as intentional donation-to-vault behaviour and ensure `processAccounting()` is called frequently enough to minimise drift.

---

### Finding 5: `processAccounting()` is callable by anyone without access control

**Severity**: Low
**File**: `src/src/BaseVault.sol`, line 933
**Classification**: fv-sol-4 (Bad access control)

**Description**:

The `processAccounting()` function is `public` with no role restriction:

```solidity
function processAccounting() public virtual nonReentrant {
    _processAccounting();
}
```

Any external caller can trigger an accounting update at any time. This recalculates `totalAssets` based on current balances and provider rates, which directly affects the share exchange rate.

**Impact**: An attacker could call `processAccounting()` at a strategically chosen moment to front-run or sandwich deposits/withdrawals. For example:
1. If the provider rate has changed unfavourably, an attacker could call `processAccounting()` before withdrawing to lock in a higher share price
2. Combined with Finding 4 (ETH donation), an attacker could donate ETH then immediately call `processAccounting()` to inflate the rate before their own deposit resolves

This is partially mitigated by the `nonReentrant` guard, but the lack of any access control means anyone can trigger accounting at a time that benefits them. The practical impact depends on how volatile the provider rates are and the vault's TVL.

**Recommendation**: Add access control (e.g. `onlyRole(PROCESSOR_ROLE)` or a new `ACCOUNTING_ROLE`) to `processAccounting()`, or add a minimum time interval between calls to prevent manipulation.

---

### Finding 6: Guard parameter decoding uses fixed 32-byte offsets, incorrect for dynamic types and arrays

**Severity**: Medium
**File**: `src/src/module/Guard.sol`, lines 24-25
**Classification**: fv-sol-3 (Arithmetic errors / calldata pitfalls)

**Description**:

The Guard decodes parameters using fixed 32-byte offsets from the calldata:

```solidity
address addressValue = abi.decode(data[4 + i * 32:], (address));
```

This assumes all parameters are statically encoded 32-byte values in sequential positions. This is incorrect for:
1. Functions with dynamic types (bytes, string, arrays) -- the ABI encoding places an offset pointer at position `i*32`, not the actual value
2. The `isArray` field in `ParamRule` exists but is never checked -- array parameters would be decoded as the offset pointer, not actual values

**Impact**: If a processor rule is configured for a function that has dynamic-type parameters preceding or interspersed with address parameters, the Guard will decode incorrect values. For example, for a function `foo(bytes, address)`, the address at position 1 would be decoded from offset `4 + 32 = 36`, but position 36 actually contains the ABI-encoded offset pointer for the `bytes` parameter, not the address.

A malicious PROCESSOR_ROLE holder could craft calldata for functions with mixed static/dynamic parameters such that the Guard validates the wrong bytes as addresses, bypassing the allowlist check.

**PoC**:

```solidity
// Suppose a function: target.doSomething(bytes memory data, address recipient)
// ABI encoding layout:
//   [0x00..0x04]: function selector
//   [0x04..0x24]: offset to `data` (e.g. 0x40)
//   [0x24..0x44]: recipient address  <-- Guard reads position i=1 here, correct
//   [0x44..0x64]: length of `data`
//   [0x64..]:     actual bytes data

// But for: target.doSomething(address sender, bytes memory data, address recipient)
// ABI encoding layout:
//   [0x04..0x24]: sender address      <-- Guard reads i=0, correct
//   [0x24..0x44]: offset to `data`    <-- Guard reads i=1 as address, WRONG
//   [0x44..0x64]: recipient address   <-- Guard reads i=2, but this is the actual recipient
// The Guard would validate the offset pointer (not a real address) against the allowlist for param 1
```

**Recommendation**: Implement proper ABI-aware parameter decoding that handles dynamic types and arrays, or restrict processor rules to functions with only static parameter types, with a validation check at rule-setting time that rejects rules for functions with dynamic parameters when using the built-in Guard (as opposed to a custom `IValidator`).

---

### Finding 7: `withdrawAsset` does not enforce withdrawal fees

**Severity**: Medium
**File**: `src/src/BaseVault.sol`, lines 613-629
**Classification**: fv-sol-4 (Bad access control) / fv-sol-2 (Precision errors)

**Description**:

The `withdrawAsset` function, restricted to `ASSET_WITHDRAWER_ROLE`, converts assets to shares without applying any withdrawal fee:

```solidity
function withdrawAsset(address asset_, uint256 assets, address receiver, address owner)
    public
    virtual
    onlyRole(ASSET_WITHDRAWER_ROLE)
    returns (uint256 shares)
{
    if (paused()) {
        revert Paused();
    }
    (shares,) = _convertToShares(asset_, assets, Math.Rounding.Ceil);
    if (assets > IERC20(asset_).balanceOf(address(this)) || balanceOf(owner) < shares) {
        revert ExceededMaxWithdraw(owner, assets, IERC20(asset_).balanceOf(address(this)));
    }
    _withdrawAsset(asset_, _msgSender(), receiver, owner, assets, shares);
}
```

Compare this with `withdraw()`, which applies `_feeOnRaw` via `previewWithdraw`. The `withdrawAsset` path bypasses fees entirely.

**Impact**: The `ASSET_WITHDRAWER_ROLE` holder can withdraw any supported asset on behalf of any owner without paying withdrawal fees. If this role is granted to an automated system or a semi-trusted party, they could perform withdrawals that avoid fees that would normally apply. However, this is a privileged function requiring `ASSET_WITHDRAWER_ROLE`, and the fee exemption may be by design for specific operational withdrawals. The severity depends on the trust model for this role.

**PoC**:

```solidity
// ASSET_WITHDRAWER_ROLE holder calls withdrawAsset
// No fee is charged, unlike standard withdraw()
vault.withdrawAsset(assetAddress, 1000e18, receiver, owner);
// Owner's shares are burned at the raw rate, no fee component
```

**Recommendation**: If fee-free withdrawals for this role are intentional, add explicit documentation. If fees should apply, incorporate the fee calculation similar to how `withdraw()` does it. Consider adding a `withdrawAsset` variant that includes fee logic.

---

### Finding 8: `withdrawAsset` does not trigger hooks

**Severity**: Low
**File**: `src/src/BaseVault.sol`, lines 613-664
**Classification**: fv-sol-4 (Bad access control)

**Description**:

The `withdrawAsset` function does not call any before/after hooks, unlike `withdraw()` and `redeem()` which call `beforeWithdraw`/`afterWithdraw` and `beforeRedeem`/`afterRedeem` respectively.

```solidity
function withdrawAsset(address asset_, uint256 assets, address receiver, address owner)
    public
    virtual
    onlyRole(ASSET_WITHDRAWER_ROLE)
    returns (uint256 shares)
{
    // ... no hooks called
    _withdrawAsset(asset_, _msgSender(), receiver, owner, assets, shares);
}
```

**Impact**: If hooks are used for critical operations (e.g. compliance checks, KYC/AML gatekeeping, accounting adjustments, or access control enforcement via `beforeWithdraw`), the `withdrawAsset` path bypasses all of them. A hooks-based whitelist could be circumvented by the ASSET_WITHDRAWER_ROLE holder.

**PoC**: The ASSET_WITHDRAWER_ROLE holder calls `withdrawAsset()` and hooks configured via `setHooks()` are not invoked.

**Recommendation**: Add hook calls to `withdrawAsset`, or document explicitly that this privileged withdrawal path intentionally bypasses hooks.

---

### Finding 9: Deposit-withdraw sandwich on `processAccounting()` due to virtual offset +1 in share calculation

**Severity**: Low
**File**: `src/src/library/VaultLib.sol`, lines 285-313
**Classification**: fv-sol-2 (Precision errors / ERC4626 rounding)

**Description**:

The share/asset conversion functions use a virtual offset of `+1`:

```solidity
baseAssets = shares.mulDiv(totalAssets + 1, totalSupply + 1, rounding);
// and
shares = baseAssets.mulDiv(totalSupply + 1, totalAssets + 1, rounding);
```

This is a standard ERC4626 inflation attack mitigation. However, the offset is minimal (`+1` instead of the commonly recommended `10^decimals` virtual shares). For a vault with 18-decimal tokens and substantial TVL, this provides limited protection against first-depositor inflation attacks.

**Impact**: With only +1 virtual offset, the first depositor can still execute a donation-based inflation attack, albeit at higher cost than with zero offset. The attacker would:
1. Deposit 1 wei to get 1 share
2. Donate a large amount directly to inflate `totalAssets`
3. Subsequent depositors receive fewer shares due to inflated rate
4. The attacker redeems to capture most of the donated + subsequent deposits

The +1 offset means the attacker must donate at least `totalAssets` to halve the shares issued, which with typical deployment patterns (seeded vaults) makes this impractical but not impossible for low-TVL periods.

**PoC**: Standard ERC4626 inflation attack with +1 offset. The vault's initialisation sets `paused=true`, which helps prevent this during deployment as the admin can seed the vault before unpausing. This reduces the practical risk.

**Recommendation**: Consider using a larger virtual offset (e.g. `10^decimals`) as recommended by OpenZeppelin ERC4626 implementations, or ensure the vault is always seeded with a non-trivial amount before unpausing.

---

### Finding 10: Missing `_disableInitializers()` does not use ERC7201 pattern for constructor

**Severity**: None (Informational)
**File**: `src/src/BaseVault.sol` (constructor inherited in Vault.sol lines 1003-1005)

**Description**:

The `Vault.sol` constructor correctly calls `_disableInitializers()`:

```solidity
constructor() {
    _disableInitializers();
}
```

This properly prevents the implementation contract from being initialised directly. This is the correct pattern for TransparentUpgradeableProxy.

**Impact**: No issue. This is a positive finding confirming correct proxy safety.

---

### Finding 11: Storage slots use simple keccak256 instead of ERC7201 formula

**Severity**: None (Informational)
**File**: `src/src/library/VaultLib.sol`, lines 38-87
**Classification**: fv-sol-7 (Proxy insecurities)

**Description**:

The ERC20 storage correctly uses the ERC7201 formula:
```
keccak256(abi.encode(uint256(keccak256("openzeppelin.storage.ERC20")) - 1)) & ~bytes32(uint256(0xff))
```

However, the YieldNest custom storage slots (vault, asset, fees, hooks) use simple `keccak256("yieldnest.storage.xxx")` without the ERC7201 transformation. Verified:
- `keccak256("yieldnest.storage.vault")` = `0x22cdba...` (matches the code)
- `keccak256("yieldnest.storage.asset")` = `0x2dd192...` (matches the code)
- `keccak256("yieldnest.storage.fees")` = `0xde9246...` (matches the code)
- `keccak256("yieldnest.storage.hooks")` = `0x888cd7...` (matches the code)

The processor storage slot (`0x52bb80...`) does not match `keccak256("yieldnest.storage.processor")` (`0x81da35...`) or `keccak256("yieldnest.storage.vault")` (`0x22cdba...`), so the comment is wrong but the slot is unique.

**Impact**: The simple keccak256 approach still provides collision resistance. The ERC7201 formula adds a masking step to guarantee the last byte is zero, which provides additional safety margins. Since all slot values are hardcoded and verified to be unique, there is no practical collision risk. This is a deviation from the ERC7201 standard but not a vulnerability.

**Recommendation**: Update the comment on `getProcessorStorage()` to reference the correct pre-image string.

---

### Finding 12: `hasAsset` can return false positive for the zero-index asset

**Severity**: Low
**File**: `src/src/BaseVault.sol`, lines 418-422
**Classification**: fv-sol-2 (Precision errors)

**Description**:

The `hasAsset` function checks if an asset exists by looking up its index and verifying the list:

```solidity
function hasAsset(address asset_) public view virtual returns (bool) {
    AssetStorage storage assetStorage = _getAssetStorage();
    AssetParams memory assetParams = assetStorage.assets[asset_];
    return assetStorage.list[assetParams.index] == asset_;
}
```

For any address that has never been added as an asset, `assetStorage.assets[unknownAddr]` returns a default-initialised `AssetParams` with `index = 0`. The function then checks `assetStorage.list[0] == unknownAddr`. If `unknownAddr` happens to equal the base asset (at index 0), this would correctly return true. For any other unknown address, `assetStorage.list[0]` would not match, so it returns false correctly.

However, this approach has an edge case: if the asset list is empty (no assets added yet), `assetStorage.list[0]` would revert with an array out-of-bounds error.

**Impact**: Before any asset is added, calling `hasAsset()` with any address would revert. This is a denial-of-service on a view function during the vault's initialisation phase (before the first `addAsset` call). The practical impact is very low because the vault starts paused and assets should be configured before unpausing.

**PoC**: Deploy vault, do not add any assets, call `hasAsset(anyAddr)` -- reverts with array out-of-bounds.

**Recommendation**: Add a length check: `if (assetStorage.list.length == 0) return false;`

---

### Finding 13: `addAsset` duplicate check has false-negative for the zero-index case

**Severity**: Low
**File**: `src/src/library/VaultLib.sol`, lines 150-156
**Classification**: fv-sol-2 (Precision errors)

**Description**:

The duplicate asset check in `addAsset` uses two conditions:

```solidity
// Check if trying to add the Base Asset again
if (index > 0 && asset_ == assetStorage.list[0]) {
    revert IVault.DuplicateAsset(asset_);
}

if (index > 0 && assetStorage.assets[asset_].index != 0) {
    revert IVault.DuplicateAsset(asset_);
}
```

The first check prevents re-adding the base asset (index 0). The second check catches other duplicates by verifying the asset's stored index is not zero. However, if a previously deleted asset (whose mapping entry was deleted via `delete assetStorage.assets[asset_]`) is re-added, the `index` field will be 0 (default), and the check `assetStorage.assets[asset_].index != 0` will be false, so it passes. This is actually correct behaviour as it allows re-adding a previously deleted asset.

**Impact**: No actual vulnerability. The duplicate detection is correct for the intended use case. A deleted asset can be re-added, which is expected behaviour.

---

## Summary of findings

| # | Title | Severity | Status |
|---|-------|----------|--------|
| 1 | Public visibility on internal fee functions | Low | Confirmed |
| 2 | Processor storage slot comment incorrect | None | Confirmed |
| 3 | Guard silently skips UINT256 parameter validation | Medium | Confirmed |
| 4 | `receive()` accepts ETH without bookkeeping update | Medium | Confirmed |
| 5 | `processAccounting()` callable by anyone | Low | Confirmed |
| 6 | Guard parameter decoding incorrect for dynamic types | Medium | Confirmed |
| 7 | `withdrawAsset` bypasses withdrawal fees | Medium | Confirmed |
| 8 | `withdrawAsset` does not trigger hooks | Low | Confirmed |
| 9 | Minimal +1 virtual offset in share calculation | Low | Confirmed |
| 10 | Correct `_disableInitializers()` usage | None | Positive |
| 11 | Storage slots use simple keccak256 not ERC7201 | None | Informational |
| 12 | `hasAsset` reverts on empty asset list | Low | Confirmed |
| 13 | `addAsset` duplicate check behaviour | Low | Not a bug |

**Critical**: 0
**High**: 0
**Medium**: 4 (Findings 3, 4, 6, 7)
**Low**: 5 (Findings 1, 5, 8, 9, 12)
**Informational/None**: 4 (Findings 2, 10, 11, 13)

## Positive security observations

1. **Proxy safety**: `_disableInitializers()` is correctly called in the constructor, preventing implementation contract initialisation
2. **Reentrancy protection**: All state-changing entry points (`deposit`, `mint`, `withdraw`, `redeem`, `processAccounting`) use `nonReentrant`
3. **Role-based access control**: Comprehensive role separation (PROCESSOR_ROLE, PAUSER_ROLE, UNPAUSER_ROLE, PROVIDER_MANAGER_ROLE, BUFFER_MANAGER_ROLE, ASSET_MANAGER_ROLE, PROCESSOR_MANAGER_ROLE, HOOKS_MANAGER_ROLE, ASSET_WITHDRAWER_ROLE, FEE_MANAGER_ROLE)
4. **SafeERC20 usage**: All ERC20 interactions use OpenZeppelin's SafeERC20
5. **Paused state on initialisation**: The vault starts paused, allowing safe configuration before accepting deposits
6. **Provider check on unpause**: The vault cannot be unpaused without a provider being set
7. **Asset deletion safety**: Assets with non-zero balances cannot be deleted
8. **Hooks validation**: The `setHooks` function validates that the hooks contract references this vault via `IHooks(hooks_).VAULT() == address(this)`
9. **mintShares caller validation**: Only the hooks contract can call `mintShares`, preventing unauthorised share minting
10. **ERC4626 rounding**: Deposit operations round shares down (favouring vault), withdrawal operations round shares up (favouring vault)
