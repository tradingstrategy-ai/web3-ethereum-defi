# YieldNest ynRWAx vault - Cyfrin solskill audit

Audit of the YieldNest ynRWAx multi-asset ERC-4626 vault against the
[Cyfrin/solskill](https://github.com/Cyfrin/solskill) production-grade Solidity coding standards.

Solidity version: `^0.8.24`

## Source files reviewed

| File | Lines | Description |
|------|-------|-------------|
| `Vault.sol` | 109 | Concrete vault, inherits BaseVault + LinearWithdrawalFee |
| `src/BaseVault.sol` | 1031 | Core ERC-4626 logic, access control, hooks, admin |
| `src/library/VaultLib.sol` | 459 | Library for storage, accounting, conversions, processor |
| `src/library/HooksLib.sol` | 178 | Library for hook dispatch |
| `src/library/LinearWithdrawalFeeLib.sol` | 96 | Library for withdrawal fee maths |
| `src/module/Guard.sol` | 46 | Call-validation guard for the processor |
| `src/module/FeeMath.sol` | 41 | Basis-point fee arithmetic |
| `src/module/LinearWithdrawalFee.sol` | 83 | Abstract contract bridging lib to vault |
| `src/interface/IVault.sol` | 183 | Vault interface, structs, errors, events |
| `src/interface/IHooks.sol` | 183 | Hook interface and param structs |
| `src/interface/IStrategy.sol` | 21 | Strategy (buffer) interface |
| `src/interface/IProvider.sol` | 6 | Price-rate provider interface |
| `src/interface/IValidator.sol` | 12 | Processor call validator interface |
| `src/Common.sol` | 31 | Re-export hub for OpenZeppelin imports |

---

## Summary of findings

| Severity | Count |
|----------|-------|
| High | 3 |
| Medium | 6 |
| Low | 8 |
| Informational | 12 |

---

## High severity

### H-01 CEI violation in `_deposit` -- state updated before token transfer

**Category:** Security concern / CEI pattern compliance
**File:** `src/BaseVault.sol:542-557`

```solidity
function _deposit(...) internal virtual {
    if (!_getAssetStorage().assets[asset_].active) {
        revert AssetNotActive();
    }

    _addTotalAssets(baseAssets);                                      // Effect

    SafeERC20.safeTransferFrom(IERC20(asset_), caller, address(this), assets); // Interaction
    _mint(receiver, shares);                                          // Effect

    emit Deposit(caller, receiver, assets, shares);
    emit DepositAsset(caller, receiver, asset_, assets, baseAssets, shares);
}
```

`_addTotalAssets` updates `vaultStorage.totalAssets` **before** the external
`safeTransferFrom` call. Although `nonReentrant` is applied to the public
entry points, the hook calls (`beforeDeposit` / `afterDeposit`) that surround
`_deposit` use `address.call`, which means a malicious hooks contract could
re-enter through a different non-guarded path. The correct CEI ordering
would be: check asset active, transfer tokens in, then update
`totalAssets` and mint shares.

**Recommendation:** Move `_addTotalAssets(baseAssets)` after the
`safeTransferFrom` call, or at minimum ensure the reentrancy guard covers
every path that touches `totalAssets`.

---

### H-02 `_feeOnRaw` and `_feeOnTotal` are `public` functions with leading underscores -- exposed as external interface

**Category:** Security concern / standard violation
**File:** `src/BaseVault.sol:1021-1029`, `Vault.sol:68-82`, `src/interface/IVault.sol:180-181`

These functions are declared `public view virtual` with leading underscores
(conventionally internal) yet are part of the `IVault` interface and
callable externally. This creates a confusing interface that violates
Solidity naming conventions.

More critically, these functions compute fee amounts using `_msgSender()` in
`previewWithdraw` and `previewRedeem`. Since `_feeOnRaw` and `_feeOnTotal`
are public, anyone can call them directly to probe fee calculations for
arbitrary user addresses. While not a direct fund-loss issue, the naming
suggests internal-only use but the actual visibility is fully external,
which could mislead integrators into trusting they are access-controlled.

**Recommendation:** Either rename them to `feeOnRaw` / `feeOnTotal` (no
underscore) to match their actual visibility, or make them `internal` and
expose proper public wrappers.

---

### H-03 `processor` function executes arbitrary external calls with value -- missing reentrancy guard

**Category:** Security concern / reentrancy
**File:** `src/BaseVault.sol:956-963`, `src/library/VaultLib.sol:441-458`

```solidity
function processor(address[] calldata targets, uint256[] memory values, bytes[] calldata data)
    external
    virtual
    onlyRole(PROCESSOR_ROLE)
    returns (bytes[] memory returnData)
{
    return VaultLib.processor(targets, values, data);
}
```

The `processor` function makes arbitrary low-level `.call{value: ...}()`
calls to external targets. It does **not** use the `nonReentrant` modifier.
A compromised or malicious target could re-enter the vault's deposit,
withdraw, or accounting functions during the call sequence. While the
`PROCESSOR_ROLE` is permissioned, the Cyfrin standards mandate defence in
depth: reentrancy protection should be applied to any function making
external calls, especially one that forwards ETH value.

**Recommendation:** Add `nonReentrant` to the `processor` function.

---

## Medium severity

### M-01 `withdrawAsset` lacks `nonReentrant` modifier

**Category:** Security concern / reentrancy guard usage
**File:** `src/BaseVault.sol:613-629`

```solidity
function withdrawAsset(address asset_, uint256 assets, address receiver, address owner)
    public
    virtual
    onlyRole(ASSET_WITHDRAWER_ROLE)
    returns (uint256 shares)
```

Unlike `deposit`, `mint`, `withdraw`, and `redeem` which all carry
`nonReentrant`, the `withdrawAsset` function does not. It makes an external
`safeTransfer` call via `_withdrawAsset`. Per Cyfrin standard 22,
`nonReentrant` should be placed before other modifiers on any state-changing
function that performs external calls.

**Recommendation:** Add `nonReentrant` as the first modifier.

---

### M-02 `mintShares` has no reentrancy guard

**Category:** Security concern / reentrancy guard usage
**File:** `src/BaseVault.sol:970-976`

```solidity
function mintShares(address recipient, uint256 shares) external {
    if (msg.sender != address(hooks())) {
        revert CallerNotHooks();
    }
    _mint(recipient, shares);
}
```

This function mints vault shares and is only callable by the hooks contract.
However, hooks are invoked via `address.call` from within `nonReentrant`
functions. If the hooks contract calls `mintShares` during a hook callback,
it will succeed because the reentrancy check was already passed at the
top-level. This means `mintShares` can be called during an in-flight
deposit/withdraw, inflating share supply without corresponding asset
backing.

**Recommendation:** Add explicit state tracking or an additional guard to
prevent inflationary mints during hook callbacks.

---

### M-03 `processAccounting` is public and permissionless

**Category:** Security concern / access control
**File:** `src/BaseVault.sol:933-935`

```solidity
function processAccounting() public virtual nonReentrant {
    _processAccounting();
}
```

Any external account can call `processAccounting()` at any time. This
re-computes `totalAssets` by querying balances and external rates from
the provider. An attacker could manipulate an underlying asset balance
(e.g. via a flash loan or donation) and then immediately call
`processAccounting()` to inflate or deflate the vault's reported total
assets, affecting share pricing for subsequent deposits or withdrawals.

**Recommendation:** Restrict to a dedicated role or add a minimum time
interval between calls.

---

### M-04 Storage slot comment mismatch in `getProcessorStorage`

**Category:** Standard violation / documentation
**File:** `src/library/VaultLib.sol:60-65`

```solidity
/**
 * @notice Get the processor storage.
 * @return $ The processor storage.
 */
function getProcessorStorage() public pure returns (IVault.ProcessorStorage storage $) {
    assembly {
        // keccak256("yieldnest.storage.vault")   // <-- WRONG comment
        $.slot := 0x52bb806a272c899365572e319d3d6f49ed2259348d19ab0da8abccd4bd46abb5
    }
}
```

The comment says `keccak256("yieldnest.storage.vault")` -- the same string
used for `getVaultStorage()`. The actual slot value is different, so the
code is functionally correct, but the comment is misleading. For
production-grade code under audit, incorrect comments erode trust and can
mask real storage collision bugs.

**Recommendation:** Update the comment to the correct derivation string
(presumably `keccak256("yieldnest.storage.processor")`).

---

### M-05 `VaultStorage` struct is not tightly packed

**Category:** Best practice / storage packing (Cyfrin standard 27)
**File:** `src/interface/IVault.sol:9-21`

```solidity
struct VaultStorage {
    uint256 totalAssets;          // slot 0: 32 bytes
    address provider;             // slot 1: 20 bytes
    address buffer;               // slot 2: 20 bytes
    bool paused;                  // slot 3: 1 byte
    uint8 decimals;               //         1 byte
    bool countNativeAsset;        //         1 byte
    bool alwaysComputeTotalAssets; //        1 byte
    uint256 defaultAssetIndex;    // slot 4: 32 bytes
}
```

`provider` (20 bytes) and `buffer` (20 bytes) each occupy a full slot
despite each leaving 12 bytes unused. `paused`, `decimals`,
`countNativeAsset`, and `alwaysComputeTotalAssets` could be packed with
one of the addresses in a single slot, saving one storage slot. For an
upgradeable contract where storage layout is fixed, this matters for gas
on every read.

**Recommendation:** Pack `provider` + `paused` + `decimals` +
`countNativeAsset` + `alwaysComputeTotalAssets` into one slot (20 + 1 + 1 + 1 + 1 = 24 bytes).

---

### M-06 `setBuffer` allows setting buffer to `address(0)` without pausing the vault

**Category:** Security concern
**File:** `src/library/VaultLib.sol:364-368`

```solidity
function setBuffer(address buffer_) public {
    address previousBuffer = getVaultStorage().buffer;
    getVaultStorage().buffer = buffer_;
    emit IVault.SetBuffer(previousBuffer, buffer_);
}
```

The code comment notes "buffer=address(0) allowed - disables ERC4626
redeem/withdraw calls." However, setting buffer to zero while the vault is
unpaused means `maxWithdraw` and `maxRedeem` will return 0, silently
disabling withdrawals without any pause event. This could trap user funds
if set accidentally.

**Recommendation:** Either require the vault to be paused when setting
buffer to zero, or emit a distinct warning event.

---

## Low severity

### L-01 Floating pragma on concrete contract

**Category:** Standard violation (Cyfrin standard 8)
**File:** `Vault.sol:2`

```solidity
pragma solidity ^0.8.24;
```

Per Cyfrin standards, concrete (non-abstract, non-library) contracts that
will be deployed should use a strict pragma version. `Vault.sol` is the
deployable contract and should pin to an exact compiler version.

**Recommendation:** Change to `pragma solidity 0.8.24;`

---

### L-02 Missing `@custom:security-contact` NatSpec

**Category:** Standard violation (Cyfrin standard 9)
**File:** All contracts

No contract in the codebase includes a `@custom:security-contact` NatSpec
tag. This is a Cyfrin requirement for production contracts so that security
researchers can report vulnerabilities.

**Recommendation:** Add `/// @custom:security-contact security@yieldnest.fi`
(or equivalent) to `Vault.sol` and `BaseVault.sol`.

---

### L-03 `nonReentrant` modifier is not placed before `onlyRole`

**Category:** Standard violation (Cyfrin standard 22)
**File:** `src/BaseVault.sol:280,294,326-330,364-368`

The Cyfrin standard states: "Use `nonReentrant` modifier before other
modifiers." In several functions the ordering is correct (`nonReentrant`
first), but `withdrawAsset` at line 613 uses `onlyRole` first (and is
missing `nonReentrant` entirely -- see M-01). Additionally, `processor`
at line 956 places `onlyRole` before what should be `nonReentrant`.

