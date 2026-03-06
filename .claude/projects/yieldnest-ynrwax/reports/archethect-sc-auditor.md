# YieldNest ynRWAx vault security audit

**Methodology**: Archethect sc-auditor Map-Hunt-Attack
**Target**: YieldNest ynRWAx Vault v0.4.2
**Solidity**: ^0.8.24 (EVM target: Cancun)
**Date**: 2026-03-06
**Auditor**: Claude Opus 4.6 (automated)

---

## Static analysis results

### Slither

**Status**: FAILED -- compilation error.
The source files were extracted from Blockscout and use `@openzeppelin/contracts/` import paths inside the bundled OpenZeppelin upgradeable contracts, but the `@openzeppelin` remapping is not resolvable from the flat directory layout. Slither requires compilable source.

### Aderyn

**Status**: FAILED -- same compilation error as Slither.

Both tools failed. The audit proceeds in **manual-only mode** without static analysis results.

---

## Phase 1: MAP -- system understanding

### Architecture diagram

```
                        +---------------------+
                        |   TransparentProxy   |
                        |  (ERC1967 Proxy)     |
                        +----------+----------+
                                   |
                                   v  delegatecall
                        +---------------------+
                        |       Vault          |  (Vault.sol)
                        |  is BaseVault,       |
                        |  LinearWithdrawalFee |
                        +----------+----------+
                                   |
              +--------------------+--------------------+
              |                    |                    |
              v                    v                    v
     +----------------+   +----------------+   +------------------+
     |   VaultLib      |   |  Guard         |   |  HooksLib        |
     |  (library)      |   |  (library)     |   |  (library)       |
     |  - storage      |   |  - processor   |   |  - before/after  |
     |  - conversions  |   |    validation  |   |    hook calls    |
     |  - accounting   |   +--------+-------+   +--------+---------+
     |  - processor    |            |                     |
     +--------+--------+            |                     |
              |                     v                     v
              |            +----------------+   +------------------+
              |            | IValidator     |   | IHooks           |
              |            | (external)     |   | (external)       |
              |            +----------------+   +------------------+
              |
              +-----------> IProvider (external: getRate)
              +-----------> IStrategy (external: buffer withdraw)
```

### Components

#### Vault.sol (top-level contract)
- **Purpose**: Concrete vault inheriting BaseVault and LinearWithdrawalFee. Defines FEE_MANAGER_ROLE, exposes fee admin functions, and wires `_feeOnRaw`/`_feeOnTotal` to the linear withdrawal fee module.
- **Key state**: `FEE_MANAGER_ROLE` constant.
- **Roles**: `FEE_MANAGER_ROLE` can set/override withdrawal fees.
- **External surface**: `initialize()`, `setBaseWithdrawalFee()`, `overrideBaseWithdrawalFee()`, `_feeOnRaw()` (public view), `_feeOnTotal()` (public view).

#### BaseVault.sol (abstract base)
- **Purpose**: Core ERC4626 multi-asset vault with role-based access control, hooks system, processor, and accounting.
- **Key state variables** (via VaultLib storage slots):
  - `VaultStorage`: `totalAssets`, `provider`, `buffer`, `paused`, `decimals`, `countNativeAsset`, `alwaysComputeTotalAssets`, `defaultAssetIndex`
  - `AssetStorage`: `assets` mapping, `list` array
  - `ProcessorStorage`: `rules` mapping (target -> funcSig -> FunctionRule)
  - `HooksStorage`: `hooks` (IHooks)
  - `FeeStorage`: `baseWithdrawalFee`, `overriddenBaseWithdrawalFee` mapping
- **Roles**:
  - `DEFAULT_ADMIN_ROLE`: Full admin
  - `PROCESSOR_ROLE`: Can execute arbitrary calls via `processor()`
  - `PAUSER_ROLE` / `UNPAUSER_ROLE`: Pause/unpause
  - `PROVIDER_MANAGER_ROLE`: Set price provider
  - `BUFFER_MANAGER_ROLE`: Set buffer strategy
  - `ASSET_MANAGER_ROLE`: Add/update/delete assets, toggle alwaysComputeTotalAssets
  - `PROCESSOR_MANAGER_ROLE`: Set processor rules (Guard allowlists)
  - `HOOKS_MANAGER_ROLE`: Set hooks contract
  - `ASSET_WITHDRAWER_ROLE`: Withdraw specific assets directly from vault balance
- **External surface**:
  - Unprivileged: `deposit()`, `mint()`, `withdraw()`, `redeem()`, `depositAsset()`, `processAccounting()`, all view functions, `receive()`
  - `PROCESSOR_ROLE`: `processor()`
  - `ASSET_WITHDRAWER_ROLE`: `withdrawAsset()`
  - `mintShares()`: Only callable by the hooks contract
  - Various admin setters with role guards

#### VaultLib.sol (library)
- **Purpose**: Stateless library holding all storage access, asset conversion maths, accounting, and processor execution logic. Uses ERC-7201 namespaced storage.
- **Key functions**: `convertAssetToBase()`, `convertBaseToAsset()`, `convertToShares()`, `convertToAssets()`, `computeTotalAssets()`, `processAccounting()`, `processor()`, `addAsset()`, `deleteAsset()`, `addTotalAssets()`, `subTotalAssets()`
- **External calls**: `IProvider.getRate()`, `IERC20.balanceOf()`, `IHooks` (before/after accounting hooks), arbitrary `target.call()` in `processor()`

#### Guard.sol (library)
- **Purpose**: Validates processor calls against stored rules (allowlists, validators).
- **Key logic**: Checks `isActive` flag, delegates to `IValidator` if set, otherwise validates ADDRESS params against allowlists.

#### HooksLib.sol (library)
- **Purpose**: Dispatches before/after hooks for deposit, mint, redeem, withdraw, and processAccounting if the hooks contract is set and the corresponding flag is enabled.
- **External calls**: Low-level `.call()` to the hooks contract.

