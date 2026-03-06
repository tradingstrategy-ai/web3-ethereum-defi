# Rigoblock SmartPool -- kadenzipfel/scv-scan audit report

**Target:** Rigoblock SmartPool (ERC-1967 proxy-based pool/fund management protocol)
- Proxy: `0xEfa4bDf566aE50537A507863612638680420645C` (Solidity 0.8.17)
- Implementation: `0x1DB955265B8DC18715cAb12E805F9b71Fa545420` (Solidity 0.8.28)
- 45 Solidity source files, ~3,143 lines
- Pool Owner: `0xcA9F5049c1Ea8FC78574f94B7Cf5bE5fEE354C31` (EOA)
- Authority: `0xe35129A1E0BdB913CF6Fd8332E9d3533b5F41472`

**Methodology:** kadenzipfel/scv-scan 4-phase approach (cheatsheet load, syntactic grep, semantic read-through, deep validation with false-positive checks) across 36 vulnerability types.

---

## [CRITICAL] Unsafe downcast of `uint256` to `uint208` can silently truncate user balances

- **Severity:** CRITICAL
- **Vulnerability Type:** Integer Overflow and Underflow (unsafe type downcast)
- **File:** `implementation/src/protocol/core/actions/MixinActions.sol:200, 206, 270, 283`
- **Description:** The `_allocateMintTokens` and `_allocateBurnTokens` functions cast `mintedAmount` and `feePool` (both `uint256`) directly to `uint208` without using `SafeCast`. Although `SafeCast` is imported and used elsewhere in the codebase (e.g., for `toInt256()` conversions), these particular casts use raw `uint208(...)` syntax. If the computed `mintedAmount` ever exceeds `type(uint208).max` (~4.1e62), the value silently truncates, resulting in a user receiving far fewer pool tokens than they paid for. While reaching this threshold is difficult with standard 18-decimal tokens, it becomes more plausible with very large deposits or pools with very low unitary value (e.g., a pool whose NAV has dropped significantly). The `activation` field in `UserAccount` uses only `uint48`, and together with `uint208 userBalance`, this packs into a single storage slot (256 bits total), meaning the truncation boundary is a real design constraint rather than being unreachable.
- **Impact:** Silent balance truncation could cause users to lose deposited funds. A user could deposit a large amount, have their minted tokens silently truncated, and receive substantially fewer tokens than the NAV-adjusted value of their deposit. The same applies to fee collector balances.
- **Recommendation:** Replace all raw `uint208(...)` casts with `SafeCast.toUint208()` from OpenZeppelin, which reverts on overflow. The `SafeCast` library is already imported. Alternatively, add explicit `require(value <= type(uint208).max)` checks before each cast.

**Code:**
```solidity
// MixinActions.sol:200 - _allocateMintTokens
accounts().userAccounts[feeCollector].userBalance += uint208(feePool);

// MixinActions.sol:206
accounts().userAccounts[recipient].userBalance += uint208(mintedAmount);

// MixinActions.sol:270 - _allocateBurnTokens
accounts().userAccounts[msg.sender].userBalance -= uint208(amountIn);

// MixinActions.sol:283
accounts().userAccounts[feeCollector].userBalance += uint208(feePool);
```

---

## [HIGH] Missing reentrancy guard on `updateUnitaryValue()` allows NAV manipulation