**Recommendation:** Ensure `nonReentrant` is always the first modifier.

---

### L-04 No use of `ReentrancyGuardTransient`

**Category:** Best practice (Cyfrin standard 23)
**File:** `src/BaseVault.sol:62`

The contract inherits `ReentrancyGuardUpgradeable` which uses permanent
storage for the reentrancy flag. Since Solidity 0.8.24 supports transient
storage (EIP-1153), using `ReentrancyGuardTransient` would save gas on
every `nonReentrant` call.

**Recommendation:** Consider migrating to a transient-storage-based
reentrancy guard if the target chain supports EIP-1153.

---

### L-05 Default variable initialisations in loops

**Category:** Standard violation (Cyfrin standard 13)
**File:** `src/library/VaultLib.sol:341,384,448`, `src/library/HooksLib.sol` (indirectly), `src/module/Guard.sol:22,36`

```solidity
for (uint256 i = 0; i < targetLength; i++) {
```

Cyfrin standard 13 states: "Don't initialize variables to default values."
Loop counters `uint256 i = 0` should be `uint256 i;`.

**Recommendation:** Remove explicit `= 0` initialisations.

---

### L-06 Header style does not match Cyfrin template

**Category:** Standard violation (Cyfrin standard 5)
**File:** Multiple files

The codebase uses `/// ADMIN ///`, `//// FEES ////`, `//// 4626-MAX ////`
style section headers. The Cyfrin standard specifies:

