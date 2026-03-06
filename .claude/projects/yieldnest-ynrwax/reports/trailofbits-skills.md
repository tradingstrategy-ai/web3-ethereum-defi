# YieldNest ynRWAx vault security audit

**Methodology**: Trail of Bits skills -- building-secure-contracts vulnerability scanner, entry-point-analyzer, fp-check, guidelines-advisor, token-integration-analyzer

**Target**: YieldNest ynRWAx Vault v0.4.2

**Scope**: All custom Solidity source files (excluding OpenZeppelin library dependencies)

| File | Lines | Description |
|------|-------|-------------|
| `Vault.sol` | 109 | Main vault contract, inherits BaseVault + LinearWithdrawalFee |
| `src/BaseVault.sol` | 1031 | Core ERC4626 vault with multi-asset, hooks, processor |
| `src/library/VaultLib.sol` | 459 | Storage, conversions, accounting, processor execution |
| `src/library/HooksLib.sol` | 179 | Hook dispatch library |
| `src/library/LinearWithdrawalFeeLib.sol` | 97 | Fee calculation library |
| `src/module/Guard.sol` | 47 | Call validation for processor |
| `src/module/FeeMath.sol` | 42 | Basis point fee maths |
| `src/module/LinearWithdrawalFee.sol` | 84 | Fee management abstract contract |
| `src/interface/IVault.sol` | 183 | Vault interface and types |
| `src/interface/IHooks.sol` | 183 | Hooks interface |
| `src/interface/IStrategy.sol` | 22 | Strategy interface |
| `src/interface/IProvider.sol` | 7 | Rate provider interface |
| `src/interface/IValidator.sol` | 13 | Validator interface |

**Compiler**: Solidity ^0.8.24, EVM target: Cancun, optimiser enabled (200 runs)

**Architecture**: Upgradeable proxy (TransparentUpgradeableProxy), libraries deployed externally (VaultLib, HooksLib, LinearWithdrawalFeeLib linked at deployment), ERC7201 namespaced storage pattern.

---

## Findings summary

| ID | Severity | Title |
|----|----------|-------|
| H-01 | HIGH | Processor can execute arbitrary external calls with vault's full token balances |
| H-02 | HIGH | Hooks contract can mint unbounded shares via `mintShares` |
| M-01 | MEDIUM | Guard parameter validation only checks ADDRESS type, ignoring UINT256 parameters |
| M-02 | MEDIUM | `processAccounting()` is publicly callable and can manipulate share price |
| M-03 | MEDIUM | Deposit updates `totalAssets` before transferring tokens -- state inconsistency window |
| M-04 | MEDIUM | `withdrawAsset` lacks withdrawal fee deduction |
| M-05 | MEDIUM | `_feeOnRaw` and `_feeOnTotal` are declared `public view` -- naming convention violates Solidity visibility expectations |
| L-01 | LOW | Storage slot comments in VaultLib are incorrect and misleading |
| L-02 | LOW | ProcessorStorage comment claims `keccak256("yieldnest.storage.vault")` -- same string as VaultStorage |
| L-03 | LOW | `deleteAsset` swap-and-pop can silently break external integrations relying on asset indices |
| L-04 | LOW | `addAsset` duplicate check is incomplete for assets added at index 0 then re-added |
| L-05 | LOW | No maximum cap on number of assets -- unbounded loop in `computeTotalAssets` |
| L-06 | LOW | `receive()` accepts native ETH without restriction but only counts it if `countNativeAsset` is true |
| L-07 | LOW | Fee manager can set per-user fee override to 100% (1e8), effectively blocking withdrawals |
| I-01 | INFO | ERC4626 `deposit` and `mint` do not check for zero shares minted |
| I-02 | INFO | `convertToShares` / `convertToAssets` use virtual total supply offset of +1, differing from OpenZeppelin's standard ERC4626 |
| I-03 | INFO | External library deployment creates tight coupling between proxy and library addresses |
| I-04 | INFO | No event emitted when `mintShares` is called by hooks |

