# Rigoblock SmartPool - Forefy Multi-Expert Security Audit Report

**Target:** Rigoblock SmartPool (ERC-1967 proxy-based pool/fund management protocol)
- Proxy: `0xEfa4bDf566aE50537A507863612638680420645C` (Solidity 0.8.17)
- Implementation: `0x1DB955265B8DC18715cAb12E805F9b71Fa545420` (Solidity 0.8.28)
- 45 Solidity source files, ~3,143 lines
- Protocol type: Fund/Pool management with cross-chain virtual supply

**Methodology:** Forefy multi-expert framework (3 rounds: two independent security experts + triager validation)

---

## [CRITICAL] Single EOA owner controls all privileged pool operations with no timelock or multisig protection

- **Severity:** CRITICAL
- **File:** `/src/implementation/src/protocol/core/actions/MixinOwnerActions.sol:42-45`
- **Description:** The pool owner is a single EOA (`0xcA9F5049c1Ea8FC78574f94B7Cf5bE5fEE354C31`) that controls all privileged operations via the `onlyOwner` modifier. This includes: setting the fee collector (`changeFeeCollector`), changing the transaction fee up to 1% (`setTransactionFee`), changing the spread up to 5% (`changeSpread`), setting the KYC provider (`setKycProvider`), changing ownership (`setOwner`), and purging active tokens (`purgeInactiveTokensAndApps`). Critically, the `MixinFallback.fallback()` function at line 47 grants `delegatecall` access to any governance-whitelisted adapter ONLY to the owner (`shouldDelegatecall = msg.sender == pool().owner`). This means the owner can execute arbitrary state changes on the pool's storage via any adapter whitelisted through the Authority contract, with no timelock, no multisig requirement, and no delay. If the EOA key is compromised, an attacker gains instant, unrestricted control over all pool funds and parameters.
- **Impact:** Complete fund loss. A compromised owner key allows: (1) immediate extraction of all pool assets via owner-only delegatecall to malicious or existing adapters, (2) redirecting fees to attacker-controlled addresses, (3) setting maximum spread (5%) and transaction fee (1%) to extract value from all future mints/burns, (4) changing the owner to permanently lock out the legitimate operator. There is no recovery mechanism, no timelock, and no emergency pause.
- **Recommendation:** Replace the single EOA owner with a multisig (e.g., Gnosis Safe with at least 3-of-5 signers). Implement a timelock (24-48 hours minimum) for all critical parameter changes (fee collector, transaction fee, spread, KYC provider, ownership transfer). Add an emergency pause mechanism governed by the multisig. Consider implementing a two-step ownership transfer pattern (propose + accept) instead of the current instant `setOwner`.

---

## [HIGH] Fallback delegatecall to governance-whitelisted adapters allows arbitrary storage manipulation by owner

- **Severity:** HIGH
- **File:** `/src/implementation/src/protocol/core/sys/MixinFallback.sol:28-69`
- **Description:** The `fallback()` function implements a two-tier extension routing system. For selectors not found in the immutable `_extensionsMap`, it queries the `Authority` contract via `getApplicationAdapter(msg.sig)`. If the caller is the pool owner, the call is executed as `delegatecall` (line 54), giving the target adapter full access to the pool's storage. The security of this mechanism depends entirely on the governance quality of the Authority contract -- if a malicious or buggy adapter is whitelisted by the Authority, any pool owner can immediately use it to corrupt pool state. Additionally, the `try/catch` around `IMinimumVersion(target).requiredVersion()` (lines 42-44) silently catches failures, meaning adapters that do not implement this interface will still be callable. The version check is thus a soft guard that can be bypassed by deploying adapters without the `IMinimumVersion` interface.

    The fallback function does not apply reentrancy protection (`nonReentrant`), meaning adapters executed via delegatecall could potentially be re-entered through callbacks. While the `onlyDelegateCall` modifier prevents direct calls to the implementation, it does not protect against reentrancy within a delegated adapter execution.
- **Impact:** If the Authority whitelists a vulnerable or malicious adapter, every pool owner can exploit it to drain all pools sharing the same implementation. This is a protocol-wide systemic risk. The lack of reentrancy guard on the fallback means adapters must individually implement their own protection.
- **Recommendation:** (1) Add a reentrancy guard to the fallback function. (2) Implement a per-pool allowlist of approved adapters, so pool owners can opt out of newly whitelisted adapters. (3) Make the version check mandatory (revert on failure) rather than using try/catch. (4) Implement a timelock on the Authority contract for whitelisting new adapters, giving pool holders time to exit before a new adapter becomes active.

---

