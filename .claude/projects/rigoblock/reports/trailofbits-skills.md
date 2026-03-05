# Rigoblock SmartPool security audit report

**Target:** Rigoblock SmartPool (ERC-1967 proxy-based pool/fund management protocol)
- Proxy: `0xEfa4bDf566aE50537A507863612638680420645C` (Solidity 0.8.17)
- Implementation: `0x1DB955265B8DC18715cAb12E805F9b71Fa545420` (Solidity 0.8.28)
- 45 Solidity source files, ~3,143 lines

**Methodology:** Trail of Bits Building Secure Contracts skills -- systematic vulnerability scanning across reentrancy, access control, delegatecall safety, proxy patterns, fee calculation, oracle manipulation, integer overflow/underflow, front-running, and flash loan attack vectors.

**Date:** 2026-03-05

---

## [CRITICAL] Single EOA pool owner controls all privileged functions with no timelock, multisig, or governance constraint

- **Severity:** CRITICAL
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinOwnerActions.sol:42-45`
- **Description:** The pool owner is an externally owned account (EOA) at `0xcA9F5049c1Ea8FC78574f94B7Cf5bE5fEE354C31`. The `onlyOwner` modifier only checks `msg.sender == pool().owner` with no timelock, no multisig requirement, and no governance constraint. This single EOA controls all critical owner functions: `setOwner`, `setTransactionFee`, `changeSpread`, `changeMinPeriod`, `setKycProvider`, `changeFeeCollector`, `setAcceptableMintToken`, and `purgeInactiveTokensAndApps`. If the owner private key is compromised, an attacker gains full control over all pool parameters.
- **Impact:** An attacker who compromises the owner key can: (1) set the transaction fee to the maximum 1% and the spread to the maximum 5% to extract value from depositors, (2) set a malicious KYC provider that blocks all mints/burns except the attacker's, effectively trapping user funds, (3) transfer ownership to another address permanently, (4) manipulate accepted mint tokens to inject worthless tokens and dilute the NAV. Pool holders have no recourse and no time to withdraw.
- **Recommendation:** Replace the single EOA owner with a multisig wallet (e.g. Safe/Gnosis Safe with a minimum 2-of-3 threshold). Implement a timelock contract (e.g. 48-hour delay) for all owner actions that affect user funds, such as fee changes, spread changes, KYC provider changes, and ownership transfers. This gives pool holders time to exit before adverse changes take effect.

---

## [CRITICAL] Fallback function delegatecalls to governance-approved adapters for the owner, enabling arbitrary state modification

- **Severity:** CRITICAL
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/sys/MixinFallback.sol:28-68`
- **Description:** The fallback function first queries the `_extensionsMap` via delegatecall for the called selector. If no extension is found, it queries the `Authority` contract via `getApplicationAdapter(msg.sig)`. When `msg.sender == pool().owner`, the call is executed as a `delegatecall` to the adapter target, granting the adapter full access to the pool's storage context. The Authority contract (`0xe35129A1E0BdB913CF6Fd8332E9d3533b5F41472`) controls which adapters are approved for which selectors. If the Authority governance is compromised or if a malicious adapter is whitelisted, it can execute arbitrary storage writes to any pool's state (including manipulating balances, NAV, ownership, etc.) because the delegatecall runs in the pool's context.
- **Impact:** A compromised Authority governance can whitelist a malicious adapter that, when called by the pool owner, overwrites arbitrary storage slots. This could drain all pool assets, manipulate the NAV to steal funds during mint/burn, reset ownership, or corrupt the virtual supply to bypass cross-chain safety checks. Since the Authority governs all pools deployed by the factory, a single governance compromise affects every pool in the protocol.
- **Recommendation:** Implement strict per-adapter storage access controls (e.g. using a storage guard pattern that restricts which slots an adapter may write to). Add adapter code verification (e.g. require adapters are verified contracts with known bytecode hashes). Add a timelock on Authority governance changes to adapter whitelisting. Consider an emergency pause mechanism that pool holders can trigger if malicious adapter activity is detected.

---

## [HIGH] The `updateUnitaryValue()` function lacks reentrancy protection, allowing NAV manipulation via external calls