#### FeeMath.sol (library)
- **Purpose**: Pure maths for fee calculations. `BASIS_POINT_SCALE = 1e8`. `feeOnRaw` rounds up (Ceil). `feeOnTotal` rounds up (Ceil).

#### LinearWithdrawalFee.sol (abstract)
- **Purpose**: Thin wrapper delegating to LinearWithdrawalFeeLib.

#### LinearWithdrawalFeeLib.sol (library)
- **Purpose**: Per-user or global withdrawal fee lookup and admin. Validates fee <= BASIS_POINT_SCALE.

### Invariants

#### Local properties
1. **INV-1**: `totalSupply == sum(balanceOf[i] for all i)` (inherited from ERC20Upgradeable).
2. **INV-2**: `totalAssets` (cached) approximates the sum of all on-chain asset balances converted to base denomination (updated by `processAccounting()`).
3. **INV-3**: `baseWithdrawalFee <= 1e8` (enforced by `LinearWithdrawalFeeLib`).
4. **INV-4**: The asset at index 0 (base asset) and the asset at `defaultAssetIndex` (0 or 1) cannot be deleted.
5. **INV-5**: Only active assets can accept deposits (`_deposit` checks `active` flag).
6. **INV-6**: Vault shares can only be minted via `deposit`, `mint`, `depositAsset`, or by the hooks contract via `mintShares()`.

#### System-wide invariants
7. **INV-7**: The vault should never be insolvent: the sum of claimable asset value (at current exchange rates) should be >= the value implied by totalSupply * share price, minus rounding dust.
8. **INV-8**: The `processor()` function cannot execute arbitrary calls -- it is constrained by Guard rules set by PROCESSOR_MANAGER_ROLE.
9. **INV-9**: Liveness: users can always withdraw when unpaused and the buffer has sufficient funds.
10. **INV-10**: No unprivileged user can mint shares, burn others' shares, or extract assets beyond their share entitlement.

---

## Phase 2: HUNT -- systematic hotspot identification

### Spot 1: Donation attack on `computeTotalAssets()` -- share price manipulation

- **Components/Functions**: `VaultLib.computeTotalAssets()` (line 374-389), `processAccounting()`, `totalBaseAssets()`, deposit/withdraw flows
- **Attacker type**: Unprivileged user or flash loan attacker
- **Related invariants**: INV-2, INV-7
- **Why suspicious**: `computeTotalAssets()` reads raw `IERC20.balanceOf(address(this))` and `address(this).balance` for all assets. An attacker can donate tokens directly to the vault (bypassing the deposit function) to inflate `computeTotalAssets()`. If `alwaysComputeTotalAssets` is true, this directly inflates `totalBaseAssets()`, which inflates share prices in real-time, enabling classic share-inflation attacks.
- **Supporting evidence**: Risk Pattern #7 (Donation Attacks), Risk Pattern #1 (ERC-4626 Share Inflation).
- **Priority**: High

### Spot 2: `_feeOnRaw` and `_feeOnTotal` are `public` -- external visibility of internal fee functions

- **Components/Functions**: `BaseVault._feeOnRaw()` (line 1021), `BaseVault._feeOnTotal()` (line 1029), `Vault._feeOnRaw()` (line 68), `Vault._feeOnTotal()` (line 80)
- **Attacker type**: N/A (design concern)
- **Related invariants**: None directly
- **Why suspicious**: Functions prefixed with underscore are conventionally internal. These are declared `public view` in the interface and implementation. While not exploitable per se, this breaks the convention and exposes internal fee logic unnecessarily. They are used in `previewWithdraw()` and `previewRedeem()` which use `_msgSender()`, meaning preview functions behave differently depending on the caller, which is non-standard for ERC4626 views.
- **Supporting evidence**: Manual analysis. ERC4626 spec expects preview functions to be caller-independent.
- **Priority**: Medium

### Spot 3: `previewWithdraw`/`previewRedeem` use `_msgSender()` -- caller-dependent preview functions

- **Components/Functions**: `BaseVault.previewWithdraw()` (line 196-199), `BaseVault.previewRedeem()` (line 207-210)
- **Attacker type**: Any integrating contract
- **Related invariants**: ERC4626 compliance
- **Why suspicious**: ERC4626 specifies that `previewWithdraw` and `previewRedeem` MUST return values that are "as close to exact" as possible and "MUST NOT account for... caller-specific limits." Using `_msgSender()` for fee lookup means the preview result varies by caller, which breaks composability with routers, aggregators, and other contracts that call preview to calculate amounts before executing.
- **Supporting evidence**: ERC4626 specification. Risk Pattern #4 (Rounding in Share/Token Math).
- **Priority**: Medium

### Spot 4: `withdrawAsset()` bypasses fees -- privileged but no fee deduction

- **Components/Functions**: `BaseVault.withdrawAsset()` (line 613-629), `BaseVault._withdrawAsset()` (line 640-664)
- **Attacker type**: ASSET_WITHDRAWER_ROLE holder
- **Related invariants**: INV-7, INV-10
- **Why suspicious**: `withdrawAsset()` allows direct withdrawal of any asset held by the vault, bypassing the buffer strategy and without deducting withdrawal fees. It converts shares with `Rounding.Ceil` (burning more shares) but does not charge fees. A privileged role could use this to withdraw assets fee-free. Per the methodology, we assume privileged roles are honest, but the lack of fee deduction could be unintentional.
- **Supporting evidence**: Manual analysis. The function bypasses `_feeOnRaw`/`_feeOnTotal` entirely.
- **Priority**: Low (privileged function)

### Spot 5: `processor()` arbitrary external calls -- Guard bypass vectors

