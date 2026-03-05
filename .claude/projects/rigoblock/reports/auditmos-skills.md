# Rigoblock SmartPool -- Auditmos skills security audit

**Target:** Rigoblock SmartPool v4.1.1, ERC-1967 proxy-based pool/fund management protocol
- Proxy: `0xEfa4bDf566aE50537A507863612638680420645C` (Solidity 0.8.17)
- Implementation: `0x1DB955265B8DC18715cAb12E805F9b71Fa545420` (Solidity 0.8.28, SmartPool)
- 45 Solidity source files, ~3,143 lines
- Pool Owner: `0xcA9F5049c1Ea8FC78574f94B7Cf5bE5fEE354C31` (EOA)

**Methodology:** 14 auditmos skills applied systematically. Skills not applicable to this protocol (audit-auction, audit-clm, audit-lending, audit-liquidation, audit-liquidation-calculation, audit-liquidation-dos, audit-unfair-liquidation) were evaluated for relevance and skipped as the protocol has no auction, concentrated liquidity, lending, or liquidation mechanisms.

**Skills applied:**
1. audit-math-precision -- NAV calculations, fee calculations, share price minting/burning
2. audit-oracle -- Oracle extension for token price conversion, NAV computation
3. audit-reentrancy -- delegatecall/extension system, mint/burn token transfers
4. audit-state-validation -- ownership transfer, input validation, cross-function state
5. audit-slippage -- mint/burn slippage protection, amountOutMin
6. audit-staking -- deposit/withdraw with lockup (vault share model)
7. audit-signature -- no EIP-712 or ecrecover usage found (skipped)

---

## [MEDIUM] Unsafe downcast of mintedAmount to uint208 can silently truncate large balances