---

## Detailed findings

### H-01: Processor can execute arbitrary external calls with vault's full token balances

**Severity**: HIGH

**Affected file and lines**: `src/library/VaultLib.sol:441-458`, `src/module/Guard.sol:9-29`

**Description**:

The `processor()` function allows the `PROCESSOR_ROLE` holder to execute arbitrary external calls from the vault contract's context using low-level `.call{value: values[i]}(data[i])`. While the `Guard.validateCall()` function checks that a rule exists and is active for the target/function combination, the Guard's parameter validation has significant limitations:

1. The Guard only validates parameters of type `ADDRESS` (line 23-28 of Guard.sol). Parameters of type `UINT256` are completely ignored -- there is no validation logic for them. This means a processor could call any approved function with any uint256 amount, including the entire vault balance.

2. If a `validator` contract is set in the rule, all validation is delegated to that external contract (line 17-19). A compromised or malicious validator could approve any call.

3. The Guard decodes parameters at fixed 32-byte ABI offsets (`data[4 + i * 32:]`), which does not handle dynamic types (bytes, arrays, strings) correctly. A function with a dynamic parameter before an address parameter would cause the address validation to read from the wrong offset.

**Attack scenario**:

A compromised `PROCESSOR_ROLE` account could:
1. Call any whitelisted target with any amount parameter (since UINT256 is not validated)
2. Drain all approved tokens from the vault via an approved `transfer(address, uint256)` function where only the address is checked but the amount is not

**Recommendation**:

- Implement validation for `UINT256` parameters (e.g. max value bounds)
- Consider adding a maximum value parameter to function rules
- Handle dynamic ABI-encoded parameters correctly, or restrict processor rules to functions with only static parameter types
- Consider adding a timelock or multi-sig requirement for processor calls

---

### H-02: Hooks contract can mint unbounded shares via `mintShares`

**Severity**: HIGH

**Affected file and lines**: `src/BaseVault.sol:970-976`

**Description**:

The `mintShares()` function allows the hooks contract to mint an arbitrary number of shares to any recipient. The only access control is `msg.sender != address(hooks())`. There is no cap, rate limit, or accounting update when shares are minted through this path.

Critically, `mintShares` does NOT call `_addTotalAssets()`, so minting shares through this function dilutes all existing shareholders without increasing the vault's recorded total assets. This directly reduces the share price for all holders.

The `setHooks()` function is protected by `HOOKS_MANAGER_ROLE` and validates that the hooks contract's `VAULT()` returns the vault address. However, this is a weak check -- any contract implementing `VAULT()` correctly can be set as hooks.

**Attack scenario**:

1. A malicious or compromised hooks manager sets a custom hooks contract
2. The hooks contract calls `mintShares(attacker, large_amount)` during any hook callback (e.g. `afterDeposit`)
3. The attacker receives shares without depositing assets, diluting all other shareholders
4. The attacker redeems the minted shares for real assets from the buffer

**Recommendation**:

- Restrict `mintShares` to only allow minting within the context of a specific operation (e.g. performance fee collection)
- Add an accounting update (`_addTotalAssets`) or require a corresponding deposit
- Consider a maximum mint cap per operation
- Add event emission for transparency and monitoring

---

### M-01: Guard parameter validation only checks ADDRESS type, ignoring UINT256 parameters

**Severity**: MEDIUM

**Affected file and lines**: `src/module/Guard.sol:22-28`

**Description**:

The `validateCall` function iterates through `rule.paramRules` but only has validation logic for `ParamType.ADDRESS`. When a parameter rule has `paramType == ParamType.UINT256`, the loop body hits the `if` check on line 23, fails the condition, and simply continues to the next iteration without performing any validation.

```solidity
for (uint256 i = 0; i < rule.paramRules.length; i++) {
    if (rule.paramRules[i].paramType == IVault.ParamType.ADDRESS) {
        address addressValue = abi.decode(data[4 + i * 32:], (address));
        _validateAddress(addressValue, rule.paramRules[i]);
        continue;
    }
    // UINT256 parameters fall through here with NO validation
}
```

