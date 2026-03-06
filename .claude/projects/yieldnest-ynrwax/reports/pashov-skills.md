# Security review -- YieldNest ynRWAx vault

---

## Scope

|                                  |                                                                                                            |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **Mode**                         | DEEP (full codebase + adversarial reasoning)                                                               |
| **Files reviewed**               | `Vault.sol` . `BaseVault.sol` . `VaultLib.sol`<br>`HooksLib.sol` . `LinearWithdrawalFeeLib.sol` . `Guard.sol`<br>`FeeMath.sol` . `LinearWithdrawalFee.sol`<br>`IVault.sol` . `IHooks.sol` . `IStrategy.sol` . `IProvider.sol` . `IValidator.sol` |
| **Confidence threshold (1-100)** | 75                                                                                                         |

---

## Findings

[95] **1. Guard parameter validation only checks ADDRESS-type params, ignoring UINT256 rules entirely**

`Guard.validateCall` . Confidence: 95

**Description**

The `Guard.validateCall` function iterates `rule.paramRules` but the loop body only acts when `paramType == ADDRESS`, silently skipping any `UINT256` param rules. If a PROCESSOR_MANAGER configures a `ParamRule` with `paramType = UINT256` expecting it to restrict numerical arguments (amounts, slippage, etc.), the guard will never validate those parameters and the processor can pass arbitrary uint256 values to whitelisted targets. This means the Guard provides a false sense of security for any non-address parameter, and the PROCESSOR_ROLE holder can drain tokens by calling whitelisted functions with unrestricted amounts.

```solidity
// Guard.sol lines 22-28
for (uint256 i = 0; i < rule.paramRules.length; i++) {
    if (rule.paramRules[i].paramType == IVault.ParamType.ADDRESS) {
        address addressValue = abi.decode(data[4 + i * 32:], (address));
        _validateAddress(addressValue, rule.paramRules[i]);
        continue;
    }
    // UINT256 params fall through with NO validation
}
```

**Fix**

```diff
  for (uint256 i = 0; i < rule.paramRules.length; i++) {
      if (rule.paramRules[i].paramType == IVault.ParamType.ADDRESS) {
          address addressValue = abi.decode(data[4 + i * 32:], (address));
          _validateAddress(addressValue, rule.paramRules[i]);
          continue;
      }
+     if (rule.paramRules[i].paramType == IVault.ParamType.UINT256) {
+         uint256 uintValue = abi.decode(data[4 + i * 32:], (uint256));
+         _validateUint256(uintValue, rule.paramRules[i]);
+         continue;
+     }
  }
```

---

[90] **2. Processor can execute arbitrary calls with ETH value, enabling native asset theft**

`VaultLib.processor` . Confidence: 90

**Description**

The `processor` function executes `targets[i].call{value: values[i]}(data[i])` where the `values` array is `uint256[] memory` (not `calldata`, making it mutable, though this is a minor point). The vault receives native ETH via `receive() external payable` and counts it in `computeTotalAssets()` when `countNativeAsset` is true. The PROCESSOR_ROLE holder can set up a rule for an arbitrary target contract and transfer out all native ETH held by the vault. While Guard validates the call, it does not restrict the `value` parameter at all -- only function selectors and address-type parameters are checked. This means any active processor rule allows sending the vault's entire ETH balance to the target. Given the vault explicitly accepts and accounts for native ETH, this constitutes a theft vector for the PROCESSOR_ROLE.

```solidity
// VaultLib.sol line 451
(bool success, bytes memory returnData_) = targets[i].call{value: values[i]}(data[i]);
```

**Fix**

```diff
  function validateCall(address target, uint256 value, bytes calldata data) internal view {
      bytes4 funcSig = bytes4(data[:4]);
      IVault.FunctionRule storage rule = VaultLib.getProcessorStorage().rules[target][funcSig];
      if (!rule.isActive) revert RuleNotActive(target, funcSig);
+     if (value > 0) revert NativeValueNotAllowed();
```

---

[90] **3. `withdrawAsset` does not charge withdrawal fees, allowing fee bypass for privileged withdrawals**

`BaseVault.withdrawAsset` . Confidence: 90

**Description**