- **Severity:** HIGH
- **Vulnerability Type:** Reentrancy
- **File:** `implementation/src/protocol/core/actions/MixinActions.sol:80`
- **Description:** The `updateUnitaryValue()` function is `external` with no access control and no `nonReentrant` modifier. The function calls `_updateNav()`, which in turn calls `_computeTotalPoolValue()`. This private function makes multiple external calls: `IEApps(address(this)).getAppTokenBalances()`, `IERC20(token).balanceOf()`, and `IEOracle(address(this)).convertBatchTokenAmounts()`. These calls flow through the fallback/extension system which involves `delegatecall` and `staticcall` to extension contracts. Since the pool's `_updateNav` writes to `poolTokens().unitaryValue` in storage at the end, and this function lacks reentrancy protection, a malicious extension or oracle that re-enters during the computation could observe or manipulate intermediate transient storage state. The developer comment says "Reentrancy protection provided by calling functions (mint, burn, depositV3, donate)", but `updateUnitaryValue()` itself is directly callable by anyone without going through those protected paths. This means an attacker can call `updateUnitaryValue()` at any time to trigger the NAV computation and storage write without the reentrancy guard.
- **Impact:** A manipulated NAV value written to storage could affect subsequent mint/burn operations, allowing an attacker to mint pool tokens at an artificially low NAV or burn at an artificially high NAV, effectively extracting value from other pool holders. The impact depends on the specific extension contracts' behaviour during re-entry.
- **Recommendation:** Add the `nonReentrant` modifier to `updateUnitaryValue()`. Since the function modifies storage (`poolTokens().unitaryValue`) via `_updateNav()`, it should have the same reentrancy protection as `mint()` and `burn()`.

**Code:**
```solidity
// MixinActions.sol:80 - missing nonReentrant
function updateUnitaryValue() external override returns (NetAssetsValue memory navParams) {
    NavComponents memory components = _updateNav();
    // ...
}
```

---

## [HIGH] Delegatecall to governance-controlled adapter target in fallback enables pool owner storage takeover

- **Severity:** HIGH
- **Vulnerability Type:** Delegatecall to Untrusted Callee
- **File:** `implementation/src/protocol/core/sys/MixinFallback.sol:28-68`
- **Description:** The fallback function routes unknown selectors to targets determined by two sources: (1) `_extensionsMap.getExtensionBySelector()` (immutable map set at deployment), and (2) `IAuthority(authority).getApplicationAdapter()` (mutable, controlled by the governance/authority contract). For route (2), when `msg.sender == pool().owner`, the call is a `delegatecall`, executing arbitrary code in the pool's storage context. This is by design for the owner to execute trades and management operations. However, the `authority` contract (`0xe35129A1E0BdB913CF6Fd8332E9d3533b5F41472`) controls which adapters map to which selectors. If the authority is compromised or the whitelister role is abused, a malicious adapter can be whitelisted that, when called by the pool owner, executes arbitrary code in the pool's storage context. Combined with the fact that the pool owner is a single EOA, compromise of either the owner EOA or the authority's whitelister key gives complete control over all pool funds via delegatecall.
- **Impact:** Total loss of funds for all pool holders. A compromised authority or whitelister can whitelist a malicious adapter that, once delegatecalled by the owner, drains all pool assets, corrupts storage, or transfers ownership.
- **Recommendation:** (1) Replace the single EOA pool owner with a multisig or timelock. (2) Consider adding a timelock to adapter whitelisting in the authority contract. (3) Add validation in the fallback that adapters satisfy a known interface before delegatecall.

**Code:**
```solidity
// MixinFallback.sol:36-48 - governance-controlled delegatecall target
if (target == _ZERO_ADDRESS) {
    target = IAuthority(authority).getApplicationAdapter(msg.sig);
    require(target != _ZERO_ADDRESS, PoolMethodNotAllowed());
    // ...
    shouldDelegatecall = msg.sender == pool().owner;
}

assembly {
    // ...
    if eq(shouldDelegatecall, 1) {
        success := delegatecall(gas(), target, 0, calldatasize(), 0, 0)
        // ...
    }
}
```

---

## [HIGH] Fee-on-transfer and rebasing tokens cause accounting discrepancies in mint/burn

- **Severity:** HIGH
- **Vulnerability Type:** Inadherence to Standards
- **File:** `implementation/src/protocol/core/actions/MixinActions.sol:151`
- **Description:** The `_mint()` function transfers `amountIn` tokens from the user via `safeTransferFrom`, then uses the same `amountIn` value (minus spread) for calculating minted pool tokens. For fee-on-transfer (FoT) tokens, the actual amount received by the pool is less than `amountIn`, but the pool mints tokens as if it received the full amount. This inflates the pool token supply relative to actual assets, diluting existing holders. The pool supports arbitrary ERC20 base tokens set at initialisation and supports `mintWithToken` for any owner-accepted token, making FoT token interaction a realistic scenario.
- **Impact:** When a fee-on-transfer token is used as base token or mint token, the pool systematically over-mints pool tokens relative to actual received assets. Over time, this causes a growing discrepancy between pool NAV and actual holdings, diluting existing holders and creating an extractable arbitrage: mint with FoT token (pool over-counts), then burn for another token (pool pays out based on inflated accounting).
- **Recommendation:** Use a balance-before/balance-after pattern to determine the actual amount received by the pool, rather than trusting the `amountIn` parameter. Alternatively, explicitly document and enforce that FoT tokens are not supported, and add a check in `setAcceptableMintToken` or `initializePool` to reject known FoT tokens.