- **Components/Functions**: `VaultLib.processor()` (line 441-458), `Guard.validateCall()` (line 9-29)
- **Attacker type**: PROCESSOR_ROLE holder
- **Related invariants**: INV-8
- **Why suspicious**: The `processor()` function executes arbitrary low-level calls (`target.call{value}(data)`) from the vault. The Guard only validates: (a) the rule is active, (b) an optional IValidator check, (c) ADDRESS parameters against allowlists. However, Guard only validates params with `ParamType.ADDRESS` and skips `UINT256` params entirely (no validation logic for UINT256). Additionally, the Guard decodes params at fixed 32-byte offsets (`data[4 + i * 32:]`), which does not handle dynamic types (arrays, bytes) correctly. A PROCESSOR_ROLE holder could craft calldata with dynamic encoding to bypass address allowlist checks.
- **Supporting evidence**: Manual analysis of Guard.sol lines 22-28. Risk Pattern #3 (Flash Loan Entry Points -- processor could be used to interact with DeFi protocols).
- **Priority**: Medium

### Spot 6: `processAccounting()` is permissionless -- accounting manipulation timing

- **Components/Functions**: `BaseVault.processAccounting()` (line 933-935), `VaultLib.processAccounting()` (line 394-432)
- **Attacker type**: Unprivileged user, MEV bot
- **Related invariants**: INV-2, INV-7
- **Why suspicious**: `processAccounting()` is `public nonReentrant` with no access control. Anyone can call it at any time. Since it reads `IERC20.balanceOf(address(this))` for all assets, an attacker who donates tokens can then call `processAccounting()` to update `totalAssets` to an inflated value, or time it after a large withdrawal to deflate the value. When `alwaysComputeTotalAssets` is false (cached mode), this creates a window where `totalAssets` can be manipulated by timing donations + processAccounting calls.
- **Supporting evidence**: Risk Pattern #7 (Donation Attacks). Combined with Spot 1.
- **Priority**: High

### Spot 7: No share inflation protection (no virtual offset, no minimum deposit)

- **Components/Functions**: `VaultLib.convertToShares()` (line 304-313), `VaultLib.convertToAssets()` (line 285-294)
- **Attacker type**: First depositor
- **Related invariants**: INV-7, INV-10
- **Why suspicious**: The share/asset conversion uses `shares.mulDiv(totalAssets + 1, totalSupply + 1, rounding)` with a `+1` offset instead of OpenZeppelin's `_decimalsOffset()` virtual share approach. While the `+1` does provide minimal inflation resistance, it is far less effective than the standard `10^decimals` offset used by OpenZeppelin's ERC4626. A first depositor attack with a donation could still succeed if the donated amount is large enough relative to 1 wei of rounding protection.
- **Supporting evidence**: Risk Pattern #1 (ERC-4626 Share Inflation). The `+1` offset provides only 1 wei of rounding protection.
- **Priority**: High

### Spot 8: `_withdraw` subtracts totalAssets before burning shares -- inconsistent state during hooks

- **Components/Functions**: `BaseVault._withdraw()` (line 583-602)
- **Attacker type**: Malicious hooks contract (set by HOOKS_MANAGER_ROLE)
- **Related invariants**: INV-2, INV-7
- **Why suspicious**: In `_withdraw()`, the function calls `_subTotalAssets()` before `_burn()` and before `IStrategy(buffer).withdraw()`. But hooks are called before `_withdraw()` in the `withdraw()` and `redeem()` functions. More importantly, the external call to `IStrategy(buffer).withdraw()` on line 599 happens after state changes but before the function returns. If the buffer strategy has a callback mechanism, it could observe inconsistent state.
- **Supporting evidence**: Risk Pattern #6 (Cross-Contract Reentrancy). However, `nonReentrant` guard on `withdraw()` and `redeem()` mitigates recursive re-entry to the same vault.
- **Priority**: Low

### Spot 9: `mintShares()` -- hooks contract can mint unbounded shares

- **Components/Functions**: `BaseVault.mintShares()` (line 970-976)
- **Attacker type**: Compromised hooks contract
- **Related invariants**: INV-6, INV-7, INV-10
- **Why suspicious**: `mintShares()` allows the hooks contract to mint arbitrary shares to any address with no limit. While the hooks contract is set by HOOKS_MANAGER_ROLE (a privileged role, assumed honest), a vulnerability in the hooks contract itself could allow an unprivileged attacker to trigger unbounded share minting. The vault places full trust in the hooks contract.
- **Supporting evidence**: Manual analysis. This is a trust assumption that should be documented.
- **Priority**: Medium

### Spot 10: Guard parameter decoding assumes ABI encoding of fixed-size params only

- **Components/Functions**: `Guard.validateCall()` (line 9-29)
- **Attacker type**: PROCESSOR_ROLE holder
- **Related invariants**: INV-8
- **Why suspicious**: The Guard decodes each parameter at `data[4 + i * 32:]`. This works for functions with only fixed-size parameters (uint256, address) but fails for functions with dynamic parameters (bytes, arrays, strings) where ABI encoding uses offsets/pointers. If a processor rule is set for a function with dynamic params, the Guard would decode the offset pointer as an address value, potentially matching an allowlisted address by coincidence or by crafting the offset.
- **Supporting evidence**: Manual analysis of ABI encoding specification vs Guard decoding logic.
- **Priority**: Medium

---

## Phase 3: ATTACK -- deep dive per spot

### Attack 1: Donation attack inflating share price (Spots 1, 6, 7)

#### Call path trace

1. `computeTotalAssets()` (VaultLib.sol:374-389):
   ```solidity
   totalBaseBalance = vaultStorage.countNativeAsset ? address(this).balance : 0;
   for (uint256 i = 0; i < assetListLength; i++) {
       uint256 balance = IERC20(assetList[i]).balanceOf(address(this));
       if (balance == 0) continue;
       totalBaseBalance += convertAssetToBase(assetList[i], balance, Math.Rounding.Floor);
   }
   ```