```solidity
/*//////////////////////////////////////////////////////////////
                  INTERNAL STATE-CHANGING FUNCTIONS
//////////////////////////////////////////////////////////////*/
```

**Recommendation:** Adopt the Cyfrin header style for consistency.

---

### L-07 Contract layout order deviates from Cyfrin standard

**Category:** Standard violation (Cyfrin standard 6)
**File:** `src/BaseVault.sol`

The Cyfrin layout prescribes: type declarations, state variables, events,
errors, modifiers, then functions. In `BaseVault.sol`:
- Role constants (`bytes32 public constant PROCESSOR_ROLE = ...`) appear at
  line 767, after hundreds of lines of functions.
- The `constructor` appears at line 1003, after admin functions.
- The `receive` function is at line 1010, after the constructor (correct per
  standard 4), but user-facing state-changing functions like `deposit` appear
  before read-only functions like `getAssets`, which is correct. However, the
  admin/role-gated functions are interleaved with internal functions.

**Recommendation:** Move all role constants to the top of the contract body,
group functions by visibility as specified.

---

### L-08 `Guard` library errors not prefixed with contract name

**Category:** Standard violation (Cyfrin standard 2)
**File:** `src/module/Guard.sol:44-45`

```solidity
error RuleNotActive(address, bytes4);
error AddressNotInAllowlist(address);
```