- **Severity:** MEDIUM
- **Auditmos Skill:** audit-math-precision (Pattern #6: Downcast Overflow)
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:200,206`
- **Description:** In `_allocateMintTokens`, the `mintedAmount` (uint256) is added to `userBalance` which is a `uint208`. The downcast from uint256 to uint208 is performed implicitly via the `+=` operator on a `uint208` storage variable. While Solidity 0.8.x checked arithmetic will catch overflow on the `+=` operation itself (reverting if the sum exceeds `type(uint208).max`), the individual `feePool` value at line 200 is cast to `uint208` without SafeCast:

  ```solidity
  accounts().userAccounts[feeCollector].userBalance += uint208(feePool);  // line 200
  accounts().userAccounts[recipient].userBalance += uint208(mintedAmount); // line 206
  ```

  The explicit `uint208(feePool)` cast will silently truncate if `feePool > type(uint208).max` (approx 4.1e62). While practically unlikely with 18-decimal tokens (would require ~4.1e44 tokens), with extremely high-supply low-decimal tokens (6 decimals), the truncation threshold is lower. The same pattern appears in `_allocateBurnTokens` at line 283.

  Note: The Solidity 0.8.x `+=` on the `uint208` would catch overflow of the *sum*, but the individual `uint208()` cast of `feePool` or `mintedAmount` before addition is an unchecked narrowing cast that truncates silently.
- **Impact:** If a token with very high total supply is used as a base token, the explicit `uint208()` cast could silently truncate fee amounts, resulting in fee collectors receiving less than owed. The `+=` on the storage variable would then succeed with the truncated value.
- **Recommendation:** Use `SafeCast.toUint208()` for all narrowing casts to ensure they revert rather than silently truncate:
  ```solidity
  accounts().userAccounts[feeCollector].userBalance += feePool.toUint208();
  accounts().userAccounts[recipient].userBalance += mintedAmount.toUint208();
  ```

---

## [MEDIUM] Single-step ownership transfer allows accidental loss of pool control

- **Severity:** MEDIUM
- **Auditmos Skill:** audit-state-validation (Pattern #1: Unchecked 2-Step Ownership Transfer)
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinOwnerActions.sol:176-182`
- **Description:** The `setOwner` function implements a single-step ownership transfer. While it correctly checks `newOwner != address(0)`, there is no two-step accept/confirm pattern. If the owner accidentally sets ownership to a wrong address (typo, wrong checksum, contract without ability to interact), control of the pool is permanently lost:

  ```solidity
  function setOwner(address newOwner) public override onlyOwner {
      require(newOwner != _ZERO_ADDRESS, PoolNullOwnerInput());
      require(newOwner != pool().owner, OwnerActionInputIsSameAsCurrent());
      address oldOwner = pool().owner;
      pool().owner = newOwner;
      emit NewOwner(oldOwner, newOwner);
  }
  ```

  The pool owner is an EOA (`0xcA9F5049c1Ea8FC78574f94B7Cf5bE5fEE354C31`) controlling all owner functions including fee parameters, KYC provider, spread, min period, and purging tokens. Loss of ownership means permanent loss of all administrative control over the pool and its parameters.

  Additionally, there is no validation that `newOwner` is not a contract that cannot call back. This is especially dangerous because the owner is an EOA and the fallback delegatecall system gives the owner exclusive write access to adapter functions.
- **Impact:** Permanent loss of administrative control over the pool, including inability to change fees, update KYC provider, manage accepted tokens, or purge inactive tokens. All pool parameters become frozen at their current values.
- **Recommendation:** Implement a two-step ownership transfer pattern (e.g., OpenZeppelin's `Ownable2Step`):
  ```solidity
  address public pendingOwner;

  function transferOwnership(address newOwner) external onlyOwner {
      pendingOwner = newOwner;
  }

  function acceptOwnership() external {
      require(msg.sender == pendingOwner);
      pool().owner = msg.sender;
  }
  ```

---

## [MEDIUM] The `updateUnitaryValue` function lacks reentrancy protection and can be called by anyone to manipulate NAV timing

- **Severity:** MEDIUM
- **Auditmos Skill:** audit-reentrancy (Pattern #3: Cross-Function Reentrancy)
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:80-90`
- **Description:** The `updateUnitaryValue()` function is `external` without the `nonReentrant` modifier and is callable by anyone. The comment says "Reentrancy protection provided by calling functions (mint, burn, depositV3, donate)" -- but this function is itself a standalone entry point, not just called internally:

  ```solidity
  /// @dev Reentrancy protection provided by calling functions (mint, burn, depositV3, donate)
  function updateUnitaryValue() external override returns (NetAssetsValue memory navParams) {
      NavComponents memory components = _updateNav();
      // ...
  }
  ```

  The `_updateNav()` function makes external calls: `IEOracle(address(this)).hasPriceFeed()`, `IEApps(address(this)).getAppTokenBalances()`, `IEOracle(address(this)).convertBatchTokenAmounts()`, and `IERC20(token).balanceOf()`. These are delegatecall/staticcall to extensions, but `getAppTokenBalances` in particular could interact with external protocols (Uniswap V4, GRG staking) that may have callback mechanisms.

  While the function writes to storage (`poolTokens().unitaryValue`), the lack of reentrancy guard means it could potentially be re-entered during one of these external calls. An attacker who controls a token in the active set (via a malicious ERC20 `balanceOf` callback) could re-enter `updateUnitaryValue` to manipulate the stored NAV.

  The more practical concern is that anyone can call this function at an advantageous time (e.g., right before a large mint/burn) to force a NAV update that uses oracle prices at that specific moment, creating a form of NAV timing manipulation.
- **Impact:** Potential NAV manipulation via timing attacks. An attacker could force NAV updates at strategic moments to benefit their subsequent mint/burn operations. In extreme cases, if a malicious token is in the active set, cross-function reentrancy through `balanceOf` callbacks could corrupt the stored unitary value.
- **Recommendation:** Add the `nonReentrant` modifier to `updateUnitaryValue()`:
  ```solidity
  function updateUnitaryValue() external override nonReentrant returns (NetAssetsValue memory navParams) {
  ```

---

## [MEDIUM] Read-only reentrancy risk via `getPoolTokens()` returning stale unitaryValue during external calls

- **Severity:** MEDIUM
- **Auditmos Skill:** audit-reentrancy (Pattern #4: Read-Only Reentrancy)
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/state/MixinPoolState.sol:99-106` and `MixinPoolValue.sol:32-90`
- **Description:** During a `mint()` or `burn()` operation, `_updateNav()` is called which makes external calls to oracle extensions and token `balanceOf`. The `unitaryValue` in storage is only updated at the end of `_updateNav()` (line 86-89 of MixinPoolValue.sol). If any of the external calls during NAV computation trigger a callback that reads `getPoolTokens()` (a public view function), the returned `unitaryValue` will be the *old* stored value, not the currently-being-computed value.

  ```solidity
  // MixinPoolValue._updateNav() line 86-89:
  if (components.unitaryValue != poolTokens().unitaryValue) {
      poolTokens().unitaryValue = components.unitaryValue;  // updated AFTER external calls
      emit NewNav(msg.sender, address(this), components.unitaryValue);
  }
  ```

  Any external protocol that relies on `getPoolTokens().unitaryValue` for pricing RGP pool tokens would read a stale value during the window when `_updateNav()` is executing. This is the classic read-only reentrancy pattern.

  The `nonReentrant` modifier on `mint`/`burn` prevents re-entering those functions, but it does NOT prevent external contracts from reading view functions like `getPoolTokens()`, `totalSupply()`, or `balanceOf()` during the callback window.
- **Impact:** External protocols integrating with RGP pool tokens (e.g., using them as collateral or for pricing) could receive stale `unitaryValue` during the NAV update window, leading to incorrect valuations. This is exploitable if pool tokens are used in DeFi composability (lending, trading, etc.).
- **Recommendation:** This is an inherent challenge with read-only reentrancy. Mitigations include:
  1. Document the stale-state window clearly for integrators
  2. Consider adding a "locked" flag that view functions can check and that integrators can query
  3. Update `unitaryValue` at the start of computation (optimistic update) and correct at the end

---

## [MEDIUM] Burn operation transfers tokens before fully completing state updates (CEI violation in spread payment)

- **Severity:** MEDIUM
- **Auditmos Skill:** audit-reentrancy (Pattern #2: State Update After External Call)
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:254-260`
- **Description:** In the `_burn` function, after computing `netRevenue` and `spread`, the function transfers tokens to `msg.sender` and then to `_getTokenJar()`:

  ```solidity
  if (tokenOut.isAddressZero()) {
      msg.sender.safeTransferNative(netRevenue);      // external call #1
      _getTokenJar().safeTransferNative(spread);       // external call #2
  } else {
      tokenOut.safeTransfer(msg.sender, netRevenue);   // external call #1
      tokenOut.safeTransfer(_getTokenJar(), spread);    // external call #2
  }
  ```

  While the `nonReentrant` modifier on `burn()` prevents direct reentrancy, the native ETH transfer uses `call{gas: 2300, value: amount}("")` which limits gas but still allows the recipient's `receive()` function to execute. The `totalSupply` and `userBalance` state updates DO occur before these transfers (lines 222-223, 268-273), so the CEI pattern is mostly followed.

  However, the critical state update -- writing the new `unitaryValue` to storage in `_updateNav()` -- happens before the burn calculations but the token transfer to the spread receiver (`_getTokenJar()`) happens after. If `_getTokenJar()` is a contract (which it is -- the "token jar" for buy-back-and-burn), and the native ETH transfer triggers a callback, it could read stale state during the second transfer.

  The `safeTransferNative` limits gas to 2300 which mitigates this significantly, but for ERC20 tokens there is no such gas limit on the `safeTransfer` calls. If the `tokenOut` is an ERC20 with transfer hooks (e.g., ERC777 compatibility), the first `safeTransfer` to `msg.sender` could trigger a callback before the spread payment to `_getTokenJar()` completes. The `nonReentrant` guard prevents re-entering `burn`/`mint`, but external contracts could still read inconsistent state.
- **Impact:** Limited due to `nonReentrant` guard and 2300 gas limit on native transfers. For ERC20 tokens with callbacks, an attacker could read the pool's token balance in an intermediate state (after user payment but before spread payment) which could affect external protocols' view of pool solvency. Severity is reduced by the reentrancy guard.
- **Recommendation:** No immediate fix required due to existing mitigations (`nonReentrant`, 2300 gas limit). For defence in depth, consider performing all state reads needed by external protocols before the transfer sequence, or batch all transfers at the end of the function.

---

## [MEDIUM] Owner-controlled oracle extension creates centralisation risk for NAV manipulation

- **Severity:** MEDIUM
- **Auditmos Skill:** audit-oracle (Pattern #5: Incorrect Price Feed / Configuration)
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/sys/MixinFallback.sol:28-68`
- **Description:** The pool's NAV calculation relies entirely on the oracle extension (`IEOracle`) which is accessed via `IEOracle(address(this))` -- meaning calls are delegated through the extension system. The extension routing in `MixinFallback.fallback()` differentiates between:

  1. **Extensions** (from `_extensionsMap`): immutable, set at deployment
  2. **Adapters** (from `Authority.getApplicationAdapter`): controlled by governance, and critically, only the **owner** gets `delegatecall` access (line 47):

  ```solidity
  // adapter calls are for owner in write mode, and read mode for everyone else
  shouldDelegatecall = msg.sender == pool().owner;
  ```

  The `_extensionsMap` is immutable, but the `Authority` contract at `0xe35129A1E0BdB913CF6Fd8332E9d3533b5F41472` controls which adapters are mapped to which function selectors. If the authority governance is compromised, or if the pool owner (an EOA) is compromised, the oracle extension could be replaced with a malicious one that returns manipulated prices.

  Since all token conversions during NAV calculation (`convertBatchTokenAmounts`, `convertTokenAmount`, `hasPriceFeed`) go through this extension system, a compromised oracle would allow:
  - Inflating NAV before minting to get shares cheaply
  - Deflating NAV before burning to extract excess value
  - Completely draining the pool by manipulating conversion rates

  The oracle uses TWAP from Uniswap V4 pools (based on `getTwap` interface), but the oracle implementation itself is an extension that could be replaced.
- **Impact:** If the Authority governance or the pool owner EOA is compromised, the oracle extension could be replaced to manipulate NAV, enabling extraction of all pool assets. The pool owner being a single EOA without timelock creates a single point of failure.
- **Recommendation:**
  1. Use a multisig or timelock for the pool owner instead of a single EOA
  2. Consider making the oracle extension immutable (part of `_extensionsMap`) rather than upgradeable through the adapter system
  3. Add sanity bounds on NAV changes (e.g., maximum percentage change per update)
  4. Consider adding a timelock on Authority adapter changes to give users time to exit

---

## [MEDIUM] Pool share tokens cannot be transferred, creating exit risk during lockup periods

- **Severity:** MEDIUM
- **Auditmos Skill:** audit-staking (Pattern #4: Flash Deposit/Withdraw Griefing -- related: lockup mechanism)
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/sys/MixinAbstract.sol:7-19`
- **Description:** The ERC20 `transfer()`, `transferFrom()`, and `approve()` functions are all implemented as no-ops that return the default value (`false` / `0`):

  ```solidity
  abstract contract MixinAbstract is IERC20 {
      function transfer(address to, uint256 value) external override returns (bool success) {}
      function transferFrom(address from, address to, uint256 value) external override returns (bool success) {}
      function approve(address spender, uint256 value) external override returns (bool success) {}
      function allowance(address owner, address spender) external view override returns (uint256) {}
  }
  ```

  Combined with the lockup period (configurable from 1 to 30 days, default 30 days), this means:
  1. Users who mint pool tokens are locked for up to 30 days with no ability to transfer or sell their position
  2. There is no secondary market possible for pool tokens since they cannot be transferred
  3. Users cannot transfer tokens to a different wallet for security reasons
  4. In emergency situations (oracle manipulation, pool compromise), users must wait the full lockup period

  While this is clearly an intentional design choice (preventing flash loan attacks and MEV), the combination of non-transferable tokens + mandatory lockup + owner-controlled parameters creates a scenario where the owner could:
  1. Change spread to maximum (5%) after users have minted
  2. Users cannot exit for the lockup duration
  3. When they finally burn, they pay the inflated spread
- **Impact:** Users are locked into the pool for up to 30 days with no recourse. The owner can change fee parameters (spread, transaction fee) while users are locked, effectively extracting value from captive holders. Maximum extractable: 5% spread + 1% transaction fee = 6% on burn, plus another 5% + 1% on the original mint = up to 12% total extraction.
- **Recommendation:**
  1. Implement a timelock on spread and fee changes, or grandfather existing holders at their entry-time parameters
  2. Consider allowing transfers with the lockup restriction carrying over to the recipient
  3. Add maximum parameter change limits per time period

---

## [MEDIUM] First depositor can manipulate initial NAV through oracle price timing

- **Severity:** MEDIUM
- **Auditmos Skill:** audit-staking (Pattern #1: Front-Running First Deposit)
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/state/MixinPoolValue.sol:52-54`
- **Description:** When a pool has no tokens minted yet (`unitaryValue == 0`), the first mint sets the unitary value to `10 ** decimals` (i.e., 1:1 ratio):

  ```solidity
  // first mint skips nav calculation
  if (components.unitaryValue == 0) {
      components.unitaryValue = 10 ** components.decimals;
  }
  ```

  Then in `_mint()`, the minted amount is calculated as:
  ```solidity
  uint256 mintedAmount = (amountIn * 10 ** components.decimals) / components.unitaryValue;
  ```

  For the first mint, this simplifies to `mintedAmount = amountIn` (1:1 ratio).

  The issue is that if the pool supports `mintWithToken` (non-base tokens), the first depositor can:
  1. Use a token whose oracle price is temporarily inflated (e.g., via a flash loan on the underlying Uniswap V4 pool)
  2. Mint pool tokens at the inflated conversion rate
  3. After the oracle price normalises, the depositor holds more pool tokens than they should

  The oracle uses TWAP which partially mitigates spot price manipulation, but short TWAP windows could still be influenced. Furthermore, the conversion happens via `IEOracle(address(this)).convertTokenAmount()` which is an external call -- the oracle implementation details are not in scope but the trust assumption is significant.

  The minimum order size check (`_assertBiggerThanMinimum`) provides some protection against dust attacks but does not prevent manipulation of the first deposit's value.
- **Impact:** The first depositor could receive more pool tokens than warranted by the actual value deposited, diluting subsequent depositors. The severity depends on the TWAP window length and the manipulability of the underlying oracle.
- **Recommendation:**
  1. Consider requiring the first deposit to be made by the pool owner
  2. Consider restricting `mintWithToken` for the first deposit (only allow base token)
  3. Add a minimum initial deposit size to make oracle manipulation economically unviable

---

## [MEDIUM] Spread fee on burn is calculated on gross revenue, creating asymmetric extraction

- **Severity:** MEDIUM
- **Auditmos Skill:** audit-math-precision (Pattern #7: Rounding Leaks Value From Protocol)
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:141,249`
- **Description:** The spread is applied asymmetrically on mint vs burn:

  **Mint (line 141):**
  ```solidity
  uint256 spread = (amountIn * _getSpread()) / _SPREAD_BASE;
  // spread is deducted from amountIn BEFORE minting tokens
  amountIn -= spread;
  ```

  **Burn (line 249):**
  ```solidity
  uint256 spread = (netRevenue * _getSpread()) / _SPREAD_BASE;
  netRevenue -= spread;
  ```

  On mint, the spread reduces the effective deposit (user gets fewer shares). On burn, the spread reduces the effective withdrawal (user gets less base token). This means a round-trip (mint then burn) incurs the spread twice:
  - Deposit 1000 tokens with 10 bps spread: 999 effective deposit
  - Burn all tokens: receives ~998 tokens (spread applied again)

  The double-spread application is intentional as a buy/sell spread. However, the spread on burn is calculated on `netRevenue` which includes the token value appreciation. This means the protocol earns a higher absolute spread fee when the pool has appreciated, even though the spread percentage is the same.

  More critically: the `_getSpread()` defaults to `_DEFAULT_SPREAD` (10 bps) when uninitialized, but returns `_MAX_LOCKUP` (30 days) when `minPeriod` is uninitialized. This creates an asymmetry where the default spread is reasonable but the default lockup is maximum.
- **Impact:** Users bear a symmetric spread on entry and exit. The spread is applied to the full value including appreciation on exit, which is economically equivalent to a performance fee on top of the stated spread. With a maximum spread of 500 bps (5%), round-trip cost can be up to 10% excluding transaction fees. Users cannot avoid this as tokens are non-transferable.
- **Recommendation:** Consider calculating the burn spread only on the principal portion (deposited value) rather than the appreciated value, or clearly document that the spread applies to the full redemption value.

---

## [MEDIUM] Fallback function delegatecall to extensions uses unchecked return data decoding

- **Severity:** MEDIUM
- **Auditmos Skill:** audit-state-validation (Pattern #4: Unchecked Return Values)
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/sys/MixinFallback.sol:30-33`
- **Description:** In the `fallback()` function, the extension lookup uses a delegatecall to `_extensionsMap`:

  ```solidity
  (, bytes memory returnData) = address(_extensionsMap).delegatecall(
      abi.encodeCall(_extensionsMap.getExtensionBySelector, (msg.sig))
  );
  (address target, bool shouldDelegatecall) = abi.decode(returnData, (address, bool));
  ```

  The delegatecall success status (first return value) is explicitly ignored with `(, bytes memory returnData)`. If the delegatecall fails (e.g., the `_extensionsMap` contract self-destructs or returns unexpected data), `returnData` could be empty or malformed. The subsequent `abi.decode` of malformed data would revert, but this is not handled gracefully.

  More concerning: the delegatecall to `_extensionsMap` executes in the pool's storage context. If `_extensionsMap` has any storage-writing behaviour, it could corrupt pool storage. The `_extensionsMap` is immutable and set at deployment, so this requires the original deployer to have set a malicious extensions map -- which is a deployment-time trust assumption rather than a runtime vulnerability.

  The `getExtensionBySelector` is expected to be a view function, but because it's called via `delegatecall`, it has write access to the pool's storage.
- **Impact:** If `_extensionsMap` is malicious or compromised (unlikely since it's immutable), it could corrupt pool storage via the delegatecall. The unchecked success status means a failing `_extensionsMap` would cause a confusing revert rather than a clear error. This is primarily a code quality issue as the `_extensionsMap` is set immutably at deployment.
- **Recommendation:** Check the delegatecall success:
  ```solidity
  (bool success, bytes memory returnData) = address(_extensionsMap).delegatecall(
      abi.encodeCall(_extensionsMap.getExtensionBySelector, (msg.sig))
  );
  require(success, "ExtensionsMap lookup failed");
  ```

---

## [MEDIUM] The `_getAndClearBalance` function silently returns 0 on ERC20 `balanceOf` failure, potentially excluding assets from NAV

- **Severity:** MEDIUM
- **Auditmos Skill:** audit-oracle (Pattern #6: Unhandled Oracle Reverts / Silent Failures)
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/state/MixinPoolValue.sol:170-189`
- **Description:** In `_getAndClearBalance`, when querying an ERC20 token's balance, the function catches all errors and returns 0:

  ```solidity
  try IERC20(token).balanceOf(address(this)) returns (uint256 _balance) {
      value += _balance.toInt256();
  } catch {
      // returns 0 balance if the ERC20 balance cannot be found
      return 0;
  }
  ```

  If a token's `balanceOf` call reverts (due to the token being paused, self-destructed, or experiencing a temporary revert), the pool's NAV computation will silently exclude that token's balance, leading to an understated NAV. This means:

  1. Minters get more shares than warranted (NAV appears lower, so more shares per unit deposited)
  2. Burners get less value than warranted (NAV is understated)

  An attacker could exploit this by:
  1. Causing a token's `balanceOf` to temporarily revert (if the token has a pausable mechanism)
  2. Minting pool tokens at the deflated NAV
  3. After the token's `balanceOf` is restored, burning at the correct (higher) NAV

  Note that the `catch` block also discards any transient storage balance (`value` from position balances is lost via `return 0`), which means application position balances for that token are also excluded.
- **Impact:** Silent NAV understatement when any active token's `balanceOf` reverts. This creates a sandwich opportunity: cause the revert, mint cheap shares, restore the token, burn at correct NAV. The practical exploitability depends on whether the attacker can cause a specific token's `balanceOf` to revert temporarily.
- **Recommendation:** Instead of silently returning 0, consider reverting the entire NAV calculation when a token balance cannot be read, similar to how `getAppTokenBalances` failures are handled (the try/catch in `_computeTotalPoolValue` reverts on failure). Alternatively, log the failure and use the last known balance.

---

## Summary

| # | Severity | Finding | Auditmos Skill |
|---|----------|---------|----------------|
| 1 | MEDIUM | Unsafe downcast of mintedAmount to uint208 | audit-math-precision |
| 2 | MEDIUM | Single-step ownership transfer | audit-state-validation |
| 3 | MEDIUM | `updateUnitaryValue` lacks reentrancy protection | audit-reentrancy |
| 4 | MEDIUM | Read-only reentrancy via stale unitaryValue | audit-reentrancy |
| 5 | MEDIUM | CEI violation in burn spread payment | audit-reentrancy |
| 6 | MEDIUM | Owner-controlled oracle extension centralisation risk | audit-oracle |
| 7 | MEDIUM | Non-transferable tokens with owner-changeable parameters | audit-staking |
| 8 | MEDIUM | First depositor oracle price timing manipulation | audit-staking |
| 9 | MEDIUM | Asymmetric spread calculation on burn | audit-math-precision |
| 10 | MEDIUM | Unchecked delegatecall return in fallback | audit-state-validation |
| 11 | MEDIUM | Silent 0 return on `balanceOf` failure deflates NAV | audit-oracle |

---

## Auditmos skill checklist verification

### audit-math-precision checklist
* [x] Multiplication always performed before division -- verified in NAV calculation, mint/burn arithmetic
* [x] Checks for rounding to zero with appropriate reverts -- `_assertBiggerThanMinimum` enforces minimum order
* [x] Token amounts scaled to common precision before calculations -- uses oracle `convertTokenAmount` for cross-token
* [x] No double-scaling of already scaled values -- no double-scaling observed
* [x] Consistent precision scaling across all modules -- pool decimals used consistently
* [ ] SafeCast used for all downcasting operations -- **VIOLATION**: explicit `uint208()` casts in `_allocateMintTokens` and `_allocateBurnTokens` (Finding #1)
* [x] Protocol fees round up, user amounts round down -- spread is deducted after division, favours protocol
* [x] Decimal assumptions documented and validated -- pool decimals set from base token, minimum 6
* [x] Interest calculations use correct time units -- no interest calculations present
* [x] Token pair directions consistent across calculations -- oracle handles conversion direction

### audit-oracle checklist
* [x] Stale price checks -- Oracle uses TWAP (time-weighted), inherently stale-resistant
* [x] L2 sequencer check -- Not applicable (Ethereum mainnet deployment)
* [x] Feed-specific heartbeats -- Not applicable (TWAP-based oracle)
* [x] Oracle precision -- Handled by oracle extension
* [x] Price feed addresses -- Validated via `hasPriceFeed` check before NAV computation
* [x] Oracle revert handling -- `hasPriceFeed` check prevents calls to non-existent feeds
* [x] Depeg monitoring -- Not applicable for TWAP-based oracle
* [x] Min/max validation -- Not applicable for TWAP-based oracle
* [x] TWAP usage -- Oracle uses TWAP (good)
* [x] Price direction -- Handled by oracle extension `convertTokenAmount`
* [ ] Circuit breaker checks -- **No circuit breaker on NAV changes** (Finding #6 addresses this)

### audit-reentrancy checklist
* [x] CEI pattern -- Mostly followed; state updated before transfers in mint/burn
* [x] NonReentrant modifiers -- Applied to mint, burn, mintWithToken, burnForToken
* [ ] Token assumptions -- Native ETH transfer uses 2300 gas limit (good), but ERC20 transfer hooks could be exploited for read-only reentrancy (Finding #4)
* [ ] Cross-function analysis -- `updateUnitaryValue` lacks nonReentrant (Finding #3)
* [ ] Read-only safety -- View functions return stale values during NAV update window (Finding #4)

### audit-state-validation checklist
* [ ] All multi-step processes verify previous steps -- **VIOLATION**: Single-step ownership transfer (Finding #2)
* [x] Functions validate array lengths > 0 before processing -- Validated in active tokens iteration
* [x] All function inputs validated for edge cases -- Zero address checks, minimum amounts enforced
* [ ] Return values from all function calls checked -- **VIOLATION**: Delegatecall return unchecked in fallback (Finding #10)
* [x] State transitions atomic -- Mint/burn are atomic within transactions
* [x] ID existence verified before use -- Token active status checked before operations
* [x] Array parameters have matching length validation -- Not applicable (no multi-array inputs in user-facing functions)
* [x] Access control modifiers on all administrative functions -- `onlyOwner` consistently applied
* [x] State variables updated before external calls -- CEI pattern mostly followed
* [x] Pause mechanisms synchronised -- No pause mechanism (by design)

### audit-slippage checklist
* [x] User can specify minTokensOut for all swaps -- `amountOutMin` parameter on mint/burn
* [ ] User can specify deadline -- **No deadline parameter** on mint/burn (TWAP mitigates this somewhat)
* [x] Slippage calculated correctly -- Direct comparison of output vs minimum
* [x] Slippage precision matches output token -- Output is in pool tokens (mint) or base tokens (burn)
* [x] Hard-coded slippage can be overridden -- User specifies their own `amountOutMin`
* [x] Slippage checked on final output amount -- Checked after all fee deductions

### audit-staking checklist
* [x] Separate tokens -- Pool tokens are separate from deposited assets (pool has its own token)
* [x] No direct transfer dilution -- totalSupply tracks minted amounts, not token balance
* [x] Precision protection -- Minimum order size enforced via `_assertBiggerThanMinimum`
* [x] Flash protection -- Minimum lockup period of 1 day (default 30 days)
* [x] Index updates -- NAV is recomputed on every mint/burn via `_updateNav()`
* [x] Balance integrity -- User balances tracked via `accounts().userAccounts` mapping