2. When `alwaysComputeTotalAssets == true`, `totalBaseAssets()` calls `computeTotalAssets()` every time.

3. `convertToShares()` (VaultLib.sol:304-313):
   ```solidity
   baseAssets = convertAssetToBase(asset_, assets, rounding);
   shares = baseAssets.mulDiv(totalSupply + 1, totalAssets + 1, rounding);
   ```

#### Attack narrative

**Attacker role**: Any user (first depositor scenario) or flash loan borrower.

**Call sequence** (first depositor attack):
1. Attacker deposits 1 wei of the base asset via `deposit(1, attacker)`. Gets ~1 share (assuming 1:1 initial rate).
2. Attacker directly transfers a large amount of base asset (e.g., 1000e18) to the vault contract.
3. If `alwaysComputeTotalAssets == true`: the totalBaseAssets is now ~1000e18 + 1 immediately.
4. If `alwaysComputeTotalAssets == false`: attacker calls `processAccounting()` to update cached totalAssets to ~1000e18 + 1.
5. Victim deposits 500e18 of base asset via `deposit(500e18, victim)`.
6. Shares minted to victim: `500e18 * (1 + 1) / (1000e18 + 1 + 1)` ~ 0 shares (integer division truncation).
7. Victim's 500e18 tokens are now in the vault but they received 0 shares.
8. Attacker redeems their 1 share, receiving approximately all vault assets.

**Broken invariant**: INV-7 (vault insolvency -- victim loses funds), INV-10 (unprivileged user extracts assets beyond entitlement).

**Extracted value**: Victim's entire deposit.

#### Devil's advocate protocol

- **Does the `+1` offset prevent this?** The `+1` in `totalAssets + 1` and `totalSupply + 1` provides only 1 wei of protection. For the attack to fail, the donation would need to be less than the deposit amount. With tokens having 18 decimals, a donation of ~1e18 is enough to round away deposits smaller than 1e18. The protection is negligible for practical amounts.
- **Does the vault start paused?** Yes, `initialize()` passes `true` for `paused_`. This means the admin must unpause, and presumably should seed the vault with initial liquidity first. However, there is no enforced minimum seed deposit, and the vault could be unpaused before any deposit occurs.
- **Is `processAccounting()` really permissionless?** Yes, line 933: `function processAccounting() public virtual nonReentrant`. No role check.
- **Could the vault start with `alwaysComputeTotalAssets == false`?** Yes, this is a constructor parameter. In cached mode, the attacker must call `processAccounting()` after the donation, which is permissionless.

#### Verdict: VULNERABILITY CONFIRMED

```json
{
  "title": "Donation-based share price inflation enables first-depositor theft",
  "severity": "HIGH",
  "confidence": "Likely",
  "source": "manual",
  "category": "ERC-4626 Share Inflation / Donation Attack",
  "affected_files": ["src/src/library/VaultLib.sol"],
  "affected_lines": {"start": 285, "end": 313},
  "description": "The vault's share-to-asset conversion uses a minimal +1 virtual offset (totalAssets + 1, totalSupply + 1) instead of OpenZeppelin's standard decimals-based virtual offset. Combined with computeTotalAssets() reading raw balanceOf() values, an attacker can donate tokens directly to the vault to inflate the share price. The first depositor can deposit 1 wei, donate a large amount, and cause subsequent depositors to receive 0 shares due to integer division truncation. The permissionless processAccounting() function allows anyone to update the cached totalAssets after a donation.",
  "impact": "A first depositor can steal the entire deposit of subsequent users by inflating the share price through direct token donations.",
  "remediation": "Implement OpenZeppelin's _decimalsOffset() pattern (virtual shares of 10^decimals) or enforce a minimum initial deposit that is permanently locked (dead shares). Alternatively, use internal accounting rather than balanceOf() for totalAssets computation.",
  "attack_scenario": "1. Attacker deposits 1 wei, receives 1 share. 2. Attacker transfers 1000e18 tokens directly to vault. 3. Attacker calls processAccounting() if in cached mode. 4. Victim deposits 500e18, receives 0 shares due to integer truncation. 5. Attacker redeems 1 share for ~1500e18.",
  "evidence_sources": [
    {
      "type": "checklist",
      "detail": "ERC-4626 first depositor / share inflation attack pattern"
    }
  ]
}
```

---

### Attack 2: Caller-dependent preview functions break ERC4626 composability (Spot 3)

#### Call path trace

`previewWithdraw()` (BaseVault.sol:196-199):
```solidity
function previewWithdraw(uint256 assets) public view virtual returns (uint256 shares) {
    uint256 fee = _feeOnRaw(assets, _msgSender());
    (shares,) = _convertToShares(asset(), assets + fee, Math.Rounding.Ceil);
}
```

`previewRedeem()` (BaseVault.sol:207-210):
```solidity
function previewRedeem(uint256 shares) public view virtual returns (uint256 assets) {
    (assets,) = _convertToAssets(asset(), shares, Math.Rounding.Floor);
    return assets - _feeOnTotal(assets, _msgSender());
}
```

Both call `_feeOnRaw`/`_feeOnTotal` with `_msgSender()`, which looks up whether `msg.sender` has a fee override.

#### Attack narrative

**Attacker role**: N/A (this is a compliance/integration issue).

**Scenario**: A router contract calls `previewWithdraw(1000)` to determine how many shares to approve for a withdrawal. The router has no fee override, so it gets charged the full fee. But the actual user who initiated the transaction might have a fee override (exempt or lower). The router over-estimates the shares needed. Conversely, if a fee-exempt address calls `previewWithdraw` as part of a UI flow but then the actual `withdraw` is called by a different contract, the fee calculation diverges.

**Broken invariant**: ERC4626 specification compliance.

#### Devil's advocate protocol