This means that even if a UINT256 parameter rule is configured, it will never be enforced.

**Recommendation**:

Implement UINT256 validation (e.g. minimum/maximum bounds checking) or explicitly revert if an unsupported param type is encountered.

---

### M-02: `processAccounting()` is publicly callable and can manipulate share price

**Severity**: MEDIUM

**Affected file and lines**: `src/BaseVault.sol:933-934`, `src/library/VaultLib.sol:394-432`

**Description**:

The `processAccounting()` function is `public` with only a `nonReentrant` modifier -- it has no access control. Anyone can call it at any time to update the cached `totalAssets` value.

When `alwaysComputeTotalAssets` is `false`, the vault relies on the cached `totalAssets` value for share price calculations. The `processAccounting()` function recomputes this by iterating through all assets and querying their balances and rates via the provider.

An attacker could front-run a large deposit by calling `processAccounting()` at a moment when the provider reports a temporarily unfavourable rate, or immediately after a large withdrawal from the buffer that has not yet been reflected. This could cause the cached total assets to be lower than the true value, allowing the attacker to mint more shares than deserved.

Additionally, the hooks system is invoked during processAccounting (`beforeProcessAccounting` and `afterProcessAccounting`), meaning an external hooks contract receives control flow during this sensitive accounting update.

**Recommendation**:

- Consider restricting `processAccounting()` to a specific role (e.g. `PROCESSOR_ROLE`)
- Or implement a minimum time interval between processAccounting calls
- Document the expected call cadence and implications of stale cached values

---

### M-03: Deposit updates `totalAssets` before transferring tokens -- state inconsistency window

**Severity**: MEDIUM

**Affected file and lines**: `src/BaseVault.sol:535-557`

**Description**:

In the `_deposit` function, the accounting update `_addTotalAssets(baseAssets)` occurs on line 547 before the actual token transfer on line 549:

```solidity
function _deposit(...) internal virtual {
    if (!_getAssetStorage().assets[asset_].active) {
        revert AssetNotActive();
    }

    _addTotalAssets(baseAssets);      // Accounting updated FIRST

    SafeERC20.safeTransferFrom(IERC20(asset_), caller, address(this), assets);  // Transfer SECOND
    _mint(receiver, shares);           // Mint THIRD
    ...
}
```

If the `safeTransferFrom` call fails (e.g. insufficient allowance or balance), the transaction reverts entirely, so there is no permanent state inconsistency. However, this ordering creates a window where:

1. Hook callbacks (`beforeDeposit`) execute before both the accounting update and the transfer
2. If any external call occurs between the accounting update and the transfer (via hooks), the vault's `totalAssets` is inflated relative to actual held assets

While the `nonReentrant` modifier on the public `deposit()` function prevents direct reentrancy, the hooks system introduces external calls that could observe the inconsistent state.

**Recommendation**:

Move `_addTotalAssets(baseAssets)` after the `safeTransferFrom` to follow the checks-effects-interactions pattern more strictly. The `_mint` call is safe as it is internal.

---

### M-04: `withdrawAsset` lacks withdrawal fee deduction

**Severity**: MEDIUM

**Affected file and lines**: `src/BaseVault.sol:613-629`

**Description**:

The `withdrawAsset` function (restricted to `ASSET_WITHDRAWER_ROLE`) converts assets to shares using `_convertToShares` with `Ceil` rounding but does NOT apply any withdrawal fee. Compare with the standard `withdraw()` function which uses `previewWithdraw()` that adds `_feeOnRaw(assets, _msgSender())` to the withdrawal amount before converting to shares.

This means the `ASSET_WITHDRAWER_ROLE` holder can withdraw any supported asset without paying the configured withdrawal fee. While this may be intentional (the role is privileged), it creates an asymmetry: the fee can be circumvented by any address granted this role.