- **Severity:** HIGH
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:80-90`
- **Description:** The `updateUnitaryValue()` external function calls `_updateNav()` which in turn calls `IEApps(address(this)).getAppTokenBalances()` and `IEOracle(address(this)).convertBatchTokenAmounts()`. Both of these are routed through the fallback mechanism, which can execute delegatecalls or staticcalls to external extension contracts. Unlike `mint()`, `burn()`, `mintWithToken()`, and `burnForToken()`, the `updateUnitaryValue()` function does not have the `nonReentrant` modifier. The comment on line 79 states "Reentrancy protection provided by calling functions (mint, burn, depositV3, donate)" but `updateUnitaryValue()` is itself directly callable by anyone and is not always invoked from within a nonReentrant context.
- **Impact:** An attacker could manipulate oracle prices or app balances during the execution of `_updateNav()` by re-entering the contract through a callback in an external extension. If the oracle or apps extension triggers a callback (e.g. through a token with a transfer hook), the attacker could force a write to `poolTokens().unitaryValue` with a manipulated value. Subsequent mints or burns would use this corrupted NAV, allowing the attacker to mint pool tokens at a deflated price or burn at an inflated price, extracting value from other pool holders.
- **Recommendation:** Add the `nonReentrant` modifier to `updateUnitaryValue()`. Since the function writes to `poolTokens().unitaryValue` storage, it must be protected from reentrancy independently of its callers.

---

## [HIGH] Unsafe truncation from `uint256` to `uint208` in user balance accounting can silently overflow

- **Severity:** HIGH
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:200,206,270,283`
- **Description:** The `UserAccount.userBalance` field is `uint208` (as defined in `ISmartPoolState`), but the `mintedAmount` and `feePool` values are computed as `uint256`. The code uses unchecked casts like `uint208(mintedAmount)` and `uint208(feePool)` when adding to `userBalance`. While Solidity 0.8.28 has overflow protection for arithmetic operations, explicit type narrowing via `uint208(value)` does NOT revert on truncation -- it silently discards the upper bits. If `mintedAmount` exceeds `type(uint208).max` (approximately `4.1 * 10^62`), the balance written would be truncated, crediting the user with far fewer tokens than minted, while `totalSupply` would reflect the full `uint256` amount.
- **Impact:** While `uint208` is large enough for most practical scenarios with 18-decimal tokens, the mismatch between the `uint256` total supply accounting and `uint208` per-user balance creates an invariant violation. In a pool with a very low unitary value (e.g., after significant losses), a large deposit could theoretically produce a `mintedAmount` that exceeds `uint208.max`. The total supply would increase by the full amount while the user balance only records the truncated amount, creating tokens that exist in total supply but are not owned by anyone -- effectively a permanent loss of funds. The same truncation risk applies to the fee collector balance in `_allocateMintTokens` and `_allocateBurnTokens`.
- **Recommendation:** Use `SafeCast.toUint208()` from OpenZeppelin (already imported as `SafeCast` in the contract) instead of raw `uint208()` casts. This will revert if the value exceeds `type(uint208).max` rather than silently truncating. Alternatively, add explicit `require(mintedAmount <= type(uint208).max)` checks before the casts.

---

## [HIGH] `getStorageAt` and `getStorageSlotsAt` expose all pool storage including private variables to any caller

- **Severity:** HIGH
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/state/MixinStorageAccessible.sol:10-37`
- **Description:** The `MixinStorageAccessible` contract exposes two public view functions that allow any caller to read arbitrary storage slots from the pool proxy: `getStorageAt(uint256 offset, uint256 length)` reads sequential storage slots starting from any offset, and `getStorageSlotsAt(uint256[] memory slots)` reads arbitrary non-sequential slots. These functions have no access control. While storage is technically readable on-chain via `eth_getStorageAt` RPC calls, this contract provides a convenient high-level interface that makes it trivial to extract all pool state in a single call, including internal accounting data, user mappings, and operator approvals.
- **Impact:** An attacker can read the complete internal state of the pool in a single transaction, including all user balances, activation timestamps, operator approvals, fee collector settings, KYC provider address, virtual supply, and all transient storage slot derivations. This information can be used to: (1) precisely plan MEV extraction by knowing exact NAV values before they are emitted, (2) identify high-value targets for phishing by enumerating all pool holders and their balances, (3) front-run large burns by reading activation timestamps and balance amounts. While storage is ultimately public on the blockchain, providing a convenient API significantly lowers the barrier for exploitation.
- **Recommendation:** If this functionality is needed for off-chain integrations (e.g. Safe compatibility), restrict access to the pool owner or add it behind an opt-in mechanism. At minimum, document the security implications for pool operators and users.

---

## [MEDIUM] Fee-on-transfer and rebasing tokens are not handled in `_mint`, causing accounting discrepancies

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:151`
- **Description:** In `_mint()`, when `tokenIn` is a non-native ERC-20 token, the code executes `tokenIn.safeTransferFrom(msg.sender, address(this), amountIn)` and then proceeds to calculate `mintedAmount` based on the full `amountIn` value (minus the spread). However, for fee-on-transfer tokens (e.g. USDT with fees enabled, STA, PAXG), the actual amount received by the pool is less than `amountIn`. Similarly, for rebasing tokens (e.g. stETH, AMPL), the balance may change between the transfer and the minting calculation. The code does not check the actual balance received after the transfer.
- **Impact:** If a fee-on-transfer token is accepted as a mint token (via `setAcceptableMintToken`), users would receive more pool tokens than the actual value deposited. Over time, this would drain the pool because the NAV calculation (which uses actual `balanceOf`) would show less value than the total supply implies. Redeeming pool holders would receive less than expected, and the last redeemers would face significant losses as the pool becomes insolvent. The owner can mitigate this by not accepting such tokens, but there is no protocol-level protection.
- **Recommendation:** Implement a balance-before/balance-after pattern for token transfers: record `balanceOf(address(this))` before and after the `safeTransferFrom`, then use the actual delta as the `amountIn` for subsequent calculations. Alternatively, maintain an explicit allowlist/blocklist of token types and revert for known fee-on-transfer or rebasing tokens.