- **Is this by design?** The fee system intentionally charges per-user fees. However, ERC4626 `previewWithdraw` is explicitly required to be "inclusive of deposit fees" and "MUST NOT account for... withdrawal fees that are charged against a specific user." The spec says preview functions should be caller-independent.
- **Does this cause loss of funds?** Not directly. It causes integration issues and incorrect estimates for third-party contracts.
- **Is `_msgSender()` standard?** Yes, it is the OpenZeppelin context pattern. But using it in a view function that ERC4626 specifies should be caller-independent is a design mismatch.

#### Verdict: VULNERABILITY CONFIRMED

```json
{
  "title": "Preview functions are caller-dependent, violating ERC4626 specification",
  "severity": "MEDIUM",
  "confidence": "Confirmed",
  "source": "manual",
  "category": "ERC-4626 Compliance",
  "affected_files": ["src/src/BaseVault.sol"],
  "affected_lines": {"start": 196, "end": 210},
  "description": "previewWithdraw() and previewRedeem() use _msgSender() to determine the withdrawal fee for the caller. ERC4626 specification (EIP-4626) states that preview functions MUST return values independent of the caller. Per-user withdrawal fee overrides mean that the same (assets) input returns different (shares) outputs depending on who calls the function, breaking composability with routers, aggregators, and other integrating contracts.",
  "impact": "Third-party contracts (DEX aggregators, routers, yield optimisers) that call preview functions to estimate amounts will get incorrect results, leading to failed transactions or suboptimal execution.",
  "remediation": "Override previewWithdraw() and previewRedeem() to use the global base withdrawal fee (worst case) rather than the per-user fee. Alternatively, provide separate user-aware preview functions (e.g., previewWithdrawFor(assets, user)) and keep the standard ERC4626 functions caller-independent.",
  "evidence_sources": [
    {
      "type": "checklist",
      "detail": "ERC-4626 compliance: preview functions must be caller-independent per EIP-4626"
    }
  ]
}
```

---

### Attack 3: Guard parameter decoding bypass for functions with dynamic types (Spot 10)

#### Call path trace