Cyfrin standard requires custom errors to be prefixed with the contract
name and double underscore: `Guard__RuleNotActive`, `Guard__AddressNotInAllowlist`.
The same issue applies to `HooksLib.sol:7-8` (`HookCallFailed`,
`InvalidPermission`) and `FeeMath.sol:14-18`.

**Recommendation:** Prefix all custom errors with their defining
contract/library name.

---

## Informational

### I-01 Library functions declared `public` instead of `internal`

**Category:** Best practice
**Files:** `src/library/VaultLib.sol` (all functions), `src/library/HooksLib.sol` (all functions), `src/library/LinearWithdrawalFeeLib.sol` (all functions)

All functions in the library contracts are declared `public`. When a library
function is `public`, Solidity deploys it as a separate contract and uses
`DELEGATECALL` to invoke it. For pure computation or storage-accessing
functions that are always called from the same contract, `internal` would
inline the code, saving the `DELEGATECALL` overhead and reducing deployment
complexity.

**Recommendation:** Consider making library functions `internal` where they
do not need to be independently deployed.

---

### I-02 `calldata` array length cached in `setProcessorRules`

**Category:** Standard violation (Cyfrin standard 16)
**File:** `src/library/VaultLib.sol:337`

```solidity
uint256 targetLength = target.length;
```

Cyfrin standard 16 says: "Don't cache `calldata` array length." Since
`target` is `calldata`, its `.length` is already cheap to read.

**Recommendation:** Use `target.length` directly in the loop condition.

---

### I-03 Named return variables unused in several functions

**Category:** Best practice (Cyfrin standard 14)
**File:** Multiple

Several functions declare named return variables but then explicitly
`return` a value instead of assigning to the named variable, defeating the
purpose. For example, `previewRedeem` at `BaseVault.sol:207-210`:

```solidity
function previewRedeem(uint256 shares) public view virtual returns (uint256 assets) {
    (assets,) = _convertToAssets(asset(), shares, Math.Rounding.Floor);
    return assets - _feeOnTotal(assets, _msgSender());
}
```

Here `assets` is assigned, then a different expression is returned. The
named return is misleading.

**Recommendation:** Either use the named return consistently or remove it.

---

### I-04 Duplicate storage reads in `pause` and `unpause`

**Category:** Best practice (Cyfrin standard 17)
**File:** `src/BaseVault.sol:902-926`

Both `pause()` and `unpause()` call `paused()` (which reads
`_getVaultStorage().paused`) and then separately call
`_getVaultStorage()` again to mutate the storage. This results in two
SLOAD operations for the same slot.