---

## [MEDIUM] Owner can set a malicious KYC provider to selectively block mints, effectively trapping existing holder funds

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinOwnerActions.sol:157-165` and `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:136-138`
- **Description:** The pool owner can set an arbitrary KYC provider contract via `setKycProvider()`. The only validation is that the address has code (i.e. is a contract). The KYC provider is then queried on every mint via `IKyc(kycProvider).isWhitelistedUser(recipient)`. While the KYC check does NOT apply to burns (allowing holders to exit), the owner could deploy a malicious KYC provider that returns `true` only for addresses controlled by the owner. This would allow the owner to mint at will while preventing any other user from minting, creating an asymmetric access to the pool.
- **Impact:** The owner can: (1) prevent new deposits from other users while continuing to deposit themselves, (2) create a situation where only the owner can mint tokens at favourable prices after manipulating the NAV downward, (3) combine this with fee changes to extract maximum value. While existing holders can still burn, the ability to selectively gate minting gives the owner an unfair advantage.
- **Recommendation:** Require the KYC provider to implement a known interface standard and be registered with the Authority contract. Add a timelock to KYC provider changes so holders have time to evaluate the new provider and exit if needed.

---

## [MEDIUM] `_allocateMintTokens` uses `unchecked` arithmetic for activation timestamp that could overflow in ~8,900 years

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:186-188`
- **Description:** The activation timestamp is calculated in an `unchecked` block: `activation = uint48(block.timestamp) + _getMinPeriod()`. The comment states "it is safe to use unchecked as max min period is 30 days". However, `uint48(block.timestamp)` itself is a truncation -- `uint48` overflows at timestamp `2^48 - 1 = 281,474,976,710,655` which is the year ~10,910 AD. While this is not practically exploitable in the near term, the `unchecked` block combined with the `uint48` truncation of `block.timestamp` means that when `block.timestamp` exceeds `type(uint48).max` (in the distant future), the truncation would produce a small timestamp value, and the activation would be set far in the past, allowing immediate burns and bypassing the lockup period entirely.
- **Impact:** In practice, this is not exploitable for thousands of years. However, the pattern is still a code quality concern: the `unchecked` block and `uint48` truncation combination creates a latent vulnerability. If the protocol intends to be an immutable, long-lived on-chain primitive, this should be addressed. More practically, if `_getMinPeriod()` returns 0 (which it currently cannot due to the `_MIN_LOCKUP` check, but could if the validation is modified), the activation would be set to `block.timestamp`, allowing immediate burn in the same block.
- **Recommendation:** Remove the `unchecked` block and use `SafeCast.toUint48(block.timestamp + _getMinPeriod())` to ensure no silent truncation occurs. The gas savings from `unchecked` are negligible compared to the external calls in the same function.

---