## [HIGH] Pool token balance stored as uint208 can silently truncate for large supply pools

- **Severity:** HIGH
- **File:** `/src/implementation/src/protocol/core/actions/MixinActions.sol:200-206`
- **Description:** User balances are stored as `uint208` in the `UserAccount` struct (defined in `ISmartPoolState.sol:88`). In `_allocateMintTokens` (line 200 and 206), the minted amount is cast to `uint208` via `uint208(feePool)` and `uint208(mintedAmount)`. These are unchecked casts -- if `mintedAmount` or `feePool` exceeds `type(uint208).max` (~4.11e62), the value silently truncates. Similarly in `_allocateBurnTokens` (lines 270 and 282), the burn amount and fee are cast to `uint208`. While `totalSupply` is a full `uint256`, individual user balances are constrained to `uint208`.

    For an 18-decimal pool token, `type(uint208).max` is approximately 4.11e44 tokens, which is astronomically large and unlikely to be reached for a single user in practice. However, if pool decimals are much higher (up to 18 is standard but the architecture supports arbitrary ERC20 base tokens), and the unitary value drops extremely low, the minted token amount per unit of base currency could theoretically grow very large. The check `assert(decimals >= 6)` in `MixinInitializer.sol:41` mitigates the extreme low-decimal case.

    The `uint208` truncation is more concerning for the cumulative addition pattern: `accounts().userAccounts[feeCollector].userBalance += uint208(feePool)` -- if the fee collector already has a large balance and receives repeated fees, the addition could overflow `uint208`. This addition is unchecked and does not use SafeCast.
- **Impact:** In an edge case where the fee collector's accumulated balance approaches `type(uint208).max`, subsequent fee allocations would silently truncate, causing the fee collector to lose tokens. While practically unlikely with 18-decimal tokens, it represents an unguarded arithmetic boundary. More critically, the `totalSupply` (uint256) and user balances (uint208) could desynchronise if truncation occurs, breaking the fundamental accounting invariant.
- **Recommendation:** Use `SafeCast.toUint208()` for all casts to `uint208` to ensure reversion on overflow rather than silent truncation. Alternatively, consider upgrading `userBalance` to `uint256` since the `UserAccount` struct packing (uint208 + uint48 = 256 bits) is an optimisation that introduces this truncation risk.

---

## [HIGH] updateUnitaryValue is publicly callable without reentrancy protection and allows NAV manipulation timing

- **Severity:** HIGH
- **File:** `/src/implementation/src/protocol/core/actions/MixinActions.sol:80-90`
- **Description:** The `updateUnitaryValue()` function is `external` and callable by anyone without the `nonReentrant` modifier. The comment on line 79 states "Reentrancy protection provided by calling functions (mint, burn, depositV3, donate)" -- but this function is also callable directly by external callers, not only via mint/burn. When called directly, there is no reentrancy protection.

    The function calls `_updateNav()` which in turn calls `_computeTotalPoolValue()` (in `MixinPoolValue.sol`). This function makes multiple external calls: `IEApps(address(this)).getAppTokenBalances()` and `IEOracle(address(this)).convertBatchTokenAmounts()`, both via `address(this)` which triggers the fallback and routes to extensions. If any of these extensions make callbacks that re-enter the pool, the NAV calculation could be manipulated.

    Additionally, because anyone can call `updateUnitaryValue()` at any time, an attacker can strategically update the stored NAV before or after manipulating the token balances that feed into the NAV calculation. For example, an attacker could:
    1. Manipulate the oracle price (e.g., through a Uniswap pool manipulation)
    2. Call `updateUnitaryValue()` to lock in the manipulated NAV
    3. Mint pool tokens at the artificially low NAV
    4. Wait for oracle correction
    5. Burn pool tokens at the restored NAV
- **Impact:** An attacker with the ability to temporarily manipulate oracle prices can extract value from the pool by timing `updateUnitaryValue()` calls to mint cheap and burn expensive. The lack of reentrancy guard also means extension callbacks during NAV computation could re-enter and corrupt the calculation.
- **Recommendation:** (1) Add the `nonReentrant` modifier to `updateUnitaryValue()`. (2) Consider restricting who can call `updateUnitaryValue()` (e.g., only the owner or a trusted keeper). (3) Implement a TWAP or time-weighted mechanism for NAV updates to prevent single-block manipulation. (4) Add a minimum time interval between NAV updates to prevent rapid-fire manipulation.

---

## [HIGH] Virtual supply manipulation through cross-chain operations can distort NAV calculations

