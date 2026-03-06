# Rigoblock SmartPool -- QuillAI qs_skills audit report

**Target:** Rigoblock SmartPool (ERC-1967 proxy + implementation)
- Proxy: `0xEfa4bDf566aE50537A507863612638680420645C` (Solidity 0.8.17)
- Implementation: `0x1DB955265B8DC18715cAb12E805F9b71Fa545420` (Solidity 0.8.28)
- 45 Solidity source files, ~3,143 lines

**Methodology:** QuillAI/qs_skills -- 10 skills covering OWASP Smart Contract Top 10 and DeFi-specific vulnerability detection.

---

## [MEDIUM] Single EOA pool owner with no timelock or multisig

- **Severity:** MEDIUM
- **OWASP Category:** SC04 -- Access Control Vulnerabilities
- **Skill:** semantic-guard-analysis, proxy-upgrade-safety
- **File:** `/protocol/core/actions/MixinOwnerActions.sol:42-45`
- **Description:** The `onlyOwner` modifier checks `msg.sender == pool().owner`, and the pool owner is a single EOA (`0xcA9F5049c1Ea8FC78574f94B7Cf5bE5fEE354C31`). All owner-only functions -- `setOwner`, `setTransactionFee`, `changeSpread`, `changeMinPeriod`, `changeFeeCollector`, `setKycProvider`, `setAcceptableMintToken`, `purgeInactiveTokensAndApps` -- are gated only by this single EOA. There is no timelock, no multisig requirement, and no two-step ownership transfer pattern.
- **Impact:** If the owner EOA private key is compromised, the attacker can instantly: (1) set transaction fees to the maximum 1% and direct them to attacker-controlled address, (2) change the spread to 5% extracting value from every mint/burn, (3) reduce minimum lockup to 1 day then burn tokens, (4) set a malicious KYC provider to lock out all depositors, (5) transfer ownership to prevent recovery. Combining these manipulations could extract significant value from pool holders before they can react.
- **Recommendation:** Implement a two-step ownership transfer pattern (propose + accept). Consider requiring a timelock for parameter changes, or use a multisig wallet for the owner role. At minimum, implement `pendingOwner` pattern to prevent accidental or malicious ownership transfers.

---

## [MEDIUM] Non-functional ERC-20 transfer/transferFrom/approve methods

- **Severity:** MEDIUM
- **OWASP Category:** SC10 -- Denial of Service
- **Skill:** external-call-safety, state-invariant-detection
- **File:** `/protocol/core/sys/MixinAbstract.sol:9-18`
- **Description:** The `transfer()`, `transferFrom()`, and `approve()` ERC-20 methods are declared but have empty bodies -- they return `false` (the default) without performing any action. While this appears intentional to restrict transfers to mint/burn only, the functions do not revert, meaning callers receive a silent `false` return value. Any protocol, DEX, or aggregator integrating pool tokens via the standard ERC-20 interface will believe the call succeeded (or at best see `success == false`) without receiving a revert.
- **Impact:** Protocols that check only `success` from a low-level call (not the return boolean) would incorrectly believe transfers succeeded, leading to accounting desynchronisation. Pool tokens cannot be used in any standard DeFi composability (DEX trading, collateral, lending). The `allowance()` function also returns 0 for all queries, breaking the approval pattern silently.
- **Recommendation:** These functions should `revert` with a descriptive error (e.g., `PoolTokenNotTransferable()`) rather than returning false. This makes the non-transferability explicit and prevents silent failures in integrating contracts.

---

## [MEDIUM] Owner-controlled delegatecall to authority-approved adapters in fallback

- **Severity:** MEDIUM
- **OWASP Category:** SC04 -- Access Control Vulnerabilities
- **Skill:** proxy-upgrade-safety, semantic-guard-analysis
- **File:** `/protocol/core/sys/MixinFallback.sol:28-69`
- **Description:** The `fallback()` function routes unmatched selectors through two paths: (1) immutable extensions via `_extensionsMap.getExtensionBySelector()`, and (2) governance-approved adapters via `IAuthority(authority).getApplicationAdapter()`. For adapters, the decision to use `delegatecall` vs `staticcall` is based solely on `msg.sender == pool().owner`. When the owner calls an adapter function, it executes as a `delegatecall`, giving the adapter full write access to the pool's storage. The security of the pool therefore depends entirely on the Authority contract's governance correctly vetting every whitelisted adapter.
- **Impact:** If the Authority governance is compromised (or makes a mistake whitelisting a malicious adapter), any pool owner calling a function mapped to that adapter will execute arbitrary code in the pool's storage context. This could drain all pool funds, corrupt storage, or brick the pool. The trust chain is: Authority governance -> whitelisted adapter -> pool owner call -> delegatecall = full storage access.
- **Recommendation:** This is a design-level concern. Ensure the Authority contract has robust governance controls (multisig, timelock). Consider adding additional validation in the fallback, such as checking adapter code hash against a whitelist, or restricting delegatecall to a curated set of verified adapters.