**Recommendation:** Cache the storage pointer:
```solidity
VaultStorage storage vs = _getVaultStorage();
if (vs.paused) revert Paused();
vs.paused = true;
```

---

### I-05 `setProvider` reads `getVaultStorage()` twice

**Category:** Best practice (Cyfrin standard 17)
**File:** `src/library/VaultLib.sol:350-357`

```solidity
function setProvider(address provider_) public {
    if (provider_ == address(0)) revert IVault.ZeroAddress();
    address previousProvider = getVaultStorage().provider;
    getVaultStorage().provider = provider_;
    ...
}
```

Two separate calls to `getVaultStorage()`, which each execute an assembly
block to compute the storage slot.

**Recommendation:** Cache in a local variable.

---

### I-06 `withdrawAsset` reads `IERC20(asset_).balanceOf(address(this))` twice

**Category:** Best practice (Cyfrin standard 17)
**File:** `src/BaseVault.sol:624-625`

```solidity
if (assets > IERC20(asset_).balanceOf(address(this)) || balanceOf(owner) < shares) {
    revert ExceededMaxWithdraw(owner, assets, IERC20(asset_).balanceOf(address(this)));
}
```

The same external call `IERC20(asset_).balanceOf(address(this))` is made
twice. This wastes gas and, in theory, the balance could change between
calls if the token has transfer hooks.

**Recommendation:** Cache the balance in a local variable.

---

### I-07 Missing NatSpec on several internal/library functions

**Category:** Best practice / documentation
**Files:** `src/BaseVault.sol:840,857,869,989`, `src/module/Guard.sol:9,31,35`

Functions such as `_addAsset`, `_updateAsset`, `_deleteAsset`, `_setHooks`,
`Guard.validateCall`, `Guard._validateAddress`, and `Guard._isInArray` lack
NatSpec `@notice` or `@dev` documentation.

**Recommendation:** Add NatSpec to all functions.

---

### I-08 `Guard.validateCall` only handles `ADDRESS` param type

**Category:** Best practice / incomplete implementation
**File:** `src/module/Guard.sol:22-28`

```solidity
for (uint256 i = 0; i < rule.paramRules.length; i++) {
    if (rule.paramRules[i].paramType == IVault.ParamType.ADDRESS) {
        ...
        continue;
    }
}
```

The `ParamType` enum also has `UINT256`, but there is no validation logic
for it. A `UINT256` param rule would silently pass without any check.

**Recommendation:** Either implement `UINT256` validation or revert for
unsupported param types.

---

### I-09 `Common.sol` imports unused contracts

**Category:** Best practice
**File:** `src/Common.sol`

`Common.sol` imports `ProxyAdmin`, `TransparentUpgradeableProxy`,
`TimelockController`, `Ownable`, `OwnableUpgradeable`, `IERC165`,
`ERC20`, `Address`, and several other contracts that are never used by the
vault code. This bloats compilation and can confuse auditors about the
actual dependency surface.

**Recommendation:** Remove unused imports from `Common.sol`.

---

### I-10 `hookEnabled` uses chained if-statements instead of a bitmap

**Category:** Best practice / gas
**File:** `src/library/HooksLib.sol:30-45`

The `hookEnabled` function reads a `Config` struct with 10 boolean fields
from the hooks contract, then checks them with sequential `if` statements.
A single `uint256` bitmap would be cheaper to store and compare.

**Recommendation:** Consider using a bitmap for hook permissions.

---

### I-11 `_deposit` does not verify `shares > 0`

**Category:** Best practice
**File:** `src/BaseVault.sol:535-557`

If a very small deposit results in zero shares after rounding, the function
will proceed to transfer tokens in and mint zero shares. The depositor
loses their tokens.

**Recommendation:** Add `if (shares == 0) revert ZeroAmount();` before the
transfer.

---

### I-12 Relative imports used in `Common.sol`

**Category:** Standard violation (Cyfrin standard 1)
**File:** `src/Common.sol:5-28`

```solidity
import {AccessControlUpgradeable} from
    "lib/openzeppelin-contracts-upgradeable/contracts/access/AccessControlUpgradeable.sol";
```

These are path-based imports using `lib/` prefix rather than absolute named
imports. Whilst they work in Foundry, the Cyfrin standard recommends
absolute and named imports only, with no relative (`..`) paths. The `lib/`
prefix is a Foundry remapping convention, not a true absolute path.

