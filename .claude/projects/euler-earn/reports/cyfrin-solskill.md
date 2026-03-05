# EulerEarn code quality assessment (Cyfrin Solidity skill)

**Contract:** `src/EulerEarn.sol` (930 lines, Solidity 0.8.26)
**Description:** ERC-4626 yield aggregator vault forked from Morpho, with Euler Vault Connector (EVC) integration and multi-strategy allocation.
**Assessment date:** 2026-03-05
**Methodology:** [Cyfrin Solidity Development Standards](https://www.cyfrin.io/)

---

## Executive summary

EulerEarn is a well-structured, production-quality contract that demonstrates strong adherence to modern Solidity best practices. The codebase shows clear influence from the Morpho MetaMorpho design with thoughtful adaptations for EVC integration. The contract uses `Ownable2Step`, custom errors, a tiered role-based access control system, and `nonReentrant` guards on all state-changing ERC-4626 entry points. The most notable areas for improvement are minor deviations from optimal function ordering, a few missed gas optimisation opportunities in storage access patterns, and the use of OpenZeppelin's `ReentrancyGuard` rather than the newer transient-storage variant.

**Overall rating: High quality, production ready.**

---

## 1. Error handling

### Positive patterns

- **Custom errors throughout.** The contract delegates all errors to `ErrorsLib`, using `revert ErrorsLib.SomeError()` consistently. No `require` statements or string-based reverts appear anywhere in the 930-line contract. This is fully aligned with Cyfrin standard #2.

- **Early revert pattern.** Input validation checks (`AlreadySet`, `AlreadyPending`, `MaxFeeExceeded`, `ZeroFeeRecipient`) appear at the top of functions before any state mutations or external calls, satisfying standard #18 (revert as quickly as possible).

- **Informative error parameters.** Errors like `UnauthorizedMarket(id)`, `PendingCap(id)`, `InvalidMarketRemovalNonZeroCap(id)`, and `InconsistentAsset(id)` include the offending vault address, which aids debugging and off-chain monitoring.

### Deviations

- **Error naming convention.** Cyfrin standard #2 specifies errors should be prefixed with the contract name and two underscores (e.g. `EulerEarn__NotCuratorRole`). The contract instead centralises errors in `ErrorsLib` without a contract-name prefix. This is a deliberate architectural choice -- shared error libraries reduce bytecode duplication across a multi-contract system -- but it does deviate from the naming convention. Since this is a forked design decision from Morpho, the trade-off is reasonable.

- **Silent `catch {}` blocks.** Lines 828 and 849 use bare `try/catch {}` when depositing to or withdrawing from strategy vaults. While the comment explains this is intentional ("skip vaults that revert"), silently swallowing errors makes off-chain debugging harder. A `catch (bytes memory reason)` with an emitted event would preserve the error context without changing on-chain behaviour.

  ```solidity
  // Current (line 828)
  try id.deposit(toSupply, address(this)) returns (uint256 suppliedShares) {
      config[id].balance = (config[id].balance + suppliedShares).toUint112();
      assets -= toSupply;
  } catch {}

  // Suggested improvement
  } catch (bytes memory reason) {
      emit EventsLib.StrategyDepositFailed(id, toSupply, reason);
  }
  ```

---

## 2. Function ordering and layout

### Positive patterns

- **Clear section headers.** The contract uses comment-delimited sections: `IMMUTABLES`, `STORAGE`, `CONSTRUCTOR`, `MODIFIERS`, `ONLY OWNER FUNCTIONS`, `ONLY CURATOR FUNCTIONS`, `ONLY ALLOCATOR FUNCTIONS`, `REVOKE FUNCTIONS`, `EXTERNAL`, `ERC4626 (PUBLIC)`, `ERC4626 (INTERNAL)`, `INTERNAL`, `LIQUIDITY ALLOCATION`, `FEE MANAGEMENT`. This is readable and well-organised.

- **Logical role-based grouping.** Functions are grouped by access role (owner, curator, allocator, guardian) which is a pragmatic alternative to strict visibility ordering. For a contract with this many roles, this is arguably more navigable.

### Deviations

- **Header style.** Cyfrin standard #5 specifies a particular header format with `/*//////////////` decorations. The contract uses `/* SECTION NAME */` single-line comments instead. This is minor but is a style deviation.

  ```solidity
  // Current
  /* ONLY OWNER FUNCTIONS */

  // Cyfrin standard
  /*//////////////////////////////////////////////////////////////
                        ONLY OWNER FUNCTIONS
  //////////////////////////////////////////////////////////////*/
  ```

- **Visibility-based ordering.** Cyfrin standard #4 prescribes ordering by visibility: constructor, then external/public state-changing, then external/public view/pure, then internal state-changing, then internal view/pure. The contract instead groups by role, which means external state-changing functions (`setFee`, `submitCap`) are interleaved with the view functions (`supplyQueueLength`, `maxWithdrawFromStrategy`). The role-based grouping is reasonable for this contract's complexity, but it is a departure from the standard.

- **File-level layout.** Cyfrin standard #6 places events and errors before interfaces and contracts. EulerEarn delegates these to `EventsLib` and `ErrorsLib` respectively, which is cleaner but means the file-level layout convention doesn't directly apply. This is a non-issue in practice.

---

## 3. Security patterns

### Positive patterns

- **CEI (Checks-Effects-Interactions) compliance.** The `_deposit` function (line 697) performs the token transfer-in first via `safeTransferFromWithPermit2`, then mints shares, emits the event, supplies to strategies, and updates `lastTotalAssets`. This is a slight deviation from strict CEI (the external `_supplyStrategy` call happens before the `_updateLastTotalAssets` effect), but the `nonReentrant` guard makes this safe.

- **Reentrancy protection on all critical paths.** The `nonReentrant` modifier is applied to `deposit`, `mint`, `withdraw`, `redeem`, `setFee`, `setFeeRecipient`, `submitCap`, and `reallocate` -- all functions that perform external calls. This is thorough.

- **`nonReentrant` placed before other modifiers.** On `setFee` (line 243: `nonReentrant onlyOwner`) and `setFeeRecipient` (line 258: `nonReentrant onlyOwner`), the reentrancy guard correctly appears first, satisfying Cyfrin standard #22.

- **EVC sub-account protection.** The `_withdraw` function (line 722-725) checks that the receiver is not an EVC sub-account whose private key is unknown, preventing permanent loss of funds. This is a thoughtful EVC-specific safety check.

- **Timelock governance pattern.** Sensitive parameter changes (timelock reduction, guardian change, cap increase) go through a two-phase submit/accept flow with a configurable timelock. Timelock increases take effect immediately (they are strictly safer), while decreases require waiting. This is well-designed.

- **Approval management.** The `_setCap` function properly manages approvals -- granting max approval when a cap is set above zero and revoking when set to zero. The Permit2-aware approval handling is a positive pattern.

### Deviations

- **`ReentrancyGuard` vs `ReentrancyGuardTransient`.** Cyfrin standard #23 recommends using `ReentrancyGuardTransient` for cheaper reentrancy protection via transient storage (EIP-1153). The contract uses OpenZeppelin's standard `ReentrancyGuard` which uses a regular storage slot. Since the contract targets Solidity 0.8.26 which supports transient storage, upgrading to the transient variant would save approximately 2,900 gas per guarded function call (avoiding the cold SSTORE).

- **Missing `nonReentrant` on some external state-changing functions.** `setSupplyQueue` (line 325), `updateWithdrawQueue` (line 340), and `submitMarketRemoval` (line 310) do not have `nonReentrant`, though they also do not make external calls. `reallocate` correctly has it. The functions `acceptTimelock`, `acceptGuardian`, and `acceptCap` also lack `nonReentrant` despite `acceptCap` making an external `staticcall` via `_setCap`. While the risk is low because the `staticcall` cannot modify state, applying `nonReentrant` defensively on all state-changing external functions would be more robust.

- **Unchecked arithmetic in `_withdrawStrategy`.** Line 847: `config[id].balance = uint112(config[id].balance - withdrawnShares)` uses an unsafe cast from `uint256` to `uint112` without `SafeCast`. If `withdrawnShares` somehow exceeds the uint112 range, this would silently truncate. Contrast with `_supplyStrategy` (line 826) which correctly uses `.toUint112()` via SafeCast. This inconsistency is a minor concern.

  ```solidity
  // Line 847 - unsafe cast
  config[id].balance = uint112(config[id].balance - withdrawnShares);

  // Line 826 - safe cast (correct pattern)
  config[id].balance = (config[id].balance + suppliedShares).toUint112();
  ```

- **`reallocate` also uses unsafe cast.** Line 415: `config[id].balance = uint112(supplyShares - withdrawnShares)` has the same issue.

---

## 4. Access control

### Positive patterns

- **`Ownable2Step` used correctly.** Cyfrin standard #24 recommends `Ownable2Step` over `Ownable`. The contract inherits `Ownable2Step` which requires a two-step ownership transfer (propose + accept), preventing accidental transfers to wrong addresses. This is excellent.

- **Tiered role hierarchy.** The contract implements a well-designed role hierarchy:
  - **Owner** -- highest privilege, can do everything
  - **Curator** -- can manage caps and market removals (owner also has curator powers)
  - **Allocator** -- can manage queues and reallocate (owner and curator also have allocator powers)
  - **Guardian** -- can revoke pending changes (owner also has guardian powers)

  The role modifiers (lines 146-178) implement this hierarchy correctly with fallthrough checks.

- **EVC-aware `_msgSender`.** All role modifiers use `_msgSenderOnlyEVCAccountOwner()` (from EVCUtil), ensuring that EVC authentication is respected while restricting to account owners only (not operators). This is the correct EVC integration pattern.

- **AlreadySet guards.** Every setter function checks if the new value equals the current value and reverts with `AlreadySet`. This prevents no-op transactions from emitting misleading events and wasting gas.

### Deviations

- **No role event emission on construction.** The constructor does not emit events for the initial owner assignment (handled by OpenZeppelin) or the creator assignment. While the `creator` is immutable and discoverable on-chain, emitting an event at construction time aids off-chain indexing.

- **Guardian self-revocation risk.** The `submitGuardian` function allows the owner to change the guardian, but a guardian can revoke its own pending replacement via `revokePendingGuardian`. This creates a tension where a compromised guardian could indefinitely block its own replacement. The timelock ensures eventual resolution, but this is a design trade-off worth documenting.

---

## 5. Testing indicators

### Positive patterns

- **Branching-friendly function design.** Functions like `submitTimelock` (immediate for increases, pending for decreases), `submitGuardian` (immediate for first guardian, pending for changes), and `submitCap` (immediate for decreases, pending for increases) have clear branching logic that maps well to the branching tree technique (Cyfrin standard #7).

- **Error-rich code.** The extensive use of specific custom errors (`AlreadySet`, `AlreadyPending`, `PendingRemoval`, `MaxQueueLengthExceeded`, etc.) provides clear test targets for negative path testing.

- **View function coverage.** Functions like `expectedSupplyAssets`, `maxWithdrawFromStrategy`, `_accruedFeeAndAssets` are pure computations that are straightforward to unit test.

### Recommendations

- **Invariant testing candidates.** The following properties are strong candidates for stateful fuzz / invariant tests (Cyfrin standard #3):
  1. `totalAssets() >= lastTotalAssets - lostAssets` (the vault never reports fewer real assets than it actually holds, minus acknowledged losses)
  2. After `reallocate`, `totalWithdrawn == totalSupplied` (conservation of assets)
  3. The sum of all `config[id].balance` share positions, when redeemed, should approximate `totalAssets() - idleAssets`
  4. `fee <= MAX_FEE` always holds
  5. `withdrawQueue` and `supplyQueue` lengths never exceed `MAX_QUEUE_LENGTH`
  6. Every vault in `withdrawQueue` has `config[id].enabled == true`

- **Fuzz testing targets.** The `_accruedFeeAndAssets` function (line 898) has complex arithmetic involving `realTotalAssets`, `lastTotalAssets`, `lostAssets`, and fee calculations. This is a prime candidate for stateless fuzz testing with varying interest accrual scenarios.

---

## 6. Gas optimisation

### Positive patterns

- **Storage caching.** Line 910: `uint256 lastTotalAssetsCached = lastTotalAssets` correctly caches the storage variable before multiple reads. This follows Cyfrin standard #17.

- **`calldata` for array parameters.** `setSupplyQueue(IERC4626[] calldata)`, `updateWithdrawQueue(uint256[] calldata)`, and `reallocate(MarketAllocation[] calldata)` all use `calldata` for read-only array inputs, following Cyfrin standard #15.

- **Early returns in loops.** Both `_supplyStrategy` (line 831: `if (assets == 0) return`) and `_withdrawStrategy` (line 852: `if (assets == 0) return`) exit early when all assets have been allocated/withdrawn, avoiding unnecessary iterations.

- **`continue` for zero-cap vaults.** Line 816: `if (supplyCap == 0) continue` in `_supplyStrategy` avoids unnecessary computation for disabled vaults.

- **No default value initialisation.** Loop counters use `uint256 i;` without `= 0`, satisfying Cyfrin standard #13.

### Deviations

- **Repeated `config[id]` storage reads.** In `reallocate` (lines 390-438), `config[id]` is read from storage multiple times per iteration:
  - `config[id].enabled` (line 390)
  - `config[id].balance` (line 392)
  - `config[id].balance` again in the write (lines 415, 426, 433)
  - `config[id].cap` (line 427)

  Caching `config[id]` as a storage pointer at the top of each iteration would be cleaner, though the compiler may already optimise some of these reads within a single transaction.

  ```solidity
  // Suggested
  MarketConfig storage mc = config[id];
  if (!mc.enabled) revert ErrorsLib.MarketNotEnabled(id);
  uint256 supplyShares = mc.balance;
  ```

- **`_accruedFeeAndAssets` iterates the full withdraw queue.** Lines 905-908 loop through every vault in `withdrawQueue` to compute `realTotalAssets`. This function is called from `totalAssets()`, `_convertToShares`, `_convertToAssets`, `maxWithdraw`, `maxRedeem`, and `_accrueInterest`. For vaults with many strategies, this becomes expensive. There is no obvious way to avoid this without a storage-cached sum, but it is worth noting.

- **`supplyQueue.length` read from storage in loop condition.** Lines 641, 812: `for (uint256 i; i < supplyQueue.length; ++i)` reads the array length from storage on each iteration. Since the array is `calldata` in `setSupplyQueue` the length is cached (per Cyfrin standard #16), but for the storage array in `_supplyStrategy` and `_maxDeposit`, caching the length would save gas. Note: Cyfrin standard #16 says not to cache `calldata` array length, but storage array length should still be cached.

  ```solidity
  // Current
  for (uint256 i; i < supplyQueue.length; ++i) { ... }

  // Suggested for storage arrays
  uint256 queueLength = supplyQueue.length;
  for (uint256 i; i < queueLength; ++i) { ... }
  ```

  The same applies to `withdrawQueue.length` at lines 839, 861, 905.

- **`msg.sender` vs `owner()` in modifiers.** Cyfrin standard #19 recommends using `msg.sender` instead of `owner()` inside `onlyOwner` functions because `msg.sender` is cheaper than an SLOAD. The custom role modifiers (`onlyCuratorRole`, `onlyAllocatorRole`, `onlyGuardianRole`) call `owner()` which is an SLOAD. However, these modifiers check multiple roles and `owner()` is only one of the checks, so the read is necessary. This is not truly a deviation -- the standard applies to `onlyOwner` functions where the caller is already guaranteed to be the owner.

---

## 7. General code quality

### Positive patterns

- **Strict pragma version.** `pragma solidity 0.8.26` uses a fixed version, satisfying Cyfrin standard #8 for deployed contracts.

- **Named imports throughout.** All imports use the `import { X } from "path"` syntax with absolute paths, satisfying Cyfrin standard #1. No wildcard or relative imports.

- **Comprehensive NatSpec.** The contract-level NatSpec includes `@title`, `@author`, and `@custom:contact` with two security contacts (Morpho and Euler), satisfying Cyfrin standard #9. Function-level documentation uses `@inheritdoc` extensively for interface compliance.

- **`immutable` for constructor-set variables.** `permit2Address` and `creator` are declared `immutable`, satisfying Cyfrin standard #28.

- **Using library pattern.** The `using X for Y` declarations at the top of the contract (lines 42-49) are comprehensive and well-organised, enabling clean syntax throughout.

- **Event emission for all state changes.** Every setter and state mutation emits an event via `EventsLib`, providing a complete audit trail for off-chain monitoring.

- **Defensive comments.** The codebase includes clear explanatory comments for non-obvious decisions, such as:
  - "Safe 'unchecked' cast because newTimelock <= MAX_TIMELOCK" (line 235)
  - "The vault's underlying asset is guaranteed to be the vault's asset because it has a non-zero supply cap" (line 430)
  - "`lastTotalAssets + assets` may be a little above `totalAssets()`" (line 705)

### Deviations

- **`memory` vs `calldata` for string parameters.** `setName(string memory)` and `setSymbol(string memory)` (lines 195, 202) and the constructor parameters `__name` and `__symbol` use `memory`. Cyfrin standard #15 prefers `calldata` for read-only function inputs. Since these strings are written to storage, `memory` is required for the constructor, but the external setter functions could use `calldata`.

  ```solidity
  // Could use calldata since the string is only assigned to storage
  function setName(string calldata newName) external onlyOwner {
      _name = newName;
      emit EventsLib.SetName(newName);
  }
  ```

- **Constructor parameter naming.** The parameters `__name` and `__symbol` use double-underscore prefixes which is unconventional. A more standard approach would be `name_` and `symbol_` or `initialName` and `initialSymbol`. This is a minor style point.

- **Struct copy in `reallocate`.** Line 388: `MarketAllocation memory allocation = allocations[i]` copies an entire calldata struct element to memory. Since `allocations` is `calldata`, individual fields can be accessed directly. Per Cyfrin standard #25, avoid copying entire structs when only a few fields are needed.

  ```solidity
  // Current - copies full struct to memory
  MarketAllocation memory allocation = allocations[i];
  IERC4626 id = allocation.id;
  // ...uses allocation.assets

  // Suggested - access calldata directly
  IERC4626 id = allocations[i].id;
  uint256 targetAssets = allocations[i].assets;
  ```

- **Missing `@custom:security-contact` on a standalone line.** The NatSpec has two `@custom:contact` annotations (lines 38-39) rather than `@custom:security-contact`. While functionally similar, the Cyfrin standard specifies `@custom:security-contact` specifically.

---

## Summary table

| Category | Rating | Notes |
|---|---|---|
| Error handling | Excellent | Custom errors, early reverts, informative parameters. Minor: silent catch blocks. |
| Function ordering | Good | Role-based grouping is pragmatic but deviates from strict visibility ordering. |
| Security patterns | Excellent | CEI, nonReentrant, Ownable2Step, timelock governance, EVC sub-account checks. Minor: could use ReentrancyGuardTransient, two unsafe uint112 casts. |
| Access control | Excellent | Well-designed role hierarchy with EVC integration, AlreadySet guards. |
| Testing indicators | Good | Code is well-structured for testing; strong invariant test candidates identified. |
| Gas optimisation | Good | calldata usage, early returns, storage caching. Could improve storage array length caching and config struct access patterns. |
| General code quality | Excellent | Strict pragma, named imports, immutables, comprehensive events and NatSpec. |

---

## Priority recommendations

1. **Replace `ReentrancyGuard` with `ReentrancyGuardTransient`** -- straightforward change that saves ~2,900 gas per guarded call, meaningful given these are user-facing deposit/withdraw paths.

2. **Use `SafeCast.toUint112()` consistently** -- lines 415 and 847 use unsafe `uint112()` casts while line 826 correctly uses `.toUint112()`. This inconsistency should be resolved for defensive consistency.

3. **Emit events from catch blocks** -- the silent `catch {}` at lines 828 and 849 should at minimum emit diagnostic events with the caught error bytes for off-chain observability.

4. **Cache storage array lengths in loops** -- `supplyQueue.length` and `withdrawQueue.length` are read from storage on each loop iteration in `_supplyStrategy`, `_withdrawStrategy`, `_simulateWithdrawStrategy`, `_maxDeposit`, and `_accruedFeeAndAssets`.

5. **Use `calldata` for string setters** -- `setName` and `setSymbol` can accept `calldata` strings for a minor gas saving.