---

## [MEDIUM] Unsafe uint208 casting of user balances without overflow check

- **Severity:** MEDIUM
- **OWASP Category:** SC02 -- Integer Overflow and Underflow
- **Skill:** input-arithmetic-safety
- **File:** `/protocol/core/actions/MixinActions.sol:200, 206, 270, 283`
- **Description:** User balances are stored as `uint208` in the `UserAccount` struct but are computed as `uint256`. The casts `uint208(feePool)` and `uint208(mintedAmount)` at lines 200, 206, 270, and 283 are performed without `SafeCast`. While Solidity 0.8+ has checked arithmetic for regular operations, explicit type casts (`uint208(x)`) do NOT revert on overflow -- they silently truncate. If `mintedAmount` or `feePool` exceeds `type(uint208).max` (approximately 4.1e62), the value will be silently truncated.
- **Impact:** In practice, the values are unlikely to reach `uint208` limits given that `totalSupply` is `uint256` and individual balances are a fraction of it. However, if the pool has extremely low `unitaryValue` (e.g., 1 wei per share) and a large deposit is made, the computed `mintedAmount` could theoretically overflow `uint208`. Silent truncation would credit the user with fewer tokens than computed, with the excess effectively lost. The `totalSupply` (stored as `uint256`) would reflect the full amount while the sum of `uint208` balances would not, breaking the `totalSupply == sum(balances)` invariant.
- **Recommendation:** Use `SafeCast.toUint208()` from OpenZeppelin (already imported as `SafeCast`) instead of raw `uint208()` casts. This will revert on overflow rather than silently truncating.

---

## [MEDIUM] NAV oracle manipulation via donation attack on balance-based valuation

- **Severity:** MEDIUM
- **OWASP Category:** SC01 -- Reentrancy Attacks / SC07 -- Oracle Manipulation
- **Skill:** oracle-flashloan-analysis
- **File:** `/protocol/core/state/MixinPoolValue.sol:170-188`
- **Description:** The pool's NAV calculation in `_getAndClearBalance()` reads token balances using `IERC20(token).balanceOf(address(this))` for ERC-20 tokens and `address(this).balance` for native currency. These spot balances are used directly to compute the pool's total value and thus the unitary value (price per share). An attacker could donate tokens directly to the pool contract (without going through `mint()`) to inflate the pool's perceived NAV before performing a burn, or conversely manipulate values to extract value.
- **Impact:** The donation attack scenario: (1) attacker mints pool tokens at fair NAV, (2) attacker donates tokens directly to the pool address, (3) NAV increases because `balanceOf(address(this))` reflects donated tokens, (4) attacker burns tokens receiving more base tokens than they deposited. The spread mechanism (up to 5%) provides partial protection, and the cross-chain virtual supply mechanism adds complexity, but the fundamental vulnerability remains. For native currency (ETH) pools, force-feeding via `selfdestruct` (or EIP-7702 equivalents) could also inflate `address(this).balance`.

  **Mitigating factors present in the code:**
  - The spread (default 10 bps, max 500 bps) on both mint and burn reduces profitability.
  - The minimum lockup period (1-30 days) prevents atomic flash-loan based attacks.
  - The minimum order size (`10 ** decimals / 1000`) prevents dust manipulation.
  - The `NavImpactLib.validateSupply()` check prevents extreme virtual supply manipulation.