The function also does not check against `maxWithdraw` or go through the buffer strategy -- it transfers tokens directly from the vault's balance. This bypasses the buffer-based withdrawal flow entirely.

**Recommendation**:

- If fee-free withdrawal is intentional for this role, add explicit documentation (NatSpec) stating this design decision
- If fees should apply, add `_feeOnRaw` to the shares calculation as in `previewWithdraw`
- Consider whether bypassing the buffer strategy is the intended behaviour

---

### M-05: `_feeOnRaw` and `_feeOnTotal` are declared `public view` with underscore prefix

**Severity**: MEDIUM

**Affected file and lines**: `src/BaseVault.sol:1021-1029`, `Vault.sol:68-82`, `src/interface/IVault.sol:180-181`

**Description**:

The functions `_feeOnRaw` and `_feeOnTotal` are declared with an underscore prefix (conventionally indicating internal/private visibility in Solidity) but have `public` visibility. They are also defined in the `IVault` interface, making them part of the external ABI.

This violates the widely-accepted Solidity naming convention where underscore-prefixed functions are internal. External integrators or auditors may incorrectly assume these functions are internal and not accessible externally, leading to misunderstandings about the attack surface.

More importantly, this means the fee calculation logic is exposed as part of the contract's public interface, which could be used by attackers to compute optimal withdrawal timing or amounts to minimise fees paid.

**Recommendation**:

Either rename the functions to remove the underscore prefix (e.g. `feeOnRaw`, `feeOnTotal`) to match their public visibility, or change them to `internal` and create separate public wrappers.

---

### L-01: Storage slot comments in VaultLib are incorrect and misleading

**Severity**: LOW

**Affected file and lines**: `src/library/VaultLib.sol:28-86`

**Description**:

The inline comments above each storage slot assembly block claim the slot is `keccak256("yieldnest.storage.X")`, but the actual hexadecimal values do not match any standard derivation from these strings -- neither plain `keccak256` nor the ERC7201 pattern (`keccak256(abi.encode(uint256(keccak256(id)) - 1)) & ~bytes32(uint256(0xff))`).

Verified mismatches:
- `getVaultStorage()`: comment says `keccak256("yieldnest.storage.vault")`, actual slot `0x22cdba...` does not match computed `0x74dc61...`
- `getAssetStorage()`: comment says `keccak256("yieldnest.storage.asset")`, actual slot `0x2dd192...` does not match computed `0xa357f8...`
- All five custom storage getters have similar mismatches

The ERC20Storage getter uses the correct OpenZeppelin ERC7201 slot.

While all six slots are confirmed unique (no collisions), misleading comments increase the risk of introducing collisions during future upgrades.

**Recommendation**:

Correct the comments to show how the actual slot values were derived, or replace the slots with values computed from the stated formulas.

---

### L-02: ProcessorStorage comment claims `keccak256("yieldnest.storage.vault")` -- same string as VaultStorage

**Severity**: LOW

**Affected file and lines**: `src/library/VaultLib.sol:60-65`

**Description**:

The comment above `getProcessorStorage()` on line 62 says:
```solidity
// keccak256("yieldnest.storage.vault")
```

This is the exact same derivation string claimed for `getVaultStorage()` on line 40. While the actual slot values are different (VaultStorage uses `0x22cdba...` and ProcessorStorage uses `0x52bb80...`), using the same comment for two different storage getters is confusing and error-prone.

The correct comment should reference something like `keccak256("yieldnest.storage.processor")`.

**Recommendation**:

Fix the comment to reflect the correct derivation string or the actual source of the slot value.

---

### L-03: `deleteAsset` swap-and-pop can silently break external integrations relying on asset indices

**Severity**: LOW

**Affected file and lines**: `src/library/VaultLib.sol:184-211`

**Description**:

The `deleteAsset` function uses a swap-and-pop pattern: the asset at the deleted index is replaced by the last asset in the list, and the list is shortened. This changes the index of the moved asset.