`Guard.validateCall()` (Guard.sol:9-29):
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
    }
}
```

The Guard decodes parameter `i` at byte offset `4 + i * 32` from the calldata. This is correct for functions where all parameters are fixed-size ABI types (uint256, address, bool, bytesN). However, for functions with dynamic types (bytes, string, arrays), ABI encoding places an offset pointer at the expected position, not the actual value.

#### Attack narrative

**Attacker role**: PROCESSOR_ROLE holder (privileged).

**Call sequence**:
1. Suppose a processor rule is set for function `transfer(address,uint256)` on a token contract, with paramRule[0] having an allowlist of approved recipient addresses.
2. This works correctly because `transfer` has only fixed-size params.
3. Now suppose a rule is set for a function like `multicall(bytes[])` where paramRule[0] is type ADDRESS. The Guard would decode `data[4:36]` as an address, but in ABI encoding of `bytes[]`, `data[4:36]` is the offset to the array data (e.g., `0x20`), not an actual address.
4. If `0x0000...0020` happens to be in the allowlist (unlikely), or if no address rule is set for this param index, the call passes through.

#### Devil's advocate protocol

- **Is PROCESSOR_ROLE privileged?** Yes. Per the methodology, privileged roles are assumed honest. This finding only matters if the PROCESSOR_MANAGER sets rules for functions with dynamic types, which would be a misconfiguration.
- **Does the Guard handle this case?** The Guard only processes `ParamType.ADDRESS` rules. If a rule has `ParamType.UINT256`, it is entirely skipped (no validation at all, line 23-28 only handles ADDRESS). So UINT256 parameters are never validated regardless.
- **Could this be used to bypass allowlists?** Only if rules are misconfigured for functions with dynamic params. For standard ERC20 functions (transfer, approve), the decoding is correct.

#### Verdict: VULNERABILITY CONFIRMED (with caveats)

```json
{
  "title": "Guard parameter validation only handles fixed-size ABI encoding and skips UINT256 params",
  "severity": "MEDIUM",
  "confidence": "Possible",
  "source": "manual",
  "category": "Access Control / Input Validation",
  "affected_files": ["src/src/module/Guard.sol"],
  "affected_lines": {"start": 9, "end": 29},
  "description": "Guard.validateCall() decodes parameters at fixed offsets (data[4 + i * 32]) which only works for functions with exclusively fixed-size ABI parameters. For functions with dynamic types (bytes, string, arrays), the ABI encoding places offset pointers at these positions, not actual values. Additionally, UINT256-typed param rules are completely skipped -- there is no validation branch for them. This means: (1) if rules are set for functions with dynamic params, address validation may check offset pointers instead of actual addresses; (2) UINT256 parameters are never validated regardless of the rule configuration.",
  "impact": "If PROCESSOR_MANAGER_ROLE incorrectly configures rules for functions with dynamic parameters, the Guard's address allowlist checks can be bypassed. UINT256 parameter constraints cannot be enforced at all.",
  "remediation": "Document that Guard rules are only valid for functions with fixed-size parameters. Add validation for UINT256 param type. Consider supporting dynamic type offset resolution or requiring IValidator for functions with dynamic params.",
  "evidence_sources": [
    {
      "type": "checklist",
      "detail": "Input validation: ABI encoding of dynamic types uses offsets, not inline values"
    }
  ]
}
```

---

### Attack 4: Permissionless `processAccounting()` enables donation-based accounting manipulation (Spot 6)

This is closely related to Attack 1 but focuses on the cached-mode scenario.

#### Call path trace

`processAccounting()` (VaultLib.sol:394-432):
```solidity
function processAccounting() public {
    // ...
    uint256 totalBaseAssetsAfterAccounting = computeTotalAssets();
    vaultStorage.totalAssets = totalBaseAssetsAfterAccounting;
    // ...
}
```

`computeTotalAssets()` reads `balanceOf(address(this))` for each asset.

#### Attack narrative

**Attacker role**: Unprivileged user or MEV bot.

**Call sequence** (cached mode, `alwaysComputeTotalAssets == false`):
1. Vault has 1000e18 base assets, 1000e18 shares outstanding (1:1 rate).
2. Attacker flash-loans 10000e18 of the base asset token.
3. Attacker transfers 10000e18 directly to the vault (no deposit, so no shares minted).
4. Attacker calls `processAccounting()`. Cached `totalAssets` updates to 11000e18.
5. Attacker deposits 1000e18 via `deposit()`. `convertToShares`: `1000e18 * (1000e18 + 1) / (11000e18 + 1)` ~ 90.9e18 shares.
6. Attacker then retrieves the donated 10000e18 somehow...

**Devil's advocate**: The attacker donated 10000e18 but that amount is now part of the vault's accounting. The attacker cannot retrieve the donated funds without shares. They only have ~90.9e18 shares representing ~1000e18. The donated funds benefit all existing shareholders. The attacker loses money on the donation.

However, the attack works in the first-depositor scenario (Attack 1) because the attacker IS the only shareholder. In multi-depositor scenarios, the permissionless `processAccounting()` does not directly enable profitable exploitation beyond the first-depositor case.

One exception: if `processAccounting()` is called BEFORE a large withdrawal, and the attacker has previously donated tokens, the inflated totalAssets means the withdrawer gets more assets per share than expected, potentially draining the buffer.

#### Verdict: VULNERABILITY CONFIRMED (as amplifier for Attack 1)

```json
{
  "title": "Permissionless processAccounting() amplifies donation-based share price manipulation",
  "severity": "MEDIUM",
  "confidence": "Likely",
  "source": "manual",
  "category": "Donation Attack / Access Control",
  "affected_files": ["src/src/library/VaultLib.sol", "src/src/BaseVault.sol"],
  "affected_lines": {"start": 394, "end": 432},
  "description": "processAccounting() is a public function with no access control (only nonReentrant). It updates the cached totalAssets by reading raw balanceOf() for all vault assets. When the vault is in cached mode (alwaysComputeTotalAssets == false), an attacker can donate tokens to the vault and then call processAccounting() to update the cached totalAssets to an inflated value. This amplifies the first-depositor attack (Attack 1) and can be used to manipulate share prices between accounting updates.",
  "impact": "Amplifies first-depositor share inflation attack. In cached mode, allows precise timing of accounting updates to coincide with donations for share price manipulation.",
  "remediation": "Add access control to processAccounting() (e.g., require a KEEPER_ROLE), or switch to internal accounting that tracks deposited amounts rather than reading raw balances.",
  "evidence_sources": [
    {
      "type": "checklist",
      "detail": "Permissionless state-modifying functions should be checked for manipulation vectors"
    }
  ]
}
```

---

### Attack 5: `withdrawAsset()` does not charge withdrawal fees (Spot 4)

#### Call path trace

`withdrawAsset()` (BaseVault.sol:613-629):
```solidity
function withdrawAsset(address asset_, uint256 assets, address receiver, address owner)
    public virtual onlyRole(ASSET_WITHDRAWER_ROLE)
    returns (uint256 shares)
{
    // ...
    (shares,) = _convertToShares(asset_, assets, Math.Rounding.Ceil);
    // ... balance checks ...
    _withdrawAsset(asset_, _msgSender(), receiver, owner, assets, shares);
}
```

`_withdrawAsset()` (BaseVault.sol:640-664):
```solidity
function _withdrawAsset(...) internal virtual {
    if (!hasAsset(asset_)) revert InvalidAsset(asset_);
    _subTotalAssets(_convertAssetToBase(asset_, assets, Math.Rounding.Floor));
    if (caller != owner) { _spendAllowance(owner, caller, shares); }
    _burn(owner, shares);
    SafeERC20.safeTransfer(IERC20(asset_), receiver, assets);
    // ...
}
```

No fee is charged. The shares burned equals the raw asset-to-share conversion without any fee addition.

#### Devil's advocate protocol

- **Is this intentional?** Likely yes. The `ASSET_WITHDRAWER_ROLE` is a privileged role, and the function is designed for specific asset management operations (e.g., rebalancing, emergency withdrawals). Fee exemption for privileged roles is a common design choice.
- **Per methodology, privileged roles are assumed honest.** This means we should not flag this as a vulnerability if it requires a privileged role to act against the protocol's interest.
- **Could an unprivileged user exploit this?** No. The `onlyRole(ASSET_WITHDRAWER_ROLE)` modifier prevents unprivileged access.

#### Verdict: NO VULNERABILITY

**Reason**: This is a design choice for privileged asset management operations. The `ASSET_WITHDRAWER_ROLE` is intended to have special capabilities. Per the methodology's Core Protocol #5 (Privileged Roles Are Honest), this is not a finding.

**Confidence**: High that this is safe (given honest privileged roles).

---

### Attack 6: `mintShares()` trust in hooks contract (Spot 9)

#### Call path trace

`mintShares()` (BaseVault.sol:970-976):
```solidity
function mintShares(address recipient, uint256 shares) external {
    if (msg.sender != address(hooks())) {
        revert CallerNotHooks();
    }
    _mint(recipient, shares);
}
```

#### Devil's advocate protocol

- **Is the hooks contract set by a privileged role?** Yes, `HOOKS_MANAGER_ROLE`.
- **Does `setHooks()` validate the hooks contract?** Line 983: `if (hooks_ != address(0) && address(IHooks(hooks_).VAULT()) != address(this)) revert InvalidHooks();` -- it checks that the hooks contract's VAULT() returns this vault's address. This is a minimal validation.
- **Could a malicious hooks contract be set?** Only by HOOKS_MANAGER_ROLE (privileged, assumed honest).
- **Could a bug in a legitimate hooks contract be exploited?** Yes, but the hooks contract is out of scope. The vault's trust assumption on the hooks contract is architectural.

#### Verdict: NO VULNERABILITY (trust assumption)

**Reason**: The hooks contract is set by a privileged role and validated to reference the correct vault. While the hooks contract has unconstrained `mintShares()` capability, exploiting this requires either a malicious HOOKS_MANAGER or a vulnerability in the hooks contract itself (out of scope).

**Confidence**: Medium (depends on hooks contract security, which is not in scope).

**Note**: This is documented as an **informational finding** -- the vault delegates unlimited share minting authority to the hooks contract with no cap or rate limit.

---

### Attack 7: Hooks called via low-level `.call()` -- failure handling (Spot 8 extension)

#### Call path trace

`HooksLib.callHook()` (HooksLib.sol:53-57):
```solidity
function callHook(IHooks self, bytes memory data) internal returns (bytes memory) {
    (bool success, bytes memory result) = address(self).call(data);
    if (!success) revert HookCallFailed(result);
    return result;
}
```

Hooks use low-level `.call()` which forwards all available gas. If the hooks contract is gas-intensive or reverts, the entire transaction reverts. This is correct behaviour (the hook failing should revert the operation).

#### Devil's advocate

- **Can the hooks contract cause a DoS?** If a hooks contract always reverts in `beforeDeposit`, all deposits would fail. But this requires the HOOKS_MANAGER to set a broken hooks contract (privileged role, assumed honest).
- **Is there a gas griefing vector?** The hooks contract could consume excessive gas, but this is the hooks manager's responsibility.

#### Verdict: NO VULNERABILITY

**Reason**: Hook failure propagation is correct. DoS via hooks requires privileged role misconfiguration.

**Confidence**: High.

---

## Findings summary

| # | Severity | Confidence | Title | File(s) |
|---|----------|------------|-------|---------|
| 1 | HIGH | Likely | Donation-based share price inflation enables first-depositor theft | VaultLib.sol:285-313 |
| 2 | MEDIUM | Confirmed | Preview functions are caller-dependent, violating ERC4626 specification | BaseVault.sol:196-210 |
| 3 | MEDIUM | Possible | Guard parameter validation only handles fixed-size ABI encoding and skips UINT256 params | Guard.sol:9-29 |
| 4 | MEDIUM | Likely | Permissionless processAccounting() amplifies donation-based share price manipulation | VaultLib.sol:394-432 |
| 5 | INFORMATIONAL | Confirmed | Hooks contract has unbounded mintShares() authority | BaseVault.sol:970-976 |
| 6 | INFORMATIONAL | Confirmed | `_feeOnRaw` and `_feeOnTotal` exposed as public despite underscore naming convention | Vault.sol:68-82, BaseVault.sol:1021-1029 |

### Findings not confirmed (dismissed)

| Spot | Reason for dismissal |
|------|---------------------|
| Spot 4 (withdrawAsset no fees) | By design -- privileged role (ASSET_WITHDRAWER_ROLE). Privileged roles assumed honest. |
| Spot 8 (_withdraw state ordering) | nonReentrant guard prevents re-entry. State changes before external calls is not ideal but is protected. |
| Spot 9 (mintShares trust) | Elevated to informational. Hooks set by privileged role with basic validation. |

---

## Detailed findings

### Finding 1 -- HIGH: Donation-based share price inflation enables first-depositor theft

**File**: `src/src/library/VaultLib.sol`, lines 285-313 (convertToAssets/convertToShares) and lines 374-389 (computeTotalAssets)

**Description**: The vault uses `balanceOf(address(this))` to compute total assets and employs a minimal `+1` virtual offset in share conversion formulae. This combination enables the classic ERC-4626 first-depositor share inflation attack. An attacker deposits 1 wei of the base asset, then donates a large amount of tokens directly to the vault contract. This inflates the share price so that subsequent depositors receive 0 shares due to integer division truncation, losing their entire deposit to the attacker.

The `+1` offset in `shares.mulDiv(totalAssets + 1, totalSupply + 1, rounding)` provides negligible protection (1 wei) compared to OpenZeppelin's standard `10^decimalsOffset()` approach.

**Attack scenario**:
1. Attacker deposits 1 wei, receives 1 share.
2. Attacker transfers 1000e18 tokens directly to vault (bypassing deposit).
3. If cached mode: attacker calls permissionless `processAccounting()`.
4. Victim deposits 500e18, receives 0 shares (truncated to 0).
5. Attacker redeems 1 share for ~1500e18 total.

**Remediation**: Implement OpenZeppelin's `_decimalsOffset()` virtual share pattern with a sufficient offset (e.g., 1e6 or higher). Alternatively, enforce a minimum initial deposit that permanently locks "dead shares" to prevent the ratio from being manipulated. Consider using internal accounting (tracking deposits and withdrawals) rather than `balanceOf()` for `computeTotalAssets()`.

---

### Finding 2 -- MEDIUM: Preview functions are caller-dependent, violating ERC4626 specification

**File**: `src/src/BaseVault.sol`, lines 196-210

**Description**: `previewWithdraw()` and `previewRedeem()` call `_feeOnRaw(assets, _msgSender())` and `_feeOnTotal(assets, _msgSender())` respectively, using `_msgSender()` to determine the per-user withdrawal fee. ERC-4626 (EIP-4626) specifies that preview functions "MUST NOT account for withdrawal fees that are charged against a specific user, and instead SHOULD account for the worst-case scenario of fees." This means preview functions should return caller-independent results.

**Impact**: Third-party contracts (DEX aggregators, yield optimisers, routers) calling these preview functions will receive caller-specific results rather than the expected worst-case estimates, leading to failed transactions, incorrect slippage calculations, or integration failures.

**Remediation**: Modify `previewWithdraw()` and `previewRedeem()` to use the global `baseWithdrawalFee` (worst case) instead of the per-user fee. Provide separate user-aware functions (e.g., `previewWithdrawFor(uint256 assets, address user)`) for UIs that need user-specific estimates.

---

### Finding 3 -- MEDIUM: Guard parameter validation only handles fixed-size ABI encoding and skips UINT256 params

**File**: `src/src/module/Guard.sol`, lines 9-29

**Description**: The Guard's `validateCall()` function decodes parameters at fixed byte offsets (`data[4 + i * 32]`). This correctly decodes functions with only fixed-size ABI parameters (address, uint256, bool) but produces incorrect results for functions with dynamic types (bytes, string, arrays) where ABI encoding uses offset pointers at these positions. Additionally, the validation loop only processes `ParamType.ADDRESS` rules; `ParamType.UINT256` rules are defined in the enum but never validated (there is no handling branch for UINT256 in the loop).

**Impact**: If processor rules are configured for functions with dynamic parameters, address allowlist checks may validate the ABI offset pointer value instead of the actual address, potentially allowing unauthorised target addresses. UINT256 parameter constraints cannot be enforced at all, regardless of rule configuration.

**Remediation**: Document that Guard rules are only valid for functions with exclusively fixed-size parameters. Add validation logic for `ParamType.UINT256`. For functions with dynamic parameters, require an `IValidator` contract that can correctly decode the calldata. Consider adding a check that reverts if param rules are set for positions beyond the function's fixed-param count.

---

### Finding 4 -- MEDIUM: Permissionless processAccounting() amplifies donation-based share price manipulation

**File**: `src/src/library/VaultLib.sol`, lines 394-432; `src/src/BaseVault.sol`, line 933

**Description**: `processAccounting()` is a public function with no access control (only `nonReentrant`). It updates the cached `totalAssets` by reading raw `balanceOf()` for all vault assets. When the vault is in cached mode (`alwaysComputeTotalAssets == false`), an attacker can: (1) donate tokens directly to the vault, (2) immediately call `processAccounting()` to update the cached total to an inflated value, and (3) exploit the inflated share price. This amplifies Finding 1 in cached mode and allows precise timing control over accounting updates.

**Impact**: Amplifies the first-depositor share inflation attack in cached mode. Allows MEV bots to time accounting updates to coincide with donations or large deposits/withdrawals for share price manipulation.

**Remediation**: Add access control to `processAccounting()` (e.g., require a `KEEPER_ROLE` or `ACCOUNTING_ROLE`). Alternatively, implement internal accounting that tracks deposited/withdrawn amounts rather than reading raw on-chain balances, making the function immune to donation manipulation.

---

### Finding 5 -- INFORMATIONAL: Hooks contract has unbounded mintShares() authority

**File**: `src/src/BaseVault.sol`, lines 970-976

**Description**: The `mintShares()` function allows the hooks contract to mint an arbitrary number of shares to any recipient with no cap, rate limit, or time constraint. While the hooks contract is set by the privileged `HOOKS_MANAGER_ROLE` and validated to reference the correct vault, a vulnerability in the hooks contract itself could allow an unprivileged attacker to trigger unbounded share minting, leading to share dilution and effective theft of all vault assets.

**Recommendation**: Consider adding a per-call or per-epoch cap on shares mintable through `mintShares()`. Document the trust assumption clearly: the vault delegates unlimited minting authority to the hooks contract.

---

### Finding 6 -- INFORMATIONAL: `_feeOnRaw` and `_feeOnTotal` exposed as public despite underscore naming convention

**File**: `src/Vault.sol`, lines 68-82; `src/src/BaseVault.sol`, lines 1021-1029

**Description**: Functions `_feeOnRaw()` and `_feeOnTotal()` are declared `public view` despite the leading underscore convention indicating internal/private visibility. They are also part of the `IVault` interface. While not a security vulnerability, this violates Solidity naming conventions and may confuse auditors and integrators about the intended access level of these functions.

**Recommendation**: Either rename to `feeOnRaw()`/`feeOnTotal()` (removing underscore) or change visibility to `internal` (which would require removing them from the interface and adjusting callers).

---

## Appendix: Static analysis tool output

### Slither

Both Slither and Aderyn failed to compile the source due to incomplete OpenZeppelin library resolution. The source files were extracted from Blockscout and use `@openzeppelin/contracts/` import paths within the bundled upgradeable contracts, but the flat directory layout does not include the required `@openzeppelin` remapping targets as separate directories.

**Error**: `Source "@openzeppelin/contracts/access/IAccessControl.sol" not found`

### Aderyn

Same compilation failure as Slither.

**Error**: `Source "@openzeppelin/contracts/access/IAccessControl.sol" not found`

---

## Methodology notes

This audit followed the Archethect sc-auditor **Map-Hunt-Attack** methodology:

1. **SETUP**: Attempted Slither and Aderyn static analysis (both failed due to compilation issues). Loaded methodology and risk patterns.
2. **MAP**: Read all 12 custom source files. Built system architecture, identified 10 invariants, and documented all components with their roles, state, and external surfaces.
3. **HUNT**: Identified 10 suspicious spots across the codebase using the 9 risk patterns, manual review, and invariant checking.
4. **ATTACK**: Deep-dived 7 spots with concrete attack narratives, devil's advocate falsification attempts, and evidence-based verdicts. Confirmed 4 vulnerabilities and 2 informational findings. Dismissed 3 spots with documented reasoning.

**Core protocols applied**:
- Hypothesis-driven analysis: each spot was treated as a hypothesis to falsify.
- Devil's advocate: actively searched for constraints that would prevent each attack.
- Evidence required: all confirmed findings cite specific lines and code paths.
- Privileged roles assumed honest: dismissed findings requiring malicious admin actions.