## [MEDIUM] ERC-20 `transfer`, `transferFrom`, and `approve` are implemented as no-ops that return false

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/sys/MixinAbstract.sol:7-19`
- **Description:** The `MixinAbstract` contract implements `transfer()`, `transferFrom()`, and `approve()` as empty functions that return the default value for `bool`, which is `false`. These functions are part of the ERC-20 interface that the pool token claims to implement (via `ISmartPool is IERC20`). Any caller invoking these methods will receive `false` as the return value without any revert, state change, or event emission. This silently fails without indication to the caller.
- **Impact:** Pool tokens cannot be transferred between users, which limits composability with DeFi protocols that expect standard ERC-20 behaviour. More critically, any protocol or wallet that calls `transfer()` and checks the return value will interpret `false` as a failed transfer, but any protocol that does NOT check the return value will assume the transfer succeeded when it did nothing. This could lead to accounting errors in integrated protocols. The `approve()` returning `false` means any `SafeERC20.safeApprove()` call will revert, preventing integration with protocols that use SafeERC20.
- **Recommendation:** These functions should `revert` with a descriptive error (e.g. `PoolTokensNotTransferable()`) instead of silently returning `false`. This makes the non-transferability explicit and prevents any integration that assumes ERC-20 compliance from silently failing.

---

## [MEDIUM] Oracle manipulation via `convertTokenAmount` can be exploited during mint/burn to extract value

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:163-164` and `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/state/MixinPoolValue.sol:163`
- **Description:** When minting with a non-base token via `mintWithToken()`, the `amountIn` is converted to the base token equivalent using `IEOracle(address(this)).convertTokenAmount()`. Similarly, during NAV calculation in `_computeTotalPoolValue()`, all active token balances are converted via `IEOracle(address(this)).convertBatchTokenAmounts()`. These oracle calls are routed through the fallback mechanism to an external oracle extension. If the oracle uses spot prices or short-TWAP windows, an attacker can manipulate the price of a token in the same block (e.g. via a flash loan that moves the price on a Uniswap pool) and then mint/burn at a manipulated NAV.
- **Impact:** An attacker could: (1) Flash-loan a large amount of an active token, (2) Manipulate its price upward on the underlying DEX the oracle reads from, (3) Call `mintWithToken()` or `burn()` to mint cheap pool tokens or burn at an inflated value, (4) Reverse the price manipulation by repaying the flash loan. The profit comes from the difference between the manipulated and true token values. The severity depends on the oracle implementation (TWAP length, manipulation resistance), which is external to this contract but critical to its security.
- **Recommendation:** Ensure the oracle extension uses manipulation-resistant pricing (e.g. long-window TWAPs of at least 30 minutes). Add explicit sanity checks on the converted amounts (e.g. maximum deviation from last stored NAV). Consider implementing a maximum single-transaction mint/burn amount as a percentage of total pool value to limit extraction.

---

## [MEDIUM] Proxy constructor uses hardcoded selector for `initializePool` without verification of return data content

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/proxy/src/Contract.sol:104-107`
- **Description:** The proxy constructor initialises the pool by delegatecalling to the implementation with a hardcoded selector `0x250e6de0` (which corresponds to `initializePool()`). The success check is `require(returnData.length == 0, "POOL_INITIALIZATION_FAILED_ERROR")`. This check asserts that the delegatecall returned empty data, but it does NOT check the boolean return value of the delegatecall itself. If the delegatecall succeeds (returns `true`) but the implementation's `initializePool()` reverts internally, the delegatecall would return `false` with revert data. However, the code stores the return data in `returnData` but never checks the first return value (success boolean). The pattern `(, bytes memory returnData) = implementation.delegatecall(...)` discards the success boolean.
- **Impact:** If the implementation's `initializePool()` reverts, the proxy constructor would still proceed because the first return value (success) is discarded. The `require(returnData.length == 0)` check would fail on a revert (since revert data length > 0), but this is a coincidental safety net, not an explicit success check. If a future implementation version changes `initializePool()` to return data on success, this check would falsely reject valid initializations.
- **Recommendation:** Explicitly check the delegatecall success boolean: `(bool success, bytes memory returnData) = implementation.delegatecall(...)` followed by `require(success && returnData.length == 0, ...)`. This makes the intent clear and is robust against implementation changes.

---

## [MEDIUM] `_getAndClearBalance` silently returns 0 for tokens whose `balanceOf` call fails, potentially undervaluing NAV

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/state/MixinPoolValue.sol:182-187`
- **Description:** In `_getAndClearBalance()`, if the `IERC20(token).balanceOf(address(this))` call fails (reverts), the function catches the error and returns 0 for that token's balance. This means the NAV calculation silently excludes any token whose `balanceOf` reverts, without logging or alerting. A token's `balanceOf` can fail if: (1) the token contract is paused, (2) the token contract is self-destructed, (3) the token contract is upgraded to an incompatible version, or (4) the token contract has a bug.
- **Impact:** If a significant token in the active tokens list has a failing `balanceOf`, the pool's NAV would be calculated without that token's value. This would make the NAV lower than the true value, causing: (1) minters to receive more pool tokens than they should (getting shares at a discount), (2) burners to receive less base token than they should (being underpaid). The owner would need to manually call `purgeInactiveTokensAndApps()` to remove the problematic token, but in the interim, the NAV is incorrect and can be exploited by informed actors.
- **Recommendation:** Instead of silently returning 0, emit an event indicating the failed balance query. Consider adding a flag that prevents mints/burns when any active token's balance cannot be queried, or revert the NAV calculation entirely when a known-active token returns 0 unexpectedly.