While the function correctly updates `assetStorage.assets[movedAsset].index`, any off-chain systems or external contracts that cached asset indices will now reference the wrong asset. The `defaultAssetIndex` is protected (it cannot be deleted and is always 0 or 1), but other indices may be used by external integrations.

**Recommendation**:

Document this behaviour clearly. Consider emitting an event that indicates the index reassignment (the current `DeleteAsset` event only reports the deleted index and asset).

---

### L-04: `addAsset` duplicate check is incomplete for assets added at index 0 then re-added

**Severity**: LOW

**Affected file and lines**: `src/library/VaultLib.sol:121-161`

**Description**:

The duplicate asset check on line 154 uses:
```solidity
if (index > 0 && assetStorage.assets[asset_].index != 0) {
    revert IVault.DuplicateAsset(asset_);
}
```

This check only triggers when `index > 0` (i.e. the asset list already has at least one entry). The condition `assetStorage.assets[asset_].index != 0` will be false for an asset that was previously added at index 0 and then somehow removed -- but the base asset (index 0) cannot be deleted per `deleteAsset`. So in practice this is safe.

However, if an asset was added at a non-zero index, deleted (which clears its `assets` mapping entry), and then re-added, the deleted asset's `AssetParams` is `{index: 0, active: false, decimals: 0}` by default. The check `assetStorage.assets[asset_].index != 0` would be false, allowing re-addition. This is actually the correct and intended behaviour for re-adding previously deleted assets.

The line 150 check (`index > 0 && asset_ == assetStorage.list[0]`) correctly prevents adding the base asset again.

This is primarily a code clarity concern -- the duplicate detection logic is subtle and relies on the default mapping value of 0 coinciding with the base asset index.

**Recommendation**:

Add a comment explaining the duplicate detection logic relies on the default mapping value and why this is safe.

---

### L-05: No maximum cap on number of assets -- unbounded loop in `computeTotalAssets`

**Severity**: LOW

**Affected file and lines**: `src/library/VaultLib.sol:374-389`

**Description**:

The `computeTotalAssets()` function iterates through all assets in the list:
```solidity
for (uint256 i = 0; i < assetListLength; i++) {
    uint256 balance = IERC20(assetList[i]).balanceOf(address(this));
    if (balance == 0) continue;
    totalBaseBalance += convertAssetToBase(assetList[i], balance, Math.Rounding.Floor);
}
```

Each iteration makes two external calls: `balanceOf()` and `getRate()` (via `convertAssetToBase`). There is no upper bound on the number of assets that can be added. While adding assets is restricted to `ASSET_MANAGER_ROLE`, a large number of assets could cause:
- `computeTotalAssets()` to exceed block gas limits
- `processAccounting()` to become uncallable
- All deposit/withdraw operations to fail if `alwaysComputeTotalAssets` is true

**Recommendation**:

Add a maximum asset count constant (e.g. `MAX_ASSETS = 32`) and enforce it in `addAsset`.

---

### L-06: `receive()` accepts native ETH without restriction but only counts it if `countNativeAsset` is true

**Severity**: LOW

**Affected file and lines**: `src/BaseVault.sol:1010-1012`

**Description**:

The `receive()` function accepts ETH from any sender and emits a `NativeDeposit` event. However, no shares are minted for the sender, and the ETH is only counted in `computeTotalAssets()` if `countNativeAsset` is `true`.