**Code:**
```solidity
// MixinActions.sol:151 - assumes full amountIn received
tokenIn.safeTransferFrom(msg.sender, address(this), amountIn);
tokenIn.safeTransfer(_getTokenJar(), spread);
// ...
amountIn -= spread;
// amountIn used for calculation, but actual received may be less for FoT tokens
uint256 mintedAmount = (amountIn * 10 ** components.decimals) / components.unitaryValue;
```

---

## [MEDIUM] Unchecked overflow in `_allocateMintTokens` activation timestamp calculation

- **Severity:** MEDIUM
- **Vulnerability Type:** Integer Overflow and Underflow
- **File:** `implementation/src/protocol/core/actions/MixinActions.sol:186-188`
- **Description:** The activation timestamp is calculated inside an `unchecked` block as `uint48(block.timestamp) + _getMinPeriod()`. The `uint48` type can represent timestamps up to approximately year 8,919,556. While `block.timestamp` will not overflow `uint48` for billions of years, the developer comment says "it is safe to use unchecked as max min period is 30 days". However, the actual maximum lockup `_MAX_LOCKUP` is `30 days = 2,592,000 seconds`. If `_getMinPeriod()` returned a value close to `type(uint48).max - block.timestamp`, the addition could overflow. Currently `_getMinPeriod()` is bounded by `_MAX_LOCKUP` (30 days), which makes overflow impossible with current timestamp values. However, the `unchecked` block suppresses the safety check, and if a future code change increased `_MAX_LOCKUP` or the lockup validation was bypassed via an extension, the overflow would wrap the activation to a past timestamp, allowing immediate burns and bypassing the lockup period.
- **Impact:** If the activation overflows (currently impractical but possible through future code changes), tokens would have an activation timestamp in the past, allowing immediate burning and bypassing the lockup period. This would enable flash-loan-like attacks: deposit, immediately withdraw, exploiting any momentary NAV miscalculation.
- **Recommendation:** Remove the `unchecked` block. The gas savings are negligible compared to the risk of a silently wrapping overflow if the constants or validation logic change in future implementations.

**Code:**
```solidity
// MixinActions.sol:186-188
unchecked {
    activation = uint48(block.timestamp) + _getMinPeriod();
}
```

---

## [MEDIUM] `SafeTransferLib.safeTransferNative` uses 2300 gas stipend which fails on some receiver contracts

- **Severity:** MEDIUM
- **Vulnerability Type:** Unsupported Opcodes / DoS with (Unexpected) Revert
- **File:** `implementation/src/protocol/libraries/SafeTransferLib.sol:18`
- **Description:** The `safeTransferNative` function uses `.call{gas: 2300, value: amount}("")` for ETH transfers. The 2300 gas stipend is inherited from Solidity's `transfer()` and `send()` functions, which is insufficient for receiver contracts that have non-trivial receive/fallback functions (e.g., Gnosis Safe multisigs, some smart contract wallets, and any contract that emits events or writes storage in its receive function). This affects burn operations where the burn output is ETH: if the caller is a smart contract wallet or multisig that costs more than 2300 gas to receive ETH, the burn will always revert, effectively locking their funds in the pool. The same applies to spread payments to the `tokenJar` address.
- **Impact:** Users who interact with the pool through smart contract wallets (Gnosis Safe, Argent, etc.) may be unable to burn pool tokens for native ETH, as the ETH transfer to their contract will revert due to insufficient gas. The `tokenJar` contract receiving spread fees could also fail if it is a complex contract. This is a funds-locking scenario for affected users.
- **Recommendation:** Replace `to.call{gas: 2300, value: amount}("")` with `to.call{value: amount}("")` to forward all available gas. Add reentrancy guards on the calling functions (already present on mint/burn) to protect against re-entry from the unrestricted gas forwarding.