The standard `withdraw` and `redeem` functions apply withdrawal fees via `_feeOnRaw` and `_feeOnTotal` respectively. However, `withdrawAsset` (which allows withdrawing any supported asset, restricted to `ASSET_WITHDRAWER_ROLE`) converts assets to shares without any fee computation. The shares burned are calculated purely from `_convertToShares(asset_, assets, Math.Rounding.Ceil)` with no fee added. If the ASSET_WITHDRAWER_ROLE is granted to a user or a contract that acts on behalf of users (e.g., a routing contract or cross-chain bridge), users could withdraw through this path and completely bypass the withdrawal fee. This is a design-level inconsistency: the privileged `withdrawAsset` creates a fee-free exit path.

```solidity
// BaseVault.sol lines 613-629
function withdrawAsset(address asset_, uint256 assets, address receiver, address owner)
    public
    virtual
    onlyRole(ASSET_WITHDRAWER_ROLE)
    returns (uint256 shares)
{
    if (paused()) { revert Paused(); }
    (shares,) = _convertToShares(asset_, assets, Math.Rounding.Ceil);
    // No fee applied ^^^
    ...
}
```

**Fix**

```diff
  function withdrawAsset(address asset_, uint256 assets, address receiver, address owner)
      ...
  {
      if (paused()) { revert Paused(); }
+     uint256 fee = _feeOnRaw(assets, owner);
-     (shares,) = _convertToShares(asset_, assets, Math.Rounding.Ceil);
+     (shares,) = _convertToShares(asset_, assets + fee, Math.Rounding.Ceil);
```

---

[90] **4. `_feeOnRaw` and `_feeOnTotal` are `public view` but named with underscore prefix, exposing internal fee logic as external entry points**

`BaseVault._feeOnRaw` / `BaseVault._feeOnTotal` . Confidence: 90

**Description**

The functions `_feeOnRaw` and `_feeOnTotal` are declared as `public view virtual override` in `BaseVault.sol` (lines 1021, 1029) and their concrete implementations in `Vault.sol` (lines 68, 80). The underscore prefix convention implies internal access, but `public` visibility means they are callable externally and are part of the ABI. This is a direct violation of the ERC-4626 interface specification -- these functions leak fee calculation internals that may be used by attackers to precisely calculate optimal extraction amounts. More critically, any external contract relying on these for fee estimation could be manipulated if the vault is upgraded. While this is an informational concern regarding interface hygiene, the real risk is that the `IVault` interface exposes `_feeOnRaw` and `_feeOnTotal` as external, meaning any integrated protocol treats these as stable API surface. Any future upgrade that changes fee logic would silently break integrators without a clean interface boundary.

---

[85] **5. Force-feeding ETH via `selfdestruct` or coinbase rewards inflates `totalBaseAssets` when `countNativeAsset` is true**

`VaultLib.computeTotalAssets` . Confidence: 85

**Description**

When `countNativeAsset` is true, `computeTotalAssets()` uses `address(this).balance` to include native ETH in the vault's total value. An attacker can force-send ETH to the vault via `selfdestruct` (pre-Dencun) or by using the vault as the coinbase recipient, inflating the vault's reported total assets without minting any shares. This manipulates the share price: `convertToShares` will return fewer shares per deposit because `totalBaseAssets` is inflated. Existing depositors benefit while new depositors are diluted. Furthermore, this breaks the accounting invariant when `alwaysComputeTotalAssets` is false and `processAccounting()` is called -- the cached `totalAssets` is updated to include the donated ETH, permanently inflating the share price.

Attack path: attacker creates a contract, funds it with ETH, calls `selfdestruct(vaultAddress)`. Next call to `processAccounting()` or `computeTotalAssets()` includes the donated ETH. Attacker who deposited before the donation now holds shares worth more than they deposited.

```solidity
// VaultLib.sol line 378
totalBaseBalance = vaultStorage.countNativeAsset ? address(this).balance : 0;
```

**Fix**

```diff
- totalBaseBalance = vaultStorage.countNativeAsset ? address(this).balance : 0;
+ totalBaseBalance = vaultStorage.countNativeAsset ? vaultStorage.trackedNativeBalance : 0;
```

Track native ETH deposits via the `receive()` function in an internal accounting variable rather than reading `address(this).balance`.

