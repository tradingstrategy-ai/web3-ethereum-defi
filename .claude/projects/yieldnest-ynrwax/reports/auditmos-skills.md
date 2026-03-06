# YieldNest ynRWAx vault -- Auditmos skills audit report

**Contract:** YieldNest ynRWAx Vault (Solidity 0.8.24)
**Methodology:** Auditmos 14-skill DeFi vulnerability framework
**Files analysed:**
- `src/Vault.sol` (109 lines)
- `src/src/BaseVault.sol` (1031 lines)
- `src/src/library/VaultLib.sol` (459 lines)
- `src/src/library/HooksLib.sol` (179 lines)
- `src/src/library/LinearWithdrawalFeeLib.sol` (97 lines)
- `src/src/module/Guard.sol` (47 lines)
- `src/src/module/FeeMath.sol` (42 lines)
- `src/src/module/LinearWithdrawalFee.sol` (84 lines)
- `src/src/interface/IVault.sol`, `IHooks.sol`, `IStrategy.sol`, `IProvider.sol`, `IValidator.sol`

**Vulnerabilities found:** Critical: 0 | High: 3 | Medium: 6 | Low: 4

---

## Skills applied

| Skill | Applicable | Findings |
|---|---|---|
| audit-math-precision | Yes | 2 findings |
| audit-oracle | Yes | 2 findings |
| audit-reentrancy | Yes | 2 findings |
| audit-state-validation | Yes | 2 findings |
| audit-slippage | Yes | 1 finding |
| audit-staking | Yes | 2 findings |
| audit-signature | Marginal | 0 findings (ERC20Permit from OpenZeppelin, properly implemented) |
| audit-lending | No | Not a lending protocol |
| audit-liquidation | No | No liquidation mechanism |
| audit-liquidation-calculation | No | No liquidation mechanism |
| audit-liquidation-dos | No | No liquidation mechanism |
| audit-unfair-liquidation | No | No liquidation mechanism |
| audit-auction | No | No auction mechanism |
| audit-clm | No | No concentrated liquidity management |

---

## HIGH-01: Deposit CEI violation -- state updated before token transfer enables inflation attack with callback tokens