**Code:**
```solidity
// SafeTransferLib.sol:18
function safeTransferNative(address to, uint256 amount) internal {
    (bool success, ) = to.call{gas: 2300, value: amount}("");
    require(success, NativeTransferFailed());
}
```

---

## [MEDIUM] Non-functional ERC20 `transfer`, `transferFrom`, and `approve` functions return false by default

- **Severity:** MEDIUM
- **Vulnerability Type:** Inadherence to Standards
- **File:** `implementation/src/protocol/core/sys/MixinAbstract.sol:9-18`
- **Description:** The `MixinAbstract` contract implements `transfer()`, `transferFrom()`, and `approve()` as empty functions that return the default `bool` value (`false`). While the contract comments indicate these are "Non-implemented ERC20 methods", any external contract or DeFi protocol that interacts with the pool token expecting standard ERC20 behaviour (e.g., checking the return value of `transfer()`) will see `false` and consider the operation failed. The functions don't revert, they silently return `false`, which is particularly dangerous because callers using `require(token.transfer(...))` will revert, while callers that don't check the return value will believe the transfer succeeded when nothing actually happened. This means pool tokens cannot be integrated with any DeFi protocol that expects standard ERC20 transfers.
- **Impact:** Pool tokens are non-transferable through standard ERC20 interfaces. Any DeFi integration (DEXes, lending protocols, yield aggregators) that calls `transfer()` or `transferFrom()` will either revert (if checking return value) or silently fail (if not checking). This severely limits pool token composability and could trap tokens in contracts that expected standard ERC20 behaviour.
- **Recommendation:** Either (a) implement proper ERC20 transfer/approve functionality with balance tracking, or (b) make these functions explicitly revert with a clear error message (e.g., `revert("Pool tokens are non-transferable")`) rather than silently returning `false`. The current behaviour of returning `false` without reverting is the worst of both worlds.

**Code:**
```solidity
// MixinAbstract.sol:9-18
function transfer(address to, uint256 value) external override returns (bool success) {}
function transferFrom(address from, address to, uint256 value) external override returns (bool success) {}
function approve(address spender, uint256 value) external override returns (bool success) {}
function allowance(address owner, address spender) external view override returns (uint256) {}
```

---

## [MEDIUM] Frontrunning exposure on `mint()` and `burn()` NAV updates

- **Severity:** MEDIUM
- **Vulnerability Type:** Transaction-Ordering Dependence (Frontrunning)
- **File:** `implementation/src/protocol/core/actions/MixinActions.sol:40-76`
- **Description:** Every call to `mint()` or `burn()` triggers `_updateNav()`, which recalculates and stores the pool's unitary value based on current on-chain token balances and oracle prices. While the functions accept `amountOutMin` parameters for slippage protection, the NAV update itself is susceptible to sandwich attacks. An attacker can observe a large mint/burn in the mempool, manipulate token prices on the oracle's source (e.g., Uniswap pools used for TWAP) in a frontrunning transaction, causing the NAV to be calculated at a manipulated value, then backrun to reverse the price manipulation and profit from the NAV distortion. The `amountOutMin` parameter protects the user from receiving less than expected, but does not prevent the NAV storage write from being manipulated. A manipulated NAV stored on-chain affects all subsequent operations until the next `_updateNav()` call.
- **Impact:** An attacker can sandwich mint/burn operations to manipulate the stored NAV, extracting value from the pool. The spread (default 10 bps) provides some protection but may be insufficient against large-scale oracle manipulation. The attacker profits at the expense of all pool holders through NAV dilution.
- **Recommendation:** (1) Consider implementing TWAP-based oracle reads with a sufficiently long window to resist single-block manipulation. (2) Add a maximum NAV change per update as a circuit breaker. (3) Consider allowing only whitelisted actors to trigger NAV updates, or adding a minimum time between NAV updates.

---