---

[85] **6. Deposit-then-`processAccounting` sandwich enables share price manipulation**

`VaultLib.processAccounting` / `BaseVault.deposit` . Confidence: 85

**Description**

The `processAccounting()` function is callable by anyone (`public nonReentrant` with no access control on `BaseVault`, line 933). It reads the live on-chain balances and rates, then updates `vaultStorage.totalAssets`. When `alwaysComputeTotalAssets` is false (cached mode), the share conversion uses the cached `totalAssets`. An attacker can: (1) Observe that asset rates have increased but `processAccounting()` has not been called, (2) Deposit at the stale (lower) total asset value, receiving more shares, (3) Call `processAccounting()` to update totalAssets to the higher value, (4) Redeem at the new (higher) share price for profit. The rate of return depends on how stale the cached value is relative to the live computation.

```solidity
// BaseVault.sol line 933 -- anyone can call
function processAccounting() public virtual nonReentrant {
    _processAccounting();
}
```

**Fix**

```diff
- function processAccounting() public virtual nonReentrant {
+ function processAccounting() public virtual nonReentrant onlyRole(PROCESSOR_ROLE) {
      _processAccounting();
  }
```

---

[85] **7. Hooks contract can mint unbounded shares via `mintShares`, diluting all existing holders**

`BaseVault.mintShares` . Confidence: 85

**Description**

The `mintShares` function (BaseVault line 970) allows the hooks contract to mint arbitrary shares to any recipient without any corresponding asset deposit or `totalAssets` increase. It only checks `msg.sender == address(hooks())`. The HOOKS_MANAGER_ROLE can set the hooks contract to any address that implements `IHooks.VAULT() == address(this)`. A compromised or malicious hooks contract can call `mintShares` to inflate total supply without backing, diluting all existing shareholders.

While this requires a compromised HOOKS_MANAGER_ROLE, the attack path is concrete: set hooks to attacker contract -> attacker contract calls `mintShares(attacker, huge_amount)` -> attacker redeems for a proportional share of all vault assets. The confidence deduction of -25 applies for privileged caller, but the impact is total fund theft.

```solidity
// BaseVault.sol lines 970-976
function mintShares(address recipient, uint256 shares) external {
    if (msg.sender != address(hooks())) {
        revert CallerNotHooks();
    }
    _mint(recipient, shares);
}
```

---

[85] **8. `previewDeposit` does not account for withdrawal fees, creating asymmetry with `previewWithdraw` / `previewRedeem`**

`BaseVault.previewDeposit` . Confidence: 85

**Description**

The ERC-4626 standard requires that `previewDeposit` returns the exact number of shares that `deposit` would mint. However, `previewDeposit` simply calls `_convertToShares(asset(), assets, Math.Rounding.Floor)` which is correct for a zero-fee deposit. The issue is that the vault has withdrawal fees but no deposit fees. The `previewWithdraw` function adds the fee on top: `_convertToShares(asset(), assets + fee, Math.Rounding.Ceil)`. This means `previewWithdraw(previewRedeem(shares))` does not necessarily return the original `shares` -- there is a fee-driven asymmetry. While this is technically correct behaviour for a fee-bearing vault, the real ERC-4626 compliance issue is that `previewRedeem` computes `assets - _feeOnTotal(assets, _msgSender())` where `_msgSender()` is the caller of `previewRedeem`. If a smart contract calls `previewRedeem` to size a redemption, the fee is calculated for the calling contract's address, not the actual owner who will redeem. This means the preview is inaccurate for any integrator contract, violating ERC-4626's requirement that preview functions return the exact amount.

```solidity
// BaseVault.sol line 207-210
function previewRedeem(uint256 shares) public view virtual returns (uint256 assets) {
    (assets,) = _convertToAssets(asset(), shares, Math.Rounding.Floor);
    return assets - _feeOnTotal(assets, _msgSender());  // fee depends on caller, not owner
}
```

**Fix**

```diff
  function previewRedeem(uint256 shares) public view virtual returns (uint256 assets) {
      (assets,) = _convertToAssets(asset(), shares, Math.Rounding.Floor);
-     return assets - _feeOnTotal(assets, _msgSender());
+     // Note: fee is caller-dependent; document this deviation from ERC-4626
+     return assets - _feeOnTotal(assets, _msgSender());
  }
```