- **Recommendation:** Consider tracking deposits internally rather than relying solely on `balanceOf()`. Alternatively, implement a virtual offset mechanism similar to ERC-4626 vaults (OpenZeppelin's `_decimalsOffset()`) to reduce the impact of donation attacks. The minimum lockup period is a strong mitigation but does not fully eliminate the attack vector for patient attackers.

---

## [MEDIUM] Transient-storage reentrancy guard does not protect cross-transaction state reading

- **Severity:** MEDIUM
- **OWASP Category:** SC01 -- Reentrancy Attacks
- **Skill:** reentrancy-pattern-analysis
- **File:** `/protocol/libraries/ReentrancyGuardTransient.sol:15-61`, `/protocol/core/actions/MixinActions.sol:80`
- **Description:** The contract uses EIP-1153 transient storage for its reentrancy guard (`ReentrancyGuardTransient`). The `nonReentrant` modifier correctly prevents re-entry within the same transaction for `mint()`, `mintWithToken()`, `burn()`, and `burnForToken()`. However, the `updateUnitaryValue()` function at line 80 is marked as `external override` but does NOT carry the `nonReentrant` modifier. It calls `_updateNav()` which reads token balances and updates the stored `unitaryValue`. The comment says "Reentrancy protection provided by calling functions (mint, burn, depositV3, donate)" but `updateUnitaryValue()` is publicly callable by anyone.
- **Impact:** Anyone can call `updateUnitaryValue()` at any time to force a NAV recalculation. While this does not directly enable a classic reentrancy exploit (there are no external calls that transfer value back to the caller from this function), it does mean the stored NAV can be updated based on manipulated balances. An attacker could: (1) donate tokens to inflate balances, (2) call `updateUnitaryValue()` to store the inflated NAV, (3) wait for lockup to expire and burn at the inflated price. The transient storage guard for balances in `_computeTotalPoolValue()` is only relevant within a single transaction, so cross-transaction manipulation is possible.
- **Recommendation:** Either add the `nonReentrant` modifier to `updateUnitaryValue()`, or restrict its caller to the pool owner, or accept the risk with documentation that the stored NAV is an approximation and actual mint/burn always recompute it.

---

## [MEDIUM] `safeTransferNative` uses 2300 gas stipend limiting smart contract wallet compatibility

- **Severity:** MEDIUM
- **OWASP Category:** SC10 -- Denial of Service
- **Skill:** external-call-safety, dos-griefing-analysis
- **File:** `/protocol/libraries/SafeTransferLib.sol:17-19`
- **Description:** The `safeTransferNative()` function uses `to.call{gas: 2300, value: amount}("")` with a hardcoded 2300 gas stipend. Post EIP-1884, certain operations (including `SLOAD`) cost more than 2300 gas. This means transfers to smart contract wallets (Gnosis Safe, Argent, etc.) or any contract with a `receive()` function that does more than emit a log event will fail.
- **Impact:** Pool holders using smart contract wallets will be unable to execute `burn()` for native-currency pools, as the ETH transfer to `msg.sender` via `safeTransferNative()` will revert. This constitutes a denial of service for an increasingly common class of users. Additionally, the spread transfer to `_getTokenJar()` uses the same function -- if the token jar is or becomes a contract, all burns will fail.
- **Recommendation:** Use `to.call{value: amount}("")` without a gas limit, and rely on the reentrancy guard for protection against reentrancy. The transient storage reentrancy guard is already in place for all `mint()` and `burn()` functions, making the 2300 gas stipend an unnecessary and harmful restriction.

---

## [MEDIUM] Pool owner can front-run depositors by manipulating spread and fees

- **Severity:** MEDIUM
- **OWASP Category:** SC09 -- Front-Running Vulnerabilities
- **Skill:** behavioral-state-analysis (front-running dimension)
- **File:** `/protocol/core/actions/MixinOwnerActions.sol:58-75, 168-173`
- **Description:** The pool owner can change the spread (up to 500 bps / 5%) and transaction fee (up to 100 bps / 1%) at any time without a timelock or advance notice. These parameters immediately affect the next mint/burn operations. A malicious or compromised owner could observe a pending large deposit in the mempool, front-run it by increasing the spread to 5%, and then reset it after the deposit executes.
- **Impact:** The owner can extract up to ~6% (5% spread + 1% transaction fee) from any depositor's mint or burn transaction by front-running the parameter changes. The `amountOutMin` slippage protection in `mint()` and `burn()` provides defence only if the user sets it appropriately, but many users set loose slippage tolerances.
- **Recommendation:** Implement a timelock on parameter changes (e.g., changes take effect after a 24-hour delay). Alternatively, enforce that parameter changes cannot take effect within the same block they are submitted, preventing same-block front-running.

---

## [MEDIUM] `updateUnitaryValue()` callable by anyone can be used for griefing via gas-expensive NAV computation

- **Severity:** MEDIUM
- **OWASP Category:** SC10 -- Denial of Service
- **Skill:** dos-griefing-analysis
- **File:** `/protocol/core/actions/MixinActions.sol:80-90`
- **Description:** The `updateUnitaryValue()` function is callable by anyone (`external override` with no access control and no `nonReentrant` modifier). This function triggers a full NAV recalculation via `_updateNav()`, which iterates over all active tokens and application positions, makes multiple external calls to oracles and ERC-20 `balanceOf()`, and uses transient storage for tracking. With up to 128 active tokens (`_MAX_UNIQUE_VALUES = 127`) and multiple application types, this can be a very gas-expensive operation.
- **Impact:** An attacker can repeatedly call `updateUnitaryValue()` to waste gas on the network. While this does not directly harm pool state (the NAV update is legitimate), it could be used as a griefing vector if bundled with other transactions or used to manipulate block gas usage. More importantly, since `updateUnitaryValue()` updates the stored NAV, an attacker can time the call to store an unfavourable NAV (e.g., during a temporary price dip in one of the active tokens), potentially affecting future mint calculations.
- **Recommendation:** Consider restricting `updateUnitaryValue()` to the pool owner, or adding the `nonReentrant` modifier to prevent it from being called during other pool operations.

---

## [MEDIUM] Unchecked `try/catch` on `IMinimumVersion` allows bypassing version requirements

- **Severity:** MEDIUM
- **OWASP Category:** SC04 -- Access Control Vulnerabilities
- **Skill:** proxy-upgrade-safety
- **File:** `/protocol/core/sys/MixinFallback.sol:42-44`
- **Description:** In the `fallback()` function, when routing to an authority-approved adapter, the code attempts to check a minimum version requirement via `try IMinimumVersion(target).requiredVersion()`. If the call fails (i.e., the adapter does not implement `requiredVersion()`), the catch block is empty, and execution continues. This means any adapter that does not implement `IMinimumVersion` will bypass the version check entirely.
- **Impact:** An older adapter that should no longer be compatible with the current pool implementation could be called if it was previously whitelisted by the Authority contract and does not implement the `requiredVersion()` function. This could lead to storage corruption or unexpected behaviour if the older adapter assumes a different storage layout or has known vulnerabilities that were fixed in newer versions.
- **Recommendation:** The empty catch is intentional for backwards compatibility, but consider logging when the version check is skipped, or implement a fallback minimum version requirement. At minimum, ensure the Authority governance process includes version compatibility verification before whitelisting adapters.

---

## [MEDIUM] Potential denial of service in `purgeInactiveTokensAndApps` via external call failures

- **Severity:** MEDIUM
- **OWASP Category:** SC10 -- Denial of Service
- **Skill:** dos-griefing-analysis
- **File:** `/protocol/core/actions/MixinOwnerActions.sol:77-137`
- **Description:** The `purgeInactiveTokensAndApps()` function iterates over all active tokens and applications in a triple-nested loop (tokens x apps x app-token-balances). The outer loop iterates `activeTokensLength`, which can be up to 128. For each token, it calls `IERC20(activeTokens[i]).balanceOf(address(this))` which is an external call. If the `IEApps(address(this)).getAppTokenBalances()` call reverts (caught in the try/catch, which re-reverts with the reason), the entire purge operation fails.
- **Impact:** If any single active application's balance query reverts, the owner cannot purge inactive tokens, which means the active tokens list can only grow (up to 128). As the list grows, the gas cost of every `mint()` and `burn()` operation increases (since `_computeTotalPoolValue()` iterates over all active tokens). In the worst case, with 128 tokens and many applications, `mint()` and `burn()` could become prohibitively expensive or exceed the block gas limit, effectively locking funds.
- **Recommendation:** Allow partial purging (purge only tokens, or only applications, or specific indices). Consider making the `getAppTokenBalances()` failure non-fatal for the purge operation, and allow the owner to force-remove tokens with explicit acknowledgment of NAV impact.

---

## [MEDIUM] Virtual supply manipulation via cross-chain mechanism could distort NAV

- **Severity:** MEDIUM
- **OWASP Category:** SC07 -- Oracle Manipulation
- **Skill:** state-invariant-detection, oracle-flashloan-analysis
- **File:** `/protocol/libraries/VirtualStorageLib.sol:24-26`, `/protocol/core/state/MixinPoolValue.sol:56-67`
- **Description:** The `VirtualStorageLib.updateVirtualSupply()` function modifies the `virtualSupply().supply` storage variable which is used in the NAV calculation. The effective supply is computed as `int256(totalSupply) + virtualSupply`. While `NavImpactLib.validateSupply()` ensures the effective supply does not drop below `totalSupply / MINIMUM_SUPPLY_RATIO (1/8)`, there is no upper bound check on positive virtual supply. The `updateVirtualSupply()` function is called from the cross-chain extension (via `delegatecall`), and its security depends entirely on the cross-chain extension's validation logic.
- **Impact:** If the cross-chain extension has a vulnerability, or if the Authority governance is compromised to whitelist a malicious cross-chain adapter, the virtual supply could be manipulated to inflate or deflate the effective supply. A large positive virtual supply would dilute the `unitaryValue` (more supply with same assets), while a negative virtual supply (limited to 87.5% of totalSupply) would inflate it. This directly affects the exchange rate for all mint and burn operations.
- **Recommendation:** Add upper bound validation for virtual supply in `updateVirtualSupply()`, such as limiting it to a percentage of `totalSupply`. Ensure the cross-chain extension has thorough access controls and validation of incoming cross-chain messages. The `MINIMUM_SUPPLY_RATIO` constraint is a good lower-bound protection; apply a similar mechanism for the upper bound.

---

## Summary

| # | Severity | Title | Skill |
|---|----------|-------|-------|
| 1 | MEDIUM | Single EOA pool owner with no timelock or multisig | semantic-guard-analysis, proxy-upgrade-safety |
| 2 | MEDIUM | Non-functional ERC-20 transfer/transferFrom/approve methods | external-call-safety, state-invariant-detection |
| 3 | MEDIUM | Owner-controlled delegatecall to authority-approved adapters in fallback | proxy-upgrade-safety, semantic-guard-analysis |
| 4 | MEDIUM | Unsafe uint208 casting of user balances without overflow check | input-arithmetic-safety |
| 5 | MEDIUM | NAV oracle manipulation via donation attack on balance-based valuation | oracle-flashloan-analysis |
| 6 | MEDIUM | Transient-storage reentrancy guard does not protect cross-transaction state reading | reentrancy-pattern-analysis |
| 7 | MEDIUM | `safeTransferNative` uses 2300 gas stipend limiting smart contract wallet compatibility | external-call-safety, dos-griefing-analysis |
| 8 | MEDIUM | Pool owner can front-run depositors by manipulating spread and fees | behavioral-state-analysis |
| 9 | MEDIUM | `updateUnitaryValue()` callable by anyone for griefing via gas-expensive NAV computation | dos-griefing-analysis |
| 10 | MEDIUM | Unchecked `try/catch` on `IMinimumVersion` allows bypassing version requirements | proxy-upgrade-safety |
| 11 | MEDIUM | Potential denial of service in `purgeInactiveTokensAndApps` via external call failures | dos-griefing-analysis |
| 12 | MEDIUM | Virtual supply manipulation via cross-chain mechanism could distort NAV | state-invariant-detection, oracle-flashloan-analysis |

---

## Skills with no findings at MEDIUM or above

The following QuillAI skills were applied but did not yield findings at MEDIUM severity or above:

### Signature & replay analysis

No signature verification (`ecrecover`, `ECDSA.recover`, EIP-712) is used in the SmartPool contract. All access control is via `msg.sender` checks. The operator system uses on-chain `setOperator()` calls rather than off-chain signatures. No findings.

### Integer overflow (Solidity 0.8+ checked math)

The contract uses Solidity 0.8.28 with checked arithmetic by default. The `unchecked` blocks are limited to:
- Line 186-188 in `MixinActions.sol`: `activation = uint48(block.timestamp) + _getMinPeriod()` -- safe because max lockup is 30 days, and `uint48` can hold timestamps until the year 8,919,535.
- Loop counter increments: standard pattern, safe.
- `SlotDerivation.offset()`: returns `bytes32(uint256(slot) + pos)` -- standard slot derivation, overflow is intentional wrapping for hash-based slot addressing.

The only concern is the explicit `uint208()` casts noted in Finding #4 above.

### Classic reentrancy

The `nonReentrant` modifier (transient storage based) is applied to all value-transferring entry points (`mint`, `mintWithToken`, `burn`, `burnForToken`). Within these functions, the CEI pattern is followed: state updates (totalSupply, user balances) occur before external calls (token transfers, safeTransferNative). The `_burn()` function updates `accounts().userAccounts[msg.sender]` and `poolTokens().totalSupply` before transferring tokens. No classic reentrancy findings above MEDIUM.