## [MEDIUM] `getStorageAt` and `getStorageSlotsAt` expose arbitrary storage reads including sensitive data

- **Severity:** MEDIUM
- **Vulnerability Type:** Unencrypted Private Data On-Chain / Insufficient Access Control
- **File:** `implementation/src/protocol/core/state/MixinStorageAccessible.sol:10-37`
- **Description:** The `MixinStorageAccessible` contract exposes two `public view` functions -- `getStorageAt` and `getStorageSlotsAt` -- that allow anyone to read arbitrary storage slots from the pool proxy. While all blockchain storage is technically readable via `eth_getStorageAt` RPC calls, these functions make it trivially easy to batch-read sensitive storage without needing to know the specific slot layout. More importantly, the `getStorageAt` function accepts a sequential `offset` and `length` parameter, allowing efficient scanning of contiguous storage regions, which is particularly useful for mapping out user balances, operator approvals, and pool parameters. The functions have no access control.
- **Impact:** While this is technically redundant with `eth_getStorageAt`, the exposed functions make it easier for malicious actors to efficiently extract structured data from the pool, including all user balances, activation timestamps, operator relationships, and pool configuration. This lowers the barrier for building attacks that depend on knowing pool state.
- **Recommendation:** This is a design choice inherited from Gnosis Safe patterns and is likely intentional for transparency. However, consider whether these functions need to be exposed or whether off-chain tools using `eth_getStorageAt` would suffice.

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 1     |
| High     | 3     |
| Medium   | 5     |
| Low      | 0     |
| Info     | 0     |

### Candidate vulnerabilities evaluated and discarded (false positives)

1. **Reentrancy on mint/burn**: All `mint()`, `mintWithToken()`, `burn()`, `burnForToken()` have `nonReentrant` modifier. External calls to oracle/apps happen within the reentrancy guard scope. False positive.

2. **Delegatecall in proxy fallback**: The proxy's fallback delegatecalls to the implementation address read from EIP-1967 slot, set only in constructor by the factory. The factory controls the implementation address. This is a standard secure proxy pattern. False positive.

3. **Delegatecall to extensionsMap**: `_extensionsMap` is an immutable reference set in the constructor. The delegatecall to it in MixinFallback is to resolve selector-to-extension mappings, which is safe since the target is immutable. False positive.

4. **Arbitrary storage via sload in MixinStorageAccessible**: Read-only, cannot modify state. Classified as MEDIUM informational rather than a vulnerability.

5. **Timestamp dependence**: `block.timestamp` is used for lockup period enforcement with a minimum of 1 day. This window is far too large for validator manipulation (~15 seconds). False positive.

6. **DoS with gas limit on loops**: Active tokens set is bounded by `_MAX_UNIQUE_VALUES = 128`. The purge function iterates over this bounded set and active applications (max 255). While these loops are gas-intensive, they are bounded and owner-initiated. False positive.

7. **`assert()` in constructors**: All `assert()` calls are in constructors (MixinStorage, MixinImmutables, MixinInitializer) for compile-time invariant validation. These consume all gas on failure but are appropriate for invariant checks that should never fail. False positive.

8. **`onlyUninitialized` uses `code.length == 0` check**: This modifier in MixinInitializer checks that the contract has no deployed code, which is only true during the constructor execution. This is a correct anti-re-initialization pattern since the proxy initialises during its constructor. A contract calling from its own constructor would have `code.length == 0`, but the `initializePool()` function calls back to the factory for parameters, which provides a natural trust boundary. False positive.

9. **Weak randomness**: No randomness generation found in the codebase. Not applicable.

10. **tx.origin usage**: Not used anywhere. Not applicable.

11. **ecrecover / signature replay**: No signature verification in the codebase. Not applicable.

12. **Hash collision with abi.encodePacked**: Not used in the codebase. Not applicable.

13. **Shadowing state variables**: Solidity 0.8.28 compiler prevents state variable shadowing. Not applicable.

14. **Incorrect constructor name**: Solidity 0.8.x uses `constructor` keyword. Not applicable.

15. **Uninitialized storage pointer**: Solidity 0.8.x requires explicit memory/storage location. Not applicable.