Consider adding an overloaded `previewRedeem(uint256 shares, address owner)` that computes the fee for the actual owner.

---

[80] **9. `convertToShares` and `convertToAssets` use `+1` virtual offset instead of `_decimalsOffset` -- weaker first-depositor protection**

`VaultLib.convertToShares` / `VaultLib.convertToAssets` . Confidence: 80

**Description**

The share/asset conversion uses `(totalSupply + 1)` and `(totalAssets + 1)` as the virtual offset (VaultLib lines 292, 312). The standard OpenZeppelin ERC-4626 implementation uses `10 ** _decimalsOffset()` which provides stronger protection against the first-depositor inflation attack. With a `+1` offset, when `totalSupply == 0` and `totalAssets == 0`, the first depositor gets `shares = baseAssets * (0 + 1) / (0 + 1) = baseAssets` shares. An attacker can then donate assets directly (token transfer, not via `deposit`) to inflate `totalAssets` via `processAccounting()`. With a `+1` offset, the attacker only needs to donate slightly more than the victim's deposit to steal nearly all of it. The standard `_decimalsOffset()` approach (typically `10 ** 3` or higher) makes this attack exponentially more expensive.

For a vault holding high-value RWA tokens, even a small rounding attack could be profitable.

```solidity
// VaultLib.sol line 292
baseAssets = shares.mulDiv(totalAssets + 1, totalSupply + 1, rounding);
// VaultLib.sol line 312
shares = baseAssets.mulDiv(totalSupply + 1, totalAssets + 1, rounding);
```

**Fix**

```diff
- baseAssets = shares.mulDiv(totalAssets + 1, totalSupply + 1, rounding);
+ uint256 offset = 10 ** _decimalsOffset();
+ baseAssets = shares.mulDiv(totalAssets + offset, totalSupply + offset, rounding);
```

---

[80] **10. `_deposit` updates `totalAssets` before transferring tokens -- allows re-entry via hooks to observe inflated state**

`BaseVault._deposit` . Confidence: 80

**Description**

In `_deposit` (BaseVault line 535-557), the order of operations is: (1) check asset active, (2) `_addTotalAssets(baseAssets)`, (3) `safeTransferFrom`, (4) `_mint`. The `_addTotalAssets` call increments `totalAssets` before the token transfer. Additionally, the `beforeDeposit` hook is called *before* `_deposit`, and the `afterDeposit` hook is called *after*. During the `beforeDeposit` hook, `totalAssets` has not yet been incremented, which is correct. However, if a malicious hooks contract or a fee-on-transfer token triggers a re-entrant call between steps (2) and (3), the vault's `totalAssets` is already inflated but no tokens have arrived and no shares have been minted. Although `nonReentrant` on `deposit/mint` prevents direct re-entry into those functions, the hooks contract call (which uses a raw `.call`) could invoke other vault functions like `processAccounting()` or `convertToShares` that would see the inflated `totalAssets`. This is mitigated by `nonReentrant` on `processAccounting`, but read-only functions like `totalBaseAssets()`, `convertToShares()`, and `previewDeposit()` would return manipulated values during the hook execution window.

```solidity
// BaseVault.sol lines 547-550
_addTotalAssets(baseAssets);           // totalAssets inflated
SafeERC20.safeTransferFrom(...);      // tokens not yet transferred
_mint(receiver, shares);              // shares not yet minted
```

**Fix**

```diff
- _addTotalAssets(baseAssets);
  SafeERC20.safeTransferFrom(IERC20(asset_), caller, address(this), assets);
+ _addTotalAssets(baseAssets);
  _mint(receiver, shares);
```

---

[80] **11. `_withdraw` decrements `totalAssets` using current rate but `redeem` computed shares at potentially different rate**

`BaseVault._withdraw` / `BaseVault.redeem` . Confidence: 80

**Description**