**Skill:** audit-reentrancy (Pattern #2: State update after external call -- inverted)
**File:** `src/src/BaseVault.sol`
**Lines:** 535-557
**Function:** `_deposit()`

### Description

The `_deposit` function updates `totalAssets` (via `_addTotalAssets`) *before* transferring tokens in from the caller. While the `nonReentrant` modifier protects the external `deposit()` and `depositAsset()` entry points, the internal function `_deposit()` follows a Checks-Effects-Interactions pattern that inverts the expected order when considering the accounting side-effect: the vault's `totalAssets` is increased before the tokens are actually received. If a malicious or fee-on-transfer token is used as a vault asset, the accounting will overcount the actual balance received. The `nonReentrant` guard on the outer function mitigates the worst reentrancy scenario, but the accounting mismatch for fee-on-transfer tokens is unprotected.

### Vulnerable code

```solidity
// BaseVault.sol:535-557
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

    _addTotalAssets(baseAssets);                                           // EFFECT: totalAssets increased

    SafeERC20.safeTransferFrom(IERC20(asset_), caller, address(this), assets); // INTERACTION: tokens transferred
    _mint(receiver, shares);                                               // EFFECT: shares minted

    emit Deposit(caller, receiver, assets, shares);
    emit DepositAsset(caller, receiver, asset_, assets, baseAssets, shares);
}
```

### Impact

- **Loss magnitude:** For fee-on-transfer tokens, the vault will credit more `totalAssets` than it actually receives. Over many deposits this leads to an inflationary mismatch where shares are overvalued relative to actual holdings.
- **Exploitability:** Medium -- requires the vault to accept a fee-on-transfer token as a supported asset. The `nonReentrant` modifier prevents direct reentrancy draining, but the accounting drift is permanent.
- **Affected functions:** `deposit()`, `depositAsset()`, `mint()`.

### Proof of concept

```solidity
// Token charges 1% fee on transfer
// User deposits 1000 tokens
// Vault receives 990 tokens but accounts for 1000 in totalAssets
// After 100 such deposits: totalAssets = 100000, actual = 99000
// All subsequent share conversions overvalue the vault by ~1%
```

### Remediation

```solidity
function _deposit(...) internal virtual {
    if (!_getAssetStorage().assets[asset_].active) {
        revert AssetNotActive();
    }

    // Transfer first, then measure actual received amount
    uint256 balanceBefore = IERC20(asset_).balanceOf(address(this));
    SafeERC20.safeTransferFrom(IERC20(asset_), caller, address(this), assets);
    uint256 actualReceived = IERC20(asset_).balanceOf(address(this)) - balanceBefore;

    // Account for actual received, not requested
    uint256 actualBaseAssets = _convertAssetToBase(asset_, actualReceived, Math.Rounding.Floor);
    _addTotalAssets(actualBaseAssets);
    _mint(receiver, shares);

    emit Deposit(caller, receiver, actualReceived, shares);
}
```

Alternatively, if fee-on-transfer tokens are explicitly not supported, add documentation and a check at `addAsset()` time to validate that the token does not impose transfer fees.

---

## HIGH-02: No slippage protection on deposits and withdrawals -- share/asset amounts subject to sandwich attacks

**Skill:** audit-slippage (Pattern #1: No slippage parameter)
**File:** `src/src/BaseVault.sol`
**Lines:** 280-286 (deposit), 326-355 (withdraw), 364-393 (redeem)
**Functions:** `deposit()`, `withdraw()`, `redeem()`, `depositAsset()`

### Description

The deposit and withdrawal functions do not accept any minimum output parameters (i.e. `minSharesOut` for deposits, `minAssetsOut` for withdrawals). The share/asset conversion relies on `totalBaseAssets()` and `totalSupply()`, both of which can be manipulated between transaction submission and execution. Since `processAccounting()` is permissionless and updates `totalAssets`, an attacker can sandwich a user's deposit by calling `processAccounting()` to shift the exchange rate, front-running the user's transaction to extract value.

### Vulnerable code

```solidity
// BaseVault.sol:280-286
function deposit(uint256 assets, address receiver) public virtual nonReentrant returns (uint256) {
    if (paused()) {
        revert Paused();
    }
    return _depositAsset(asset(), assets, receiver);
    // No minSharesOut parameter
}

// BaseVault.sol:364-393
function redeem(uint256 shares, address receiver, address owner) public virtual nonReentrant returns (uint256 assets) {
    // ...
    assets = previewRedeem(shares);
    // No minAssetsOut parameter
    // ...
    _withdraw(_msgSender(), receiver, owner, assets, shares);
}
```

### Impact

- **Loss magnitude:** Depends on the size of the `totalAssets` manipulation achievable via `processAccounting()`. If underlying asset rates change significantly between cached and live values, the sandwich profit can be substantial.
- **Exploitability:** High -- `processAccounting()` is callable by anyone and updates the exchange rate used for all conversions. An MEV bot can sandwich any deposit/withdrawal transaction.
- **Affected functions:** `deposit()`, `depositAsset()`, `withdraw()`, `redeem()`, `mint()`.

### Proof of concept

```
1. User submits deposit(1000 USDC) expecting ~1000 shares at current rate
2. Attacker front-runs by calling processAccounting() when rate is favourable
   (e.g. totalAssets increases due to yield accrual)
3. User's deposit executes at a worse rate, receiving fewer shares
4. Attacker back-runs with processAccounting() or their own deposit/redeem to capture the difference
```

### Remediation

Add `minSharesOut` and `minAssetsOut` parameters to deposit and withdrawal functions:

```solidity
function deposit(uint256 assets, address receiver, uint256 minSharesOut)
    public virtual nonReentrant returns (uint256 shares)
{
    if (paused()) revert Paused();
    shares = _depositAsset(asset(), assets, receiver);
    if (shares < minSharesOut) revert SlippageExceeded();
}
```

---

## HIGH-03: `processAccounting()` is permissionless and can be used to manipulate the exchange rate

**Skill:** audit-staking (Pattern #5: Update not called after reward distribution) and audit-oracle (no staleness/manipulation protection)
**File:** `src/src/BaseVault.sol`, `src/src/library/VaultLib.sol`
**Lines:** BaseVault.sol:933-935, VaultLib.sol:394-432
**Functions:** `processAccounting()`, `VaultLib.processAccounting()`

### Description

The `processAccounting()` function is publicly callable by anyone (only protected by `nonReentrant`). It recomputes `totalAssets` by iterating over all vault assets, querying their balances and converting them via the provider's rates. This means any external actor can trigger an accounting update at any time, potentially at a moment when rates are temporarily unfavourable due to market conditions or oracle staleness. Combined with the lack of slippage protection (HIGH-02), this creates a sandwich vector where an attacker can manipulate the timing of accounting updates to exploit other users' deposits and withdrawals.

### Vulnerable code

```solidity
// BaseVault.sol:933-935
function processAccounting() public virtual nonReentrant {
    _processAccounting();
}

// VaultLib.sol:394-432
function processAccounting() public {
    IVault _vault = IVault(address(this));
    IVault.VaultStorage storage vaultStorage = getVaultStorage();
    // ...
    uint256 totalBaseAssetsAfterAccounting = computeTotalAssets();
    vaultStorage.totalAssets = totalBaseAssetsAfterAccounting;  // Anyone can update this
    // ...
}
```

### Impact

- **Loss magnitude:** Proportional to the delta between cached and live `totalAssets`. If strategies accrue yield or rates change, the cached value can be significantly stale. An attacker can choose when to trigger the update for maximum extraction.
- **Exploitability:** High -- no access restriction, no cooldown, no maximum delta check.
- **Affected functions:** All functions that rely on `totalBaseAssets()` when `alwaysComputeTotalAssets` is false (the default mode).

### Proof of concept

```
1. Vault has cached totalAssets = 10000 (stale value)
2. Real total assets is 10500 due to yield accrual in strategies
3. Attacker deposits at the stale rate, receiving shares based on totalAssets = 10000
   -> Gets more shares per asset than they should
4. Attacker immediately calls processAccounting(), updating totalAssets to 10500
5. Attacker redeems their shares at the new, higher rate
   -> Extracts the difference as profit
```

### Remediation

Add access control or a cooldown mechanism to `processAccounting()`:

```solidity
// Option A: Access control
function processAccounting() public virtual nonReentrant onlyRole(PROCESSOR_ROLE) {
    _processAccounting();
}

// Option B: Cooldown
uint256 public constant ACCOUNTING_COOLDOWN = 1 hours;
function processAccounting() public virtual nonReentrant {
    require(block.timestamp >= lastAccountingTimestamp + ACCOUNTING_COOLDOWN, "Cooldown");
    lastAccountingTimestamp = block.timestamp;
    _processAccounting();
}
```

---

## MEDIUM-01: Provider rate has no staleness or manipulation validation

**Skill:** audit-oracle (Pattern #1: Not checking stale prices, Pattern #6: Unhandled oracle reverts)
**File:** `src/src/library/VaultLib.sol`
**Lines:** 221-229, 239-247
**Functions:** `convertAssetToBase()`, `convertBaseToAsset()`

### Description

The `IProvider.getRate()` call has no validation for staleness, zero values, or reasonableness bounds. The provider is a single external contract that returns a rate for each asset. If the provider returns a stale, zero, or manipulated rate, the vault will use it directly in all share/asset conversions, affecting deposits, withdrawals, and accounting. The `IProvider` interface exposes only `getRate(address asset)` with no additional metadata (timestamp, confidence, etc.).

### Vulnerable code

```solidity
// VaultLib.sol:221-229
function convertAssetToBase(address asset_, uint256 assets, Math.Rounding rounding)
    public view returns (uint256 baseAssets)
{
    if (asset_ == address(0)) revert IVault.ZeroAddress();
    uint256 rate = IProvider(getVaultStorage().provider).getRate(asset_);
    // No check: rate == 0, rate staleness, rate bounds
    baseAssets = assets.mulDiv(rate, 10 ** (getAssetStorage().assets[asset_].decimals), rounding);
}
```

### Impact

- **Loss magnitude:** If `rate` is zero, `convertAssetToBase` returns zero, meaning deposited assets would be valued at zero, minting zero shares (or near-zero). If rate is artificially inflated, withdrawals extract more than deposited.
- **Exploitability:** Medium -- depends on the provider implementation, which is external and not in scope. However, the vault provides no defence-in-depth against provider failures.
- **Affected functions:** All conversion functions, `computeTotalAssets()`, `processAccounting()`.

### Remediation

```solidity
function convertAssetToBase(address asset_, uint256 assets, Math.Rounding rounding)
    public view returns (uint256 baseAssets)
{
    if (asset_ == address(0)) revert IVault.ZeroAddress();
    uint256 rate = IProvider(getVaultStorage().provider).getRate(asset_);
    if (rate == 0) revert IVault.ZeroRate();
    baseAssets = assets.mulDiv(rate, 10 ** (getAssetStorage().assets[asset_].decimals), rounding);
}
```

Consider also wrapping the provider call in a try/catch to handle reverts gracefully, and adding bounds checks for rate reasonableness.

---

## MEDIUM-02: `withdrawAsset` does not charge withdrawal fees

**Skill:** audit-math-precision (Pattern #7: Rounding leaks value from protocol)
**File:** `src/src/BaseVault.sol`
**Lines:** 613-629
**Function:** `withdrawAsset()`

### Description

The `withdrawAsset()` function allows users with `ASSET_WITHDRAWER_ROLE` to withdraw specific assets from the vault. Unlike `withdraw()` and `redeem()`, this function does not apply any withdrawal fees. While it is role-gated, the fee bypass means that any address granted `ASSET_WITHDRAWER_ROLE` can extract value without paying the withdrawal fee that regular users must pay. If this role is granted to a contract or entity that processes user withdrawals, the fee structure is effectively bypassed.

### Vulnerable code

```solidity
// BaseVault.sol:613-629
function withdrawAsset(address asset_, uint256 assets, address receiver, address owner)
    public virtual onlyRole(ASSET_WITHDRAWER_ROLE) returns (uint256 shares)
{
    if (paused()) { revert Paused(); }
    // NO fee calculation -- contrast with withdraw() which calls previewWithdraw() including fees
    (shares,) = _convertToShares(asset_, assets, Math.Rounding.Ceil);
    if (assets > IERC20(asset_).balanceOf(address(this)) || balanceOf(owner) < shares) {
        revert ExceededMaxWithdraw(owner, assets, IERC20(asset_).balanceOf(address(this)));
    }
    _withdrawAsset(asset_, _msgSender(), receiver, owner, assets, shares);
}
```

### Impact

- **Loss magnitude:** Equal to the withdrawal fee percentage multiplied by the withdrawal amount. If the base withdrawal fee is e.g. 0.5%, every `withdrawAsset` call avoids this fee entirely.
- **Exploitability:** Medium -- requires `ASSET_WITHDRAWER_ROLE`, but if this role is used for legitimate user-facing withdrawals (e.g. a router or integration contract), it leaks protocol revenue.

### Remediation

Either apply the withdrawal fee consistently in `withdrawAsset()`, or document explicitly that this function is intended as a fee-exempt administrative action and ensure the role is never granted to user-facing contracts.

---

## MEDIUM-03: `_feeOnRaw` and `_feeOnTotal` are declared as `public view` -- internal naming convention with external visibility

**Skill:** audit-state-validation (Pattern #6: Missing access control)
**File:** `src/src/BaseVault.sol`, `src/Vault.sol`
**Lines:** BaseVault.sol:1021-1029, Vault.sol:68-82
**Functions:** `_feeOnRaw()`, `_feeOnTotal()`

### Description

The functions `_feeOnRaw` and `_feeOnTotal` use the underscore-prefix naming convention that universally denotes internal/private functions in Solidity, but they are declared with `public` visibility. They are even listed in the `IVault` interface, meaning they are part of the external ABI. This is a state validation issue: the naming convention misleads auditors and developers into believing these functions are internal when they are in fact externally callable. While they are `view` functions and cannot change state, they expose the fee calculation logic and per-user fee overrides to any external caller, which may reveal information about fee-exempted addresses (e.g. institutional partners).

### Vulnerable code

```solidity
// BaseVault.sol:1021-1022
function _feeOnRaw(uint256 amount, address user) public view virtual override returns (uint256);
function _feeOnTotal(uint256 amount, address user) public view virtual override returns (uint256);

// IVault.sol:180-181
function _feeOnRaw(uint256 amount, address user) external view returns (uint256);
function _feeOnTotal(uint256 amount, address user) external view returns (uint256);
```

### Impact

- **Loss magnitude:** No direct fund loss. Information leakage about fee-exempted addresses.
- **Exploitability:** Low -- view functions only. But the naming convention violation is a code quality issue that could mask real vulnerabilities in future modifications.

### Remediation

Rename to `feeOnRaw` / `feeOnTotal` (without underscore prefix) or change visibility to `internal` and create separate `external` wrapper functions with appropriate naming.

---

## MEDIUM-04: Guard only validates ADDRESS-type parameters, skipping UINT256 validation

**Skill:** audit-state-validation (Pattern #3: Unexpected empty inputs)
**File:** `src/src/module/Guard.sol`
**Lines:** 9-29
**Function:** `validateCall()`

### Description

The `Guard.validateCall()` function iterates over parameter rules but only validates parameters with `ParamType.ADDRESS`. Parameters with `ParamType.UINT256` are iterated over but never checked (`continue` is only hit for ADDRESS type; for UINT256 the loop body has no validation logic). This means the processor guard does not enforce any rules on uint256 parameters, even if param rules are configured for them.

### Vulnerable code

```solidity
// Guard.sol:9-29
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
        // UINT256 type: NO validation performed, silently passes
    }
}
```

### Impact

- **Loss magnitude:** A malicious or compromised `PROCESSOR_ROLE` holder could pass arbitrary uint256 values (amounts, slippage parameters, etc.) through the processor, bypassing intended guard restrictions.
- **Exploitability:** Medium -- requires `PROCESSOR_ROLE` access. The Guard is meant to be the safety net for processor calls, but it provides no protection for numeric parameters.

### Remediation

Add uint256 validation logic (e.g. min/max bounds checking) to the guard:

```solidity
if (rule.paramRules[i].paramType == IVault.ParamType.UINT256) {
    uint256 uintValue = abi.decode(data[4 + i * 32:], (uint256));
    _validateUint256(uintValue, rule.paramRules[i]);
    continue;
}
```

---

## MEDIUM-05: First depositor inflation attack -- virtual offset of +1 is insufficient for high-decimal assets

**Skill:** audit-staking (Pattern #1: Front-running first deposit)
**File:** `src/src/library/VaultLib.sol`
**Lines:** 285-313
**Functions:** `convertToAssets()`, `convertToShares()`

### Description

The share/asset conversion uses a virtual offset of `+1` in both numerator and denominator (`totalAssets + 1` / `totalSupply + 1`). While this is a known mitigation against the classic ERC-4626 inflation attack, a `+1` offset is only effective for tokens with low precision. For an 18-decimal base asset (which is the expected configuration), an attacker can still perform a donation-based inflation attack. The attacker deposits a small amount (1 wei) to become the first depositor, then donates a large amount of base asset directly to the vault or to a strategy. After `processAccounting()` runs, the share price is inflated. Subsequent depositors receive far fewer shares than expected.

The OpenZeppelin ERC-4626 implementation uses a configurable `_decimalsOffset()` (typically 0-6 extra decimals) for this reason. A `+1` virtual offset provides negligible protection for 18-decimal tokens.

### Vulnerable code

```solidity
// VaultLib.sol:285-313
function convertToAssets(address asset_, uint256 shares, Math.Rounding rounding)
    public view returns (uint256 assets, uint256 baseAssets)
{
    uint256 totalAssets = IVault(address(this)).totalBaseAssets();
    uint256 totalSupply = getERC20Storage().totalSupply;
    baseAssets = shares.mulDiv(totalAssets + 1, totalSupply + 1, rounding);
    assets = convertBaseToAsset(asset_, baseAssets, rounding);
}

function convertToShares(address asset_, uint256 assets, Math.Rounding rounding)
    public view returns (uint256 shares, uint256 baseAssets)
{
    uint256 totalAssets = IVault(address(this)).totalBaseAssets();
    uint256 totalSupply = getERC20Storage().totalSupply;
    baseAssets = convertAssetToBase(asset_, assets, rounding);
    shares = baseAssets.mulDiv(totalSupply + 1, totalAssets + 1, rounding);
}
```

### Impact

- **Loss magnitude:** High for first depositors after the attacker. The attacker can make subsequent deposits receive significantly fewer shares than expected.
- **Exploitability:** Medium -- requires being the first depositor and donating assets to the vault. Mitigated partially by the vault starting in a paused state (requiring admin to unpause), giving admin opportunity to seed the vault.

### Proof of concept

```
1. Attacker deposits 1 wei, receives 1 share (with virtual offset, shares ~ 1)
2. Attacker donates 10000e18 base tokens directly to vault address
3. processAccounting() is called (permissionless), updating totalAssets to ~10000e18
4. Victim deposits 5000e18 tokens
   shares = 5000e18 * (1 + 1) / (10000e18 + 1) ~ 1 share
5. Victim receives only ~1 share for 5000e18 tokens
6. Attacker redeems their 1 share for ~7500e18 tokens
```

### Remediation

Use a larger virtual offset (e.g. `10 ** _decimalsOffset()` where `_decimalsOffset()` is 3-6) or require a minimum initial deposit amount that makes the inflation attack uneconomical.

---

## MEDIUM-06: `_withdraw()` uses `asset()` for base conversion instead of the withdrawal asset

**Skill:** audit-math-precision (Pattern #3: No precision scaling)
**File:** `src/src/BaseVault.sol`
**Lines:** 583-602
**Function:** `_withdraw()`

### Description

The `_withdraw` function converts the withdrawal `assets` amount to base denomination using `asset()` (the default asset), regardless of which asset is actually being withdrawn. While the standard `withdraw()` and `redeem()` functions always operate on the default asset, if a future code path or override calls `_withdraw` with a different asset, the base conversion would use the wrong asset's rate. Currently, this is not directly exploitable because all callers use the default asset, but it is a latent design risk that could become a vulnerability if the contract is extended.

More importantly, the subtraction `_subTotalAssets(_convertAssetToBase(asset(), assets, Math.Rounding.Floor))` rounds down when subtracting from totalAssets. This means the vault slightly overcounts totalAssets after each withdrawal, as the subtracted amount is less than or equal to the actual value removed. Over many withdrawals, this drift can accumulate.

### Vulnerable code

```solidity
// BaseVault.sol:583-602
function _withdraw(address caller, address receiver, address owner, uint256 assets, uint256 shares)
    internal virtual
{
    VaultStorage storage vaultStorage = _getVaultStorage();

    // Uses asset() (default asset) for base conversion -- not parameterised
    _subTotalAssets(_convertAssetToBase(asset(), assets, Math.Rounding.Floor));
    if (caller != owner) {
        _spendAllowance(owner, caller, shares);
    }
    _burn(owner, shares);
    IStrategy(vaultStorage.buffer).withdraw(assets, receiver, address(this));
    emit Withdraw(caller, receiver, owner, assets, shares);
}
```

### Impact

- **Loss magnitude:** The rounding-down-on-subtraction causes a small positive drift in `totalAssets` per withdrawal. For a vault with millions in AUM and frequent withdrawals, this can accumulate to a meaningful accounting error.
- **Exploitability:** Low for the rounding issue (tiny per-transaction). Medium for the hardcoded `asset()` if the contract is extended to support multi-asset withdrawals via this path.

### Remediation

Pass the withdrawal asset as a parameter to `_withdraw()` for future safety, and consider rounding up the subtraction (or using `Math.Rounding.Ceil`) to favour the protocol:

```solidity
function _withdraw(address caller, address receiver, address owner, uint256 assets, uint256 shares, address asset_)
    internal virtual
{
    _subTotalAssets(_convertAssetToBase(asset_, assets, Math.Rounding.Ceil));
    // ...
}
```

---

## LOW-01: `mintShares()` has no cap or validation on minted amount

**Skill:** audit-state-validation (Pattern #3: Unexpected empty inputs)
**File:** `src/src/BaseVault.sol`
**Lines:** 970-976
**Function:** `mintShares()`

### Description

The `mintShares()` function allows the hooks contract to mint arbitrary shares to any recipient with no upper bound check or zero-amount validation. A malicious or buggy hooks contract could mint unlimited shares, diluting all existing shareholders.

### Vulnerable code

```solidity
// BaseVault.sol:970-976
function mintShares(address recipient, uint256 shares) external {
    if (msg.sender != address(hooks())) {
        revert CallerNotHooks();
    }
    _mint(recipient, shares);
    // No zero check, no cap, no totalAssets update
}
```

### Impact

- **Loss magnitude:** Complete share dilution if hooks contract is compromised. However, the hooks contract address is controlled by `HOOKS_MANAGER_ROLE` and validated at `setHooks()` time to ensure `VAULT()` returns this vault's address.
- **Exploitability:** Low -- requires a compromised or malicious hooks contract, which in turn requires a compromised `HOOKS_MANAGER_ROLE` admin.

### Remediation

Add a zero-amount check and consider a maximum mint cap or corresponding `totalAssets` update:

```solidity
function mintShares(address recipient, uint256 shares) external {
    if (msg.sender != address(hooks())) revert CallerNotHooks();
    if (shares == 0) revert ZeroAmount();
    _mint(recipient, shares);
}
```

---

## LOW-02: `setBuffer(address(0))` disables withdrawals but does not pause deposits

**Skill:** audit-state-validation (Pattern #8: Improper pause mechanism)
**File:** `src/src/library/VaultLib.sol`
**Lines:** 364-368
**Function:** `setBuffer()`

### Description

Setting the buffer to `address(0)` is explicitly documented as a way to disable ERC-4626 redeem/withdraw operations. However, deposits remain active. This creates an asymmetric state where users can deposit but cannot withdraw, which could trap user funds if the buffer is set to zero while the vault is unpaused.

### Vulnerable code

```solidity
// VaultLib.sol:364-368
function setBuffer(address buffer_) public {
    // SECURITY: buffer=address(0) allowed - disables ERC4626 redeem/withdraw calls.
    address previousBuffer = getVaultStorage().buffer;
    getVaultStorage().buffer = buffer_;
    emit IVault.SetBuffer(previousBuffer, buffer_);
}
```

### Impact

- **Loss magnitude:** Users who deposit while buffer is zero will have their funds trapped until buffer is restored.
- **Exploitability:** Low -- requires `BUFFER_MANAGER_ROLE` to set buffer to zero without pausing the vault. This is an admin-level misconfiguration risk.

### Remediation

Consider automatically pausing the vault when buffer is set to `address(0)`, or adding a check in `deposit()` that reverts if buffer is zero.

---

## LOW-03: `convertToShares` rounds `baseAssets` down regardless of rounding parameter

**Skill:** audit-math-precision (Pattern #7: Rounding leaks value from protocol)
**File:** `src/src/library/VaultLib.sol`
**Lines:** 304-313
**Function:** `convertToShares()`

### Description

The `convertToShares()` function calls `convertAssetToBase()` which applies the `rounding` parameter. However, the comment in `BaseVault._convertToShares()` at line 684 states "baseAssets is always rounded down, ignoring the rounding parameter". This inconsistency between the documentation and the implementation (which does pass the rounding parameter through to `convertAssetToBase`) indicates a potential design confusion. If the intent is for baseAssets to always round down, then the `Ceil` rounding used in `mint()` would not properly round up the intermediate baseAssets computation.

### Vulnerable code

```solidity
// VaultLib.sol:304-313
function convertToShares(address asset_, uint256 assets, Math.Rounding rounding)
    public view returns (uint256 shares, uint256 baseAssets)
{
    uint256 totalAssets = IVault(address(this)).totalBaseAssets();
    uint256 totalSupply = getERC20Storage().totalSupply;
    baseAssets = convertAssetToBase(asset_, assets, rounding); // Passes rounding through
    shares = baseAssets.mulDiv(totalSupply + 1, totalAssets + 1, rounding);
}

// BaseVault.sol:684 comment:
// @dev baseAssets is always rounded down, ignoring the rounding parameter.
```

### Impact

- **Loss magnitude:** Negligible per transaction (1-2 wei difference), but the documentation/code mismatch indicates possible rounding direction confusion that could compound.
- **Exploitability:** Low -- the actual impact is minimal since `mulDiv` with the correct rounding is applied to the final shares calculation.

### Remediation

Either update the comment to match the implementation, or explicitly round baseAssets down:

```solidity
baseAssets = convertAssetToBase(asset_, assets, Math.Rounding.Floor); // Always round down
shares = baseAssets.mulDiv(totalSupply + 1, totalAssets + 1, rounding); // Rounding applies to shares
```

---

## LOW-04: Hook calls via low-level `.call()` have no gas limit

**Skill:** audit-reentrancy (Pattern #1: Token transfer reentrancy -- callback vectors)
**File:** `src/src/library/HooksLib.sol`
**Lines:** 53-57
**Function:** `callHook()`

### Description

The `callHook` function uses a low-level `.call()` without a gas limit. A malicious or gas-intensive hooks contract could consume all available gas, causing deposits, withdrawals, and other hooked operations to revert. While the hooks contract is set by a trusted role, a poorly implemented hooks contract could cause a denial-of-service for all vault operations.

### Vulnerable code

```solidity
// HooksLib.sol:53-57
function callHook(IHooks self, bytes memory data) internal returns (bytes memory) {
    (bool success, bytes memory result) = address(self).call(data);
    if (!success) revert HookCallFailed(result);
    return result;
}
```

### Impact

- **Loss magnitude:** No direct fund loss, but potential DoS on all vault operations if the hooks contract consumes excessive gas or reverts.
- **Exploitability:** Low -- requires a compromised `HOOKS_MANAGER_ROLE` to set a malicious hooks contract.

### Remediation

Consider adding a gas limit to hook calls:

```solidity
(bool success, bytes memory result) = address(self).call{gas: 500000}(data);
```

---

## Checklist verification

### Always-checklist (cross-cutting)

| Check | Status | Notes |
|---|---|---|
| State changes before external calls (CEI) | FAIL | `_deposit()` updates totalAssets before transferFrom (HIGH-01) |
| NonReentrant on vulnerable functions | PASS | All entry points use `nonReentrant` |
| No assumptions about token transfer behaviour | FAIL | No fee-on-transfer handling (HIGH-01) |
| Cross-function reentrancy considered | PASS | `nonReentrant` is global |
| Read-only reentrancy risks evaluated | PASS | View functions read consistent state |
| Fee-on-transfer tokens handled | FAIL | Not handled (HIGH-01) |
| Rebasing tokens accounted for | WARN | No rebasing detection; would break accounting |
| Tokens with callbacks (ERC777) considered | PASS | `nonReentrant` protects |
| Zero transfer reverting tokens handled | PASS | SafeERC20 used throughout |
| Pausable tokens won't brick protocol | PASS | Vault has its own pause mechanism |
| Token decimals properly scaled | PASS | Decimals queried from token and stored |
| Critical functions have appropriate modifiers | PASS | Role-based access control throughout |
| Two-step ownership transfer | N/A | Uses AccessControl roles, not Ownable |
| Role-based permissions segregated | PASS | 9 separate roles for different operations |
| Emergency pause functionality | PASS | Separate PAUSER_ROLE and UNPAUSER_ROLE |
| Time delays for critical operations | WARN | No timelock on parameter changes |

### Math precision checklist

| Check | Status | Notes |
|---|---|---|
| Multiplication before division | PASS | Uses OpenZeppelin `mulDiv` |
| Rounding to zero checks | WARN | No minimum amount checks on deposits |
| Token amounts scaled to common precision | PASS | Base asset conversion via provider rates |
| No double-scaling | PASS | Single conversion path through VaultLib |
| Consistent precision across modules | WARN | Comment/code mismatch on rounding (LOW-03) |
| SafeCast for downcasting | N/A | No downcasting of large values |
| Protocol fees round up, user amounts round down | PASS | `FeeMath.feeOnRaw` uses `Ceil` rounding |
| Decimal assumptions validated | PASS | Decimals checked at `addAsset()` |
| Interest calculations use correct time units | N/A | No interest calculations |
| Token pair directions consistent | PASS | Single direction via provider rate |

### Oracle checklist

| Check | Status | Notes |
|---|---|---|
| Stale price checks | FAIL | No staleness validation on provider rates (MEDIUM-01) |
| L2 sequencer check | N/A | No direct Chainlink usage |
| Feed-specific heartbeats | N/A | Uses custom IProvider |
| Oracle precision via decimals() | PASS | Asset decimals stored and used |
| Oracle revert handling | FAIL | No try/catch around provider calls (MEDIUM-01) |
| TWAP usage | N/A | No AMM price dependencies |
| Price direction verified | PASS | Single rate per asset from provider |
| Circuit breaker checks | FAIL | No bounds validation on rates (MEDIUM-01) |

### Reentrancy checklist

| Check | Status | Notes |
|---|---|---|
| CEI pattern | FAIL | `_deposit()` updates state before transfer (HIGH-01) |
| NonReentrant on state-changing functions | PASS | Applied to all external entry points |
| Token assumptions | WARN | Assumes standard ERC-20 behaviour |
| Cross-function analysis | PASS | Global reentrancy guard prevents cross-function re-entry |
| Read-only safety | PASS | View functions return consistent state |

### Slippage checklist

| Check | Status | Notes |
|---|---|---|
| User can specify minTokensOut | FAIL | No slippage parameter on any function (HIGH-02) |
| User can specify deadline | FAIL | No deadline parameter |
| Slippage calculated correctly | N/A | No slippage mechanism exists |
| Slippage checked on final output | FAIL | No output validation |

### Staking/Vault checklist

| Check | Status | Notes |
|---|---|---|
| No direct transfer dilution | WARN | `computeTotalAssets()` uses `balanceOf`, donations inflate totalAssets |
| Precision protection for small deposits | FAIL | +1 virtual offset insufficient for 18-decimal tokens (MEDIUM-05) |
| Flash protection | FAIL | No time locks or minimum deposit duration |
| Index updates | WARN | `processAccounting()` is permissionless (HIGH-03) |

### State validation checklist

| Check | Status | Notes |
|---|---|---|
| Multi-step processes verify previous steps | PASS | Initializer pattern correct |
| Array lengths > 0 validated | PASS | `setProcessorRules` validates matching lengths |
| Function inputs validated | WARN | `mintShares` no zero check (LOW-01) |
| Return values checked | PASS | Processor checks `success` return |
| Pause mechanisms synchronised | WARN | Buffer=0 disables withdrawals but not deposits (LOW-02) |

---

## Summary

### High severity issues requiring attention

1. **HIGH-01** -- Deposit CEI violation with fee-on-transfer token accounting mismatch
2. **HIGH-02** -- No slippage protection on deposits and withdrawals
3. **HIGH-03** -- Permissionless `processAccounting()` enables exchange rate manipulation

### Medium severity issues

1. **MEDIUM-01** -- No validation on provider rates (staleness, zero, bounds)
2. **MEDIUM-02** -- `withdrawAsset()` bypasses withdrawal fees
3. **MEDIUM-03** -- Public `_feeOnRaw`/`_feeOnTotal` with internal naming convention
4. **MEDIUM-04** -- Guard only validates ADDRESS params, ignoring UINT256
5. **MEDIUM-05** -- First depositor inflation attack with insufficient +1 offset
6. **MEDIUM-06** -- `_withdraw()` hardcodes `asset()` for base conversion with rounding drift

### Recommendations

- Add slippage protection (`minSharesOut`/`minAssetsOut`) to all deposit and withdrawal functions
- Add access control or cooldown to `processAccounting()`
- Add zero/staleness/bounds validation for provider rates
- Handle fee-on-transfer tokens or explicitly disallow them
- Increase the virtual share offset from +1 to at least `10 ** 3` for 18-decimal tokens
- Add uint256 parameter validation to the Guard module
- Ensure consistent rounding direction policy (favour the protocol)