**Recommendation:** Use Foundry remappings to create cleaner import paths
(e.g. `@openzeppelin/...`).

---

## FREI-PI invariant analysis

The Cyfrin methodology emphasises encoding O(1) invariants directly into
core functions. The following key invariants are identified for this vault:

| Invariant | Encoded? | Notes |
|-----------|----------|-------|
| `totalSupply == 0 => totalAssets == 0` | No | Not enforced. A seed deposit breaks this on purpose, but there is no check preventing totalAssets from drifting to zero while shares remain outstanding. |
| `shares minted <= assets deposited (at current rate)` | Partially | `_convertToShares` rounds down, which is correct. |
| `assets withdrawn + fee <= shares burned (at current rate)` | Partially | `previewWithdraw` rounds up correctly. |
| `totalAssets only increases on deposit, decreases on withdraw` | No | `processAccounting` can arbitrarily change totalAssets based on external oracle rates. No upper/lower bound on delta. |
| `sum of all user shares == totalSupply` | Yes | Inherited from OZ ERC20. |

**Recommendation:** Add explicit invariant checks (or at minimum,
assertions) for the first and fourth invariants, ideally tested via
stateful fuzz tests as the Cyfrin standard recommends.

---

## CEI pattern compliance summary

| Function | CEI compliant? | Issue |
|----------|---------------|-------|
| `_deposit` | No | State update (`_addTotalAssets`) before external call (`safeTransferFrom`) -- see H-01 |
| `_withdraw` | Mostly | Burns shares before external `buffer.withdraw`, which is correct. However `_subTotalAssets` is called before burn, which is acceptable. |
| `_withdrawAsset` | Yes | Burns before transfer. |
| `processor` | N/A | Arbitrary external calls by design, but lacks reentrancy guard -- see H-03 |
| `processAccounting` | Mostly | External calls to provider and hooks before and after state update. Protected by `nonReentrant`. |

---

## Access control summary

The vault uses OpenZeppelin `AccessControlUpgradeable` with the following roles:

| Role | Controls |
|------|----------|
| `DEFAULT_ADMIN_ROLE` | Granting/revoking all other roles |
| `PROCESSOR_ROLE` | Executing arbitrary calls via `processor()` |
| `PAUSER_ROLE` | Pausing the vault |
| `UNPAUSER_ROLE` | Unpausing the vault |
| `PROVIDER_MANAGER_ROLE` | Setting the price provider |
| `BUFFER_MANAGER_ROLE` | Setting the buffer strategy |
| `ASSET_MANAGER_ROLE` | Adding/updating/deleting assets |
| `PROCESSOR_MANAGER_ROLE` | Setting processor rules |
| `HOOKS_MANAGER_ROLE` | Setting the hooks contract |
| `ASSET_WITHDRAWER_ROLE` | Withdrawing specific assets |
| `FEE_MANAGER_ROLE` | Setting withdrawal fees |

The role separation is comprehensive. However, the `PROCESSOR_ROLE` is
extremely powerful -- it can execute arbitrary calls from the vault address,
including approving tokens, transferring tokens, or interacting with any
protocol. This role must be protected by a multisig with a timelock, as
recommended by Cyfrin standard 12.

---

## Event emission completeness

| Operation | Event emitted? | Notes |
|-----------|---------------|-------|
| Deposit (default asset) | `Deposit` + `DepositAsset` | Complete |
| Deposit (other asset) | `Deposit` + `DepositAsset` | Complete |
| Withdraw | `Withdraw` | Missing fee amount in event |
| Redeem | `Withdraw` | Missing fee amount in event |
| WithdrawAsset | `WithdrawAsset` | Complete |
| Pause/Unpause | `Pause(bool)` | Complete |
| Set provider | `SetProvider` | Complete |
| Set buffer | `SetBuffer` | Complete |
| Add/update/delete asset | Yes | Complete |
| Process accounting | `ProcessAccounting` | Complete |
| Processor calls | `ProcessSuccess` | Complete |
| Fee changes | `SetBaseWithdrawalFee`, `WithdrawalFeeOverridden` | Complete |
| Hooks changes | `SetHooks` | Complete |
| Mint shares (via hooks) | None | Missing -- no event emitted for inflationary mints |
| Native deposit | `NativeDeposit` | Complete |

**Recommendation:** Emit fee amounts in `Withdraw` events. Emit an event
from `mintShares` to make inflationary mints observable off-chain.