In the `redeem` function, `previewRedeem(shares)` is called to compute `assets`. This internally calls `_convertToAssets` which uses the provider rate. Then `_withdraw` is called, which computes `_convertAssetToBase(asset(), assets, Math.Rounding.Floor)` to determine how much to decrement from `totalAssets`. If the provider rate changes between the two calls (e.g., within the same transaction via a manipulable oracle, or due to state changes in hooks), the amount subtracted from `totalAssets` could differ from what was used to compute the share conversion. This can lead to accounting drift over time. With `alwaysComputeTotalAssets = false`, this drift accumulates permanently.

In `_withdraw` (line 591), the base amount subtracted is computed fresh: `_convertAssetToBase(asset(), assets, Math.Rounding.Floor)`. But `assets` was derived from `previewRedeem` which also called `convertBaseToAsset` with the same rate. The two conversions (share->baseAssets->assets, then assets->baseAssets) introduce a round-trip rounding loss. This means the subtracted base amount may be less than what was originally added during the corresponding deposit, slowly inflating `totalAssets` relative to actual holdings.

```solidity
// BaseVault.sol line 591 -- round-trip conversion introduces rounding loss
_subTotalAssets(_convertAssetToBase(asset(), assets, Math.Rounding.Floor));
```

---

[80] **12. Guard `paramRules` index-based parameter decoding assumes standard ABI layout, bypassable with non-standard encoding**

`Guard.validateCall` . Confidence: 80

**Description**

The Guard decodes parameters using a fixed offset: `abi.decode(data[4 + i * 32:], (address))`. This assumes standard ABI encoding where parameter `i` starts at byte offset `4 + i * 32`. However, functions with dynamic types (bytes, arrays, strings) use pointer-based ABI encoding where the actual data is at an offset specified by the pointer. If a whitelisted function has a dynamic-type parameter before an address parameter, the guard will read the wrong calldata position and validate the offset pointer as an address instead of the actual address value. The PROCESSOR_ROLE can craft calldata with a valid offset that passes the allowlist check while the actual address parameter at the pointed-to location is not on the allowlist.

```solidity
// Guard.sol line 24
address addressValue = abi.decode(data[4 + i * 32:], (address));
```

**Fix**

```diff
- address addressValue = abi.decode(data[4 + i * 32:], (address));
+ // Use proper ABI decoding that accounts for dynamic types
+ // or restrict paramRules to only be used with functions that have
+ // only static-type parameters in positions before the validated param
```

---

[80] **13. Withdrawal fee is collected but never transferred to any fee recipient -- fees are burned as excess shares**

`BaseVault.withdraw` / `BaseVault.redeem` . Confidence: 80

**Description**

The withdrawal fee mechanism works as follows: in `previewWithdraw`, the fee is added to the asset amount before conversion to shares: `shares = convertToShares(assets + fee)`. In `previewRedeem`, the fee is subtracted from assets: `assets = convertToAssets(shares) - feeOnTotal`. In both cases, the user receives fewer assets (or burns more shares) than the fee-free conversion would give. However, the excess shares burned (or the deficit in assets transferred) simply reduces the vault's share supply without the corresponding assets leaving the vault. This means the fee accrues to all remaining shareholders proportionally, not to a specific fee recipient. While this may be by design, it means there is no explicit fee collection mechanism for the protocol/admin. If the intended design is for fees to go to a treasury, this is missing functionality. More critically, this means the fee is effectively a "tax" that increases the share price for remaining holders rather than generating protocol revenue.

---