- **Severity:** HIGH
- **File:** `/src/implementation/src/protocol/libraries/VirtualStorageLib.sol:24-26` and `/src/implementation/src/protocol/core/state/MixinPoolValue.sol:56-67`
- **Description:** The `VirtualStorageLib.updateVirtualSupply()` function directly modifies a signed int256 virtual supply value by adding a delta, with no access control in the library itself. Access control is expected to be enforced by the calling extension (ECrosschain), which is executed via delegatecall from the fallback. The virtual supply directly impacts NAV calculation in `_updateNav()`:

    ```solidity
    int256 effectiveSupply = int256(components.totalSupply) + virtualSupply;
    components.unitaryValue = (components.netTotalValue * 10 ** components.decimals) / components.totalSupply;
    ```

    The `validateSupply()` function in `NavImpactLib.sol:71-77` provides a minimum ratio check (effective supply must be at least 1/8 of total supply), but this still allows significant manipulation. A negative virtual supply of up to 7/8 of total supply is permitted, which would inflate the unitary value by up to 8x.

    The `validateNavImpact()` function provides percentage-based validation, but it is only called from the cross-chain extension (not from `_updateNav()` directly). If the cross-chain extension has bugs or if the Authority whitelists a malicious adapter that calls `updateVirtualSupply()` directly, the virtual supply can be manipulated to inflate or deflate the NAV.

    Furthermore, the `effectiveSupply` calculation on line 60 replaces `components.totalSupply` (line 67), meaning the virtual supply adjustment affects the divisor in the unitary value calculation. A large negative virtual supply increases unitary value, making existing shares worth more (diluting new minters). A large positive virtual supply decreases unitary value, making existing shares worth less (benefiting new minters at the expense of existing holders).
- **Impact:** Manipulation of virtual supply through compromised or buggy cross-chain extensions can inflate/deflate NAV by up to 8x within the permitted bounds, enabling extraction of significant value from pool holders.
- **Recommendation:** (1) Add explicit access control to `updateVirtualSupply()` to ensure only the authorised cross-chain extension can call it. (2) Tighten the `MINIMUM_SUPPLY_RATIO` from 8 to a more conservative value (e.g., 2, meaning effective supply must be at least 50% of total supply). (3) Add rate limiting on virtual supply changes per time period. (4) Consider requiring multi-party authorisation for virtual supply modifications above a threshold.

---

## [MEDIUM] ERC20 transfer/transferFrom/approve functions are non-functional, breaking composability