If `countNativeAsset` is `false`, ETH sent to the vault is effectively lost to the sender but benefits existing shareholders (it increases the vault's ETH balance which is not accounted for). If `countNativeAsset` is `true`, the ETH increases the vault's reported total assets, inflating the share price and benefiting existing shareholders at the expense of future depositors.

This is a known ERC4626 donation attack vector, though the impact is limited because:
1. The `+1` offset in `convertToShares`/`convertToAssets` provides some protection against the first-depositor attack
2. The vault is intended to be seeded before use (per the comment on `subTotalAssets`)

**Recommendation**:

Consider restricting `receive()` to only accept ETH from known contracts (e.g. the buffer strategy or processor targets), or document this behaviour as a known design decision.

---

### L-07: Fee manager can set per-user fee override to 100% (1e8), effectively blocking withdrawals

**Severity**: LOW

**Affected file and lines**: `src/library/LinearWithdrawalFeeLib.sol:56-62`, `src/module/FeeMath.sol:32-34`

**Description**:

The `overrideBaseWithdrawalFee` function allows setting a per-user fee up to `BASIS_POINT_SCALE` (1e8 = 100%). If set to 100%, the `feeOnRaw` calculation returns `amount * 1e8 / 1e8 = amount`, meaning the fee equals the entire withdrawal amount. In `previewWithdraw`, the shares needed would be `convertToShares(assets + fee) = convertToShares(2 * assets)`, effectively doubling the cost.

For `previewRedeem`, `feeOnTotal` with fee=1e8 returns `amount * 1e8 / (1e8 + 1e8) = amount / 2`, meaning the user receives only half the value of their shares.

This gives the `FEE_MANAGER_ROLE` the power to effectively prevent specific users from withdrawing by setting their fee to 100%.

**Recommendation**:

Consider setting a maximum fee cap lower than 100% (e.g. 50% or 20%) to prevent this denial-of-service vector.

---

### I-01: ERC4626 `deposit` and `mint` do not check for zero shares minted

**Severity**: INFO

**Affected file and lines**: `src/BaseVault.sol:280-286, 294-317`

**Description**:

The `deposit()` and `mint()` functions do not explicitly check that the resulting shares amount is non-zero. If a very small deposit amount is provided relative to the current share price, `convertToShares` could return 0 shares (due to floor rounding). The user would transfer tokens but receive 0 shares.

OpenZeppelin's reference ERC4626 implementation checks for this condition. The current code's `+1` offset in the mulDiv formula makes this unlikely in practice, but not impossible with very large total assets and small deposit amounts.

**Recommendation**:

Add a check: `if (shares == 0) revert ZeroAmount();`

---

### I-02: `convertToShares` / `convertToAssets` use virtual total supply offset of +1

**Severity**: INFO

**Affected file and lines**: `src/library/VaultLib.sol:285-313`

**Description**:

The conversion formulas use:
```solidity
baseAssets = shares.mulDiv(totalAssets + 1, totalSupply + 1, rounding);
shares = baseAssets.mulDiv(totalSupply + 1, totalAssets + 1, rounding);
```

This `+1` offset is a simplified inflation attack mitigation (compared to OpenZeppelin's virtual shares/assets approach using `_decimalsOffset()`). While this provides basic protection, it offers less protection than the standard OpenZeppelin approach for high-decimal tokens.

The `+1` offset means:
- When totalSupply=0 and totalAssets=0: 1 asset -> `1 * (0+1) / (0+1) = 1` share (1:1 mapping)
- An attacker donating assets to inflate `totalAssets` before the first deposit gains less advantage due to the offset, but the protection weakens as amounts grow

**Recommendation**:

Document the design decision and consider whether the `+1` offset provides sufficient protection for the expected asset magnitudes.

---

### I-03: External library deployment creates tight coupling between proxy and library addresses

**Severity**: INFO

**Affected file and lines**: `compiler_settings.json`, `Vault.sol`

**Description**:

The compiler settings show VaultLib, HooksLib, and LinearWithdrawalFeeLib are deployed as external libraries at specific addresses:
```json
"HooksLib": "0xa31f8bfd9e642783ea38859eb5064eb3414dea2e",
"LinearWithdrawalFeeLib": "0x11d398cb65ecd0789025e16815ae0f54a479f7dd",
"VaultLib": "0x605ec7e59693e5764d32cfefc61c665c2603532d"
```

These libraries use `DELEGATECALL` (Solidity's default for external library calls) to execute in the vault's storage context. This is functionally correct, but:

1. Library addresses are baked into the Vault's bytecode at deployment time
2. Upgrading library logic requires deploying a new vault implementation and upgrading the proxy
3. If a library address is compromised or self-destructed, all vault operations would fail

**Recommendation**:

This is standard practice for Solidity external libraries. Ensure library contracts are verified and that their code cannot be modified (no self-destruct, no DELEGATECALL to user-controlled addresses within library code).

---

### I-04: No event emitted when `mintShares` is called by hooks

**Severity**: INFO

**Affected file and lines**: `src/BaseVault.sol:970-976`

**Description**:

The `mintShares` function calls `_mint(recipient, shares)` which emits a standard ERC20 `Transfer` event from address(0). However, there is no vault-specific event indicating that shares were minted via the hooks mechanism (as opposed to a regular deposit). This makes it difficult to distinguish between legitimate deposit-minted shares and hook-minted shares in off-chain monitoring.

**Recommendation**:

Add a custom event such as `HooksMintShares(address indexed recipient, uint256 shares)`.

---

## Architecture observations

### Role-based access control model

The vault uses a comprehensive role system:

| Role | Responsibility | Risk level |
|------|---------------|------------|
| `DEFAULT_ADMIN_ROLE` | Grant/revoke all other roles | Critical |
| `PROCESSOR_ROLE` | Execute arbitrary calls via processor | Critical |
| `PROCESSOR_MANAGER_ROLE` | Configure processor rules (whitelist) | High |
| `HOOKS_MANAGER_ROLE` | Set hooks contract (can mint shares) | High |
| `ASSET_MANAGER_ROLE` | Add/remove/update assets | High |
| `FEE_MANAGER_ROLE` | Set withdrawal fees, per-user overrides | Medium |
| `BUFFER_MANAGER_ROLE` | Set buffer strategy address | Medium |
| `PROVIDER_MANAGER_ROLE` | Set rate provider address | High |
| `PAUSER_ROLE` / `UNPAUSER_ROLE` | Pause/unpause vault operations | Medium |
| `ASSET_WITHDRAWER_ROLE` | Withdraw specific assets (fee-free) | Medium |

The `PROCESSOR_ROLE` and `HOOKS_MANAGER_ROLE` are the most powerful roles and should be assigned to multi-sig wallets or timelocked contracts. The `PROVIDER_MANAGER_ROLE` is also critical because the rate provider directly controls share pricing.

### External dependency trust assumptions

1. **Rate Provider** (`IProvider.getRate`): The vault trusts this contract to return accurate exchange rates. A compromised provider can inflate or deflate asset values, enabling theft or denial of service.

2. **Buffer Strategy** (`IStrategy`): The vault trusts the buffer to correctly withdraw assets on redemption. A compromised buffer could fail to return assets.

3. **Hooks Contract** (`IHooks`): The hooks contract has execution rights during all deposit/withdraw/accounting operations and can mint shares. This is the broadest trust assumption.

4. **Validator Contracts** (`IValidator`): Validators can approve or reject processor calls. A compromised validator could approve malicious calls.

### ERC4626 compliance notes

- The vault extends ERC4626 with multi-asset support (`depositAsset`, `withdrawAsset`, `previewDepositAsset`)
- Standard ERC4626 functions (`deposit`, `withdraw`, `mint`, `redeem`) operate on the `defaultAsset`
- Withdrawal fees are applied in `previewWithdraw` and `previewRedeem` but not in `withdrawAsset`
- The `+1` offset in share/asset conversion differs from OpenZeppelin's standard virtual shares approach

### Positive security patterns observed

1. **ReentrancyGuard**: All public state-changing functions use `nonReentrant`
2. **SafeERC20**: All token transfers use OpenZeppelin's SafeERC20 wrapper
3. **Initializer protection**: Constructor calls `_disableInitializers()` to prevent implementation contract initialisation
4. **Pausing**: Deposit and withdrawal operations check the paused state
5. **Burns before transfers**: `_withdraw` burns shares before transferring assets, following checks-effects-interactions
6. **Rounding direction**: Deposits round shares down (fewer shares minted), withdrawals round shares up (more shares burned) -- correct for vault protection