| # | Confidence | Title |
|---|---|---|
| 1 | [95] | Guard parameter validation only checks ADDRESS-type params, ignoring UINT256 rules entirely |
| 2 | [90] | Processor can execute arbitrary calls with ETH value, enabling native asset theft |
| 3 | [90] | `withdrawAsset` does not charge withdrawal fees, allowing fee bypass for privileged withdrawals |
| 4 | [90] | `_feeOnRaw` and `_feeOnTotal` are `public view` but named with underscore prefix, exposing internal fee logic as external entry points |
| 5 | [85] | Force-feeding ETH via `selfdestruct` or coinbase rewards inflates `totalBaseAssets` when `countNativeAsset` is true |
| 6 | [85] | Deposit-then-`processAccounting` sandwich enables share price manipulation |
| 7 | [85] | Hooks contract can mint unbounded shares via `mintShares`, diluting all existing holders |
| 8 | [85] | `previewRedeem` computes fee for `msg.sender` instead of `owner`, breaking ERC-4626 compliance for integrators |
| 9 | [80] | `convertToShares`/`convertToAssets` use `+1` virtual offset -- weaker first-depositor protection |
| 10 | [80] | `_deposit` updates `totalAssets` before transferring tokens -- allows hooks to observe inflated state |
| 11 | [80] | `_withdraw` round-trip base conversion introduces persistent accounting drift |
| 12 | [80] | Guard parameter decoding assumes standard ABI layout, bypassable with dynamic types |
| 13 | [80] | Withdrawal fee is collected but never transferred to a fee recipient -- accrues to shareholders |
| | | **Below confidence threshold** |
| 14 | [75] | `processAccounting` is publicly callable with no access control, enabling front-running of rate changes |
| 15 | [75] | `previewWithdraw` uses `_msgSender()` for fee calculation, returning different values depending on caller |
| 16 | [60] | Fee-on-transfer tokens cause accounting mismatch in `_deposit` |
| 17 | [60] | Rebasing tokens held by vault cause `totalAssets` divergence from cached value |
| 18 | [55] | `deleteAsset` uses swap-and-pop pattern which may disrupt external index references |

---

[75] **14. `processAccounting` is publicly callable with no access control, enabling front-running of rate changes**

`BaseVault.processAccounting` . Confidence: 75

**Description**

The `processAccounting()` function on BaseVault (line 933) is `public virtual nonReentrant` with no role restriction. Anyone can call it at any time. In cached mode (`alwaysComputeTotalAssets = false`), the timing of `processAccounting` calls directly affects share pricing. A MEV searcher can monitor the provider's rate feed and front-run rate increases by depositing before calling `processAccounting`, or front-run rate decreases by redeeming before calling `processAccounting`. While finding #6 describes the sandwich attack, this is the underlying root cause: the lack of access control on the function that updates the price-determining state variable.

---

[75] **15. `previewWithdraw` uses `_msgSender()` for fee calculation, returning different values depending on caller**

`BaseVault.previewWithdraw` . Confidence: 75

**Description**

The `previewWithdraw` function calls `_feeOnRaw(assets, _msgSender())` (line 197). Since fees can be overridden per-user, the preview result changes depending on who calls it. An integrator contract calling `previewWithdraw` will get the fee calculated for its own address, not for the end user. This violates the ERC-4626 principle that preview functions should be predictable and consistent, and will cause incorrect share calculations when called by routers, aggregators, or other smart contract integrators.

---

[60] **16. Fee-on-transfer tokens cause accounting mismatch in `_deposit`**

`BaseVault._deposit` . Confidence: 60

**Description**

The `_deposit` function calls `_addTotalAssets(baseAssets)` based on the `assets` parameter, then transfers tokens. If the deposited asset has a fee-on-transfer, the vault receives fewer tokens than `assets` but `totalAssets` is increased by the full `baseAssets` amount. This inflates the vault's reported value. However, this requires the vault to list a fee-on-transfer token as a supported asset, which may be an operational choice.

---

[60] **17. Rebasing tokens held by vault cause `totalAssets` divergence from cached value**

`VaultLib.computeTotalAssets` . Confidence: 60

**Description**

In cached mode, `totalAssets` only updates via `_addTotalAssets`, `_subTotalAssets`, and `processAccounting`. If the vault holds a rebasing token (e.g., stETH), the actual `balanceOf` changes between accounting updates but the cached value does not reflect this. Share pricing will be stale until someone calls `processAccounting()`.

---

[55] **18. `deleteAsset` uses swap-and-pop pattern which may disrupt external index references**

`VaultLib.deleteAsset` . Confidence: 55

**Description**

When deleting an asset, the last asset in the list is moved to the deleted position. Any off-chain or on-chain system that cached the index of the moved asset will now reference the wrong position. While the on-chain `assetParams.index` is updated, external integrations might break.

---

> This review was performed by an AI assistant applying the Pashov Audit Group methodology. AI analysis can never verify the complete absence of vulnerabilities and no guarantee of security is given. Team security reviews, bug bounty programmes, and on-chain monitoring are strongly recommended. For a consultation regarding your projects' security, visit [https://www.pashov.com](https://www.pashov.com)