- **Severity:** MEDIUM
- **File:** `/src/implementation/src/protocol/core/sys/MixinAbstract.sol:9-18`
- **Description:** The `MixinAbstract` contract implements the IERC20 `transfer`, `transferFrom`, and `approve` functions as empty no-ops that always return `false` (default value). These functions do not revert -- they silently return false. The `allowance` function similarly returns 0 for all queries.

    While this is a deliberate design choice (pool tokens are non-transferable by design), the implementation is problematic because:
    1. The functions return `false` instead of reverting, which is non-standard. Many DeFi protocols and wallets check the return value of ERC20 functions and may silently fail rather than revert.
    2. The `Transfer` events emitted during mint/burn (lines 202, 208, 285, 289 in MixinActions.sol) suggest ERC20 compatibility, but the transfer functions do not work.
    3. Any protocol or contract that attempts to interact with pool tokens as standard ERC20 tokens will silently fail.

    This breaks the principle of least surprise and could lead to integration bugs where calling contracts assume the transfer succeeded (if they don't check the return value) or silently lose tokens.
- **Impact:** Any DeFi protocol or smart contract attempting to transfer pool tokens will silently fail. This prevents pool tokens from being used as collateral in lending protocols, traded on DEXes, or used in any composable DeFi application. While this may be intentional, the silent failure (returning false instead of reverting) could cause unexpected behaviour in integrations that don't check return values.
- **Recommendation:** Either (1) have the functions revert with a clear error message like `PoolTokenNotTransferable()` to prevent silent failures, or (2) implement proper ERC20 transfer functionality if pool token transferability is desired. The current implementation of returning false without reverting is the worst of both approaches.

---

## [MEDIUM] Spread fee applied on both mint and burn creates double-fee extraction with no maximum cap check on compound effect

- **Severity:** MEDIUM
- **File:** `/src/implementation/src/protocol/core/actions/MixinActions.sol:141,249`
- **Description:** The spread fee is charged on both minting (line 141) and burning (line 249). On mint, the spread is deducted from the deposit amount before calculating pool tokens: `uint256 spread = (amountIn * _getSpread()) / _SPREAD_BASE`. On burn, the spread is deducted from the withdrawal revenue: `uint256 spread = (netRevenue * _getSpread()) / _SPREAD_BASE`.

    With the maximum spread of 500 basis points (5%), a round-trip (mint then burn) costs the user approximately 9.75% (5% on entry + 5% of the reduced position on exit). Combined with the maximum transaction fee of 100 basis points (1%) which is also charged on both mint and burn, the total round-trip cost can reach approximately 11.66%.

    The owner can change the spread at any time (up to `_MAX_SPREAD = 500` bps) via `changeSpread()`. There is no timelock on this change, and it takes effect immediately for the next mint/burn operation. An adversarial owner could:
    1. Set spread to 0 to attract deposits
    2. After sufficient deposits, increase spread to maximum (500 bps)
    3. Users are now locked into paying 5% on exit, plus the mandatory minimum lockup period of 1 day prevents them from front-running the spread change

    The minimum lockup period (`_MIN_LOCKUP = 1 days`) means users cannot react to spread changes within 24 hours.
- **Impact:** Pool operator can sandwich users between low and high spread periods. Users who deposited under a low spread are forced to exit at a high spread due to the lockup period, losing up to 5% of their principal unexpectedly. Combined fees can extract up to ~12% on a round trip.
- **Recommendation:** (1) Implement a timelock on spread changes, at minimum equal to the maximum lockup period (30 days), so all existing depositors can exit at the spread they entered under. (2) Consider applying the spread to only mint OR burn, not both. (3) Record the spread at which each user entered and allow them to exit at that same spread, or the current spread, whichever is lower.

---

## [MEDIUM] safeTransferNative uses 2300 gas limit which will fail for smart contract recipients post-EIP-1153

- **Severity:** MEDIUM
- **File:** `/src/implementation/src/protocol/libraries/SafeTransferLib.sol:17-19`
- **Description:** The `safeTransferNative` function uses a hardcoded gas limit of 2300: `(bool success, ) = to.call{gas: 2300, value: amount}("")`. This is the legacy gas stipend amount that was historically considered safe for preventing reentrancy (matching `transfer()` and `send()` behaviour).

    However, since EIP-1153 (Cancun upgrade, March 2024), the TSTORE opcode costs only 100 gas, which fits within the 2300 gas stipend. This means a receiving contract can now execute transient storage operations (including reentrancy guard manipulations) within a 2300-gas callback. While the SmartPool itself uses `ReentrancyGuardTransient` (which should prevent re-entry through mint/burn), the 2300 gas stipend is no longer a reliable reentrancy prevention mechanism.

    More practically, the 2300 gas limit causes failures when sending ETH to:
    - Gnosis Safe multisig wallets (require >2300 gas for receive)
    - Smart contract wallets with access control logic in their receive/fallback
    - Proxy wallets (need gas for delegatecall in fallback)

    This function is called in `_mint` (line 149) for spread fees and in `_burn` (lines 255-256) for both user payouts and spread fees when the base token is ETH (address(0)). If the `tokenJar` address is a smart contract that requires more than 2300 gas, all spread fee transfers will fail, blocking all mint and burn operations for native ETH pools.
- **Impact:** (1) All ETH pool operations (mint/burn) will permanently fail if the `tokenJar` is a smart contract requiring more than 2300 gas to receive ETH. (2) Users with smart contract wallets cannot burn pool tokens from ETH-denominated pools. (3) The 2300 gas limit no longer prevents transient storage reentrancy since EIP-1153.
- **Recommendation:** Replace `{gas: 2300}` with a reasonable higher limit (e.g., `{gas: 10000}`) or remove the gas limit entirely and rely on the `ReentrancyGuardTransient` modifier for reentrancy protection. Verify that the `tokenJar` address can receive ETH with the given gas stipend.

---

## [MEDIUM] getStorageAt and getStorageSlotsAt expose full storage read access to any caller

- **Severity:** MEDIUM
- **File:** `/src/implementation/src/protocol/core/state/MixinStorageAccessible.sol:10-37`
- **Description:** The `getStorageAt` and `getStorageSlotsAt` functions are `public view` with no access control, allowing anyone to read arbitrary storage slots from the proxy. While storage is technically accessible to anyone through eth_getStorageAt RPC calls, these functions make it significantly easier to read and decode packed storage values, including:
    - All user balances and activation timestamps
    - Pool parameters (fees, spread, KYC provider)
    - Virtual supply values
    - Application bitmask and token registry data

    These functions are derived from the Gnosis Safe pattern (as noted in the comment) and are designed for offchain consumption. However, they also expose an unbounded loop (`for (uint256 index = 0; index < length; index++)`) where `length` is caller-controlled. This creates a denial-of-service vector: a caller can pass an extremely large `length` value, causing the function to consume all available gas. While view functions executed off-chain do not incur gas costs, if these functions are called on-chain by other contracts (e.g., in a composed transaction), the gas cost is real and unbounded.
- **Impact:** (1) Full storage readability simplifies attack reconnaissance. (2) Unbounded loop in `getStorageAt` can be used for gas griefing if called on-chain by another contract.
- **Recommendation:** (1) Add a maximum `length` parameter to `getStorageAt` (e.g., `require(length <= 256)`). (2) Consider whether this level of storage access is needed and if it can be restricted to the pool owner.

---

## [MEDIUM] First depositor can manipulate initial NAV for share price advantage

- **Severity:** MEDIUM
- **File:** `/src/implementation/src/protocol/core/state/MixinPoolValue.sol:53-54`
- **Description:** When a pool's `unitaryValue` is 0 (uninitialised, first mint), the code sets `components.unitaryValue = 10 ** components.decimals` (line 54) as the initial price of 1 token = 1 base token unit. This initial value is used to calculate the first minted amount:

    ```solidity
    uint256 mintedAmount = (amountIn * 10 ** components.decimals) / components.unitaryValue;
    ```

    For the first mint, this simplifies to `mintedAmount = amountIn`, a 1:1 ratio.

    However, after the first mint, the second minter's share price is calculated based on the NAV. If the first minter deposits a small amount, then donates (sends tokens directly to the pool without minting) a large amount of the base token, the unitary value will spike when NAV is recalculated on the second mint. This is a variant of the classic first-depositor share inflation attack.

    Specifically:
    1. First depositor mints with minimum amount (1e15 for 18-decimal token, due to `_MINIMUM_ORDER_DIVISOR = 1e3`)
    2. First depositor sends a large amount of base token directly to the pool contract
    3. On the next `_updateNav()`, the pool's `netTotalValue` includes the donated tokens, inflating `unitaryValue`
    4. Second depositor receives fewer shares for their deposit due to the inflated unitary value
    5. First depositor burns their shares and receives back their initial deposit plus a large portion of the second depositor's funds

    The mitigation is partial: the spread fee (minimum 10 bps, default) applies to both the attacker and victim, and the minimum order size reduces the precision of the attack. However, with a large enough donation relative to the victim's deposit, the attack remains profitable.
- **Impact:** First depositor can steal a significant portion of the second depositor's funds through share price inflation. The minimum order size and spread provide partial mitigation but do not eliminate the attack for large pools.
- **Recommendation:** (1) Implement a dead shares mechanism: on first deposit, mint a small number of shares (e.g., 1000) to a burn address to establish a minimum share price floor. (2) Implement a virtual offset pattern similar to OpenZeppelin's ERC4626 `_decimalsOffset()`. (3) Track internal assets separately from `balanceOf(address(this))` so direct donations do not affect NAV. (4) Set a minimum first deposit amount high enough to make the inflation attack economically infeasible.

---

## [MEDIUM] Pool owner can manipulate NAV by selectively purging active tokens

- **Severity:** MEDIUM
- **File:** `/src/implementation/src/protocol/core/actions/MixinOwnerActions.sol:77-137`
- **Description:** The `purgeInactiveTokensAndApps()` function allows the pool owner to remove tokens from the `activeTokensSet` if their balance is <= 1 wei and they are not in active applications. However, this creates a manipulation vector:

    1. Owner accepts a token via `setAcceptableMintToken()`
    2. Users mint with that token, adding it to `activeTokensSet`
    3. Owner trades the token away via an adapter (using owner-only delegatecall)
    4. Token balance drops to 0 or 1 wei
    5. Owner calls `purgeInactiveTokensAndApps()` to remove the token from `activeTokensSet`
    6. The token is no longer counted in NAV calculations
    7. If the owner later reacquires the token, it is invisible to NAV until re-added

    This allows the owner to hide assets from the NAV calculation or selectively include/exclude tokens to manipulate the share price. Since the owner has delegatecall access to adapters, they can trade tokens in and out of the pool's possession.

    The code comment at line 99 notes "base token is never pushed to active list for gas savings, we can safely remove any unactive token" -- but this safety assumes the owner acts honestly.
- **Impact:** Pool owner can selectively hide or reveal assets in NAV calculations, manipulating the share price to profit at the expense of other pool holders. This is particularly concerning given the owner is a single EOA with no oversight.
- **Recommendation:** (1) Remove the ability for the owner to purge tokens that have been used for minting -- once users have deposited via a token, it should remain in the active set permanently. (2) Add a delay between purging and NAV updates to give holders time to react. (3) Require a governance vote or multi-party approval for token purging.
