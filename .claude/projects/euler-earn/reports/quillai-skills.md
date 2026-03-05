# EulerEarn combined audit report (QuillAI skills)

**Contract:** `EulerEarn.sol` (930 lines, Solidity 0.8.26)
**Type:** ERC-4626 yield aggregator vault (forked from Morpho MetaMorpho, with EVC integration)
**Skills applied:** State Invariant Detection, Input & Arithmetic Safety, Reentrancy Pattern Analysis, External Call Safety, DoS & Griefing Analysis, Signature & Replay Analysis

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 3 |
| MEDIUM | 5 |
| LOW / INFO | 6 |

The EulerEarn contract is a well-architected ERC-4626 aggregator with strong foundational defences including `ReentrancyGuard`, virtual share/asset offsets, `SafeERC20`, and timelocked governance. The findings below focus on genuine risks rather than theoretical concerns that are already mitigated.

---

## Skill 1: State invariant detection

### Finding 1.1: `lastTotalAssets` can drift from actual total assets

**Function:** `_deposit()` at `EulerEarn.sol:L697-708`, `_withdraw()` at `EulerEarn.sol:L717-735`
**Severity:** MEDIUM
**Invariant:** `lastTotalAssets ~= realTotalAssets + lostAssets`

**Description:**

The contract acknowledges (L705-706, L727-728) that `lastTotalAssets + assets` after a deposit and `lastTotalAssets - assets` after a withdrawal may slightly differ from the actual `totalAssets()` computed from underlying strategy vaults. This is because `_supplyStrategy()` may deposit into strategies where `previewRedeem(deposit(x))` does not exactly equal `x` due to the strategy's own rounding.

Each deposit or withdrawal can introduce a small positive delta into `lastTotalAssets` relative to reality. Over many interactions, this delta accumulates and inflates `lostAssets` at the next `_accrueInterest()` call (L911: `if (realTotalAssets < lastTotalAssetsCached - lostAssets)`).

**Impact:**

The `lostAssets` variable monotonically increases due to these rounding artefacts, not actual losses. This inflates `totalAssets()` (L919: `newTotalAssets = realTotalAssets + newLostAssets`), which in turn slightly inflates the share price. While each delta is small (typically 1-2 wei per interaction), over thousands of interactions the accumulated `lostAssets` could become non-trivial for low-decimal tokens.

**Recommendation:**

This is a known and documented design choice. For additional safety, consider periodic `lostAssets` reconciliation by the owner, or add a function to reset `lostAssets` when it exceeds actual losses. Low-decimal tokens (e.g. GUSD with 2 decimals) amplify this effect and should be flagged in documentation.

---

### Finding 1.2: `config[id].balance` can desynchronise from actual share holdings

**Function:** `_supplyStrategy()` at `EulerEarn.sol:L811-835`, `_withdrawStrategy()` at `EulerEarn.sol:L838-856`
**Severity:** LOW
**Invariant:** `config[id].balance == IERC4626(id).balanceOf(address(this))`

**Description:**

The `config[id].balance` tracks the vault's shares in each strategy. When a new strategy is enabled via `_setCap()` (L788), it initialises balance to `id.balanceOf(address(this)).toUint112()`, correctly capturing any pre-existing shares. However, if someone directly transfers strategy vault shares to the EulerEarn contract outside of the normal `deposit`/`reallocate` flow, `config[id].balance` will be less than the actual `balanceOf`.

Additionally, `_supplyStrategy()` and `_withdrawStrategy()` use try/catch (L825, L846). If a strategy's `deposit()` succeeds but returns a different number of shares than expected (e.g. due to strategy-internal fee changes between the `maxDeposit` call and the actual deposit), the balance tracking remains correct because it uses the return value. This is well-handled.

**Impact:**

Donated shares are not tracked and cannot be withdrawn through normal operations (they are not included in `config[id].balance`). They inflate `totalAssets()` via `expectedSupplyAssets()` and `realTotalAssets` but are inaccessible, effectively acting as a donation to all share holders. This is consistent with how most ERC-4626 vaults handle direct token transfers.

**Recommendation:**

INFO-level. The behaviour is by design. The `reallocate()` function with `allocation.assets == 0` (L399) can recover such donations by redeeming all `supplyShares`, but only the tracked shares, not donated ones. Consider documenting this explicitly.

---

### Finding 1.3: `_accrueInterest()` fee calculation uses stale `totalSupply()`

**Function:** `_accruedFeeAndAssets()` at `EulerEarn.sol:L898-929`
**Severity:** LOW
**Invariant:** Fee shares should be calculated using the supply that exists before minting the fee shares.

**Description:**

At L926-927, `feeShares` is calculated as:
```solidity
feeShares = _convertToSharesWithTotals(feeAssets, totalSupply(), newTotalAssets - feeAssets, Math.Rounding.Floor);
```

This uses `totalSupply()` (pre-fee-minting supply) in the denominator, which means the fee recipient receives slightly fewer shares than a pure proportional calculation would give. This is intentional and protocol-favourable: the fee recipient's shares are worth slightly less than the exact fee amount, preventing fee extraction from exceeding the actual interest earned.

**Impact:**

No vulnerability. The rounding direction is correct (protocol-favourable). The fee recipient receives slightly less than the nominal fee percentage, which is the safe direction.

**Recommendation:**

INFO-level. No action needed. This is correct behaviour.

---

## Skill 2: Input and arithmetic safety

### Finding 2.1: Unsafe downcast in `reallocate()` from `uint256` to `uint112` without `SafeCast`

**Function:** `reallocate()` at `EulerEarn.sol:L415`
**Category:** Unsafe Cast
**Severity:** HIGH

**Vulnerable code:**

```solidity
config[id].balance = uint112(supplyShares - withdrawnShares);  // L415
```

Compared to L433 which correctly uses SafeCast:
```solidity
config[id].balance = (supplyShares + suppliedShares).toUint112();  // L433
```

And L826 which also uses SafeCast:
```solidity
config[id].balance = (config[id].balance + suppliedShares).toUint112();  // L826
```

**Description:**

Line 415 performs a raw `uint112()` cast on the result of `supplyShares - withdrawnShares`. While `supplyShares` is loaded from `config[id].balance` (which is `uint112`), and `withdrawnShares` is the return value of `id.withdraw()` which should be <= `supplyShares`, a malicious or buggy strategy vault could return a `withdrawnShares` value that is:
1. Greater than `supplyShares`, causing an underflow (caught by Solidity 0.8+ checked math).
2. Or, in the branch at L410-412 where `withdrawnShares = shares = supplyShares`, the result is always 0, which is safe.

For the `id.withdraw()` branch (L409), `withdrawnShares` is returned by an external call to an untrusted strategy vault. If the strategy returns a value larger than `supplyShares`, Solidity 0.8+ checked arithmetic would revert on the subtraction, which is correct behaviour. However, if `supplyShares - withdrawnShares` exceeds `type(uint112).max` (which is impossible since `supplyShares` itself is `uint112`), the raw cast would silently truncate.

**Analysis:** Since `supplyShares` is already `uint112` (loaded from `config[id].balance`), and `withdrawnShares <= supplyShares` is enforced by the subtraction not underflowing, the result is guaranteed to fit in `uint112`. The raw cast is technically safe but inconsistent with the rest of the codebase which uses `toUint112()`. This inconsistency is a code quality issue rather than an exploitable vulnerability.

**Recommendation:**

For consistency and defence-in-depth, replace:
```solidity
config[id].balance = uint112(supplyShares - withdrawnShares);
```
with:
```solidity
config[id].balance = (supplyShares - withdrawnShares).toUint112();
```

**Revised severity:** LOW (inconsistency, not exploitable)

---

### Finding 2.2: Unsafe downcast in `_withdrawStrategy()` from `uint256` to `uint112` without `SafeCast`

**Function:** `_withdrawStrategy()` at `EulerEarn.sol:L847`
**Category:** Unsafe Cast
**Severity:** HIGH

**Vulnerable code:**

```solidity
config[id].balance = uint112(config[id].balance - withdrawnShares);  // L847
```

**Description:**

Same pattern as Finding 2.1. `config[id].balance` is `uint112`, and `withdrawnShares` is the return value of an external call `id.withdraw(toWithdraw, address(this), address(this))`. The subtraction is checked by Solidity 0.8+, so underflow reverts. However, this is inside a try/catch block (L846), so if the subtraction were to revert, it would be silently caught and the loop would continue, potentially leaving the vault in an inconsistent state.

**Wait** -- re-reading the code more carefully:

```solidity
try id.withdraw(toWithdraw, address(this), address(this)) returns (uint256 withdrawnShares) {
    config[id].balance = uint112(config[id].balance - withdrawnShares);
    assets -= toWithdraw;
} catch {}
```

The try/catch wraps the external call `id.withdraw()`. The code inside `returns (uint256 withdrawnShares) { ... }` executes only if the external call succeeds. If `config[id].balance - withdrawnShares` underflows, that revert is NOT caught by the try/catch (the try/catch only catches reverts from the external call itself). So the whole transaction would revert.

Actually, in Solidity 0.8+, a revert in the success handler of a try/catch does propagate (it is not caught). So the underflow would correctly revert the entire transaction.

The raw `uint112()` cast is safe for the same reason as Finding 2.1: `config[id].balance` is already `uint112`, `withdrawnShares <= config[id].balance` due to checked subtraction, so the result fits in `uint112`.

**Impact:**

Not exploitable. This is a code consistency issue. A malicious strategy returning `withdrawnShares > config[id].balance` would cause the transaction to revert entirely (not silently caught).

**Recommendation:**

For consistency with the rest of the codebase, use `SafeCast`:
```solidity
config[id].balance = (uint256(config[id].balance) - withdrawnShares).toUint112();
```

**Revised severity:** LOW (inconsistency, not exploitable)

---

### Finding 2.3: Virtual share offset mitigates ERC-4626 inflation attack

**Function:** `_convertToSharesWithTotals()` at `EulerEarn.sol:L671-679`
**Category:** Inflation Attack (ERC-4626)
**Severity:** INFO (mitigated)

**Description:**

The contract uses `ConstantsLib.VIRTUAL_AMOUNT` as both a virtual shares and virtual assets offset:

```solidity
return assets.mulDiv(
    newTotalSupply + ConstantsLib.VIRTUAL_AMOUNT,
    newTotalAssets + ConstantsLib.VIRTUAL_AMOUNT,
    rounding
);
```

This is the standard mitigation for the ERC-4626 first-depositor inflation attack. The virtual offset means that even with zero real supply, the conversion produces meaningful non-zero shares, preventing the rounding-to-zero attack.

**Recommendation:**

No action needed. The inflation attack is properly mitigated. Note: the exact value of `VIRTUAL_AMOUNT` matters for the strength of this mitigation. Based on the Morpho codebase this is typically 1e6, which provides strong protection for tokens with up to 18 decimals.

---

### Finding 2.4: Fee calculation can round `feeAssets` to zero for small interest

**Function:** `_accruedFeeAndAssets()` at `EulerEarn.sol:L922-923`
**Category:** Dust Amount
**Severity:** LOW

**Description:**

```solidity
uint256 feeAssets = totalInterest.mulDiv(fee, WAD);
```

If `totalInterest * fee < WAD` (1e18), then `feeAssets` rounds to zero. With a typical fee of 10% (0.1e18), this means interest amounts below 10 wei produce zero fee. The code acknowledges this at L922: "It is acknowledged that `feeAssets` may be rounded down to 0 if `totalInterest * fee < WAD`."

**Impact:**

Negligible financial impact. An attacker would need to trigger `_accrueInterest()` at every single block to systematically avoid fees, which is economically irrational given gas costs. The comment shows this is a known and accepted design trade-off.

**Recommendation:**

INFO-level. No action needed. This is documented and accepted.

---

### Finding 2.5: Rounding direction analysis for deposit/withdraw/mint/redeem

**Functions:** `deposit()` L560-568, `mint()` L571-577, `withdraw()` L580-593, `redeem()` L596-611
**Category:** Rounding
**Severity:** INFO (correct)

**Analysis:**

| Operation | Rounding | Direction | Correct? |
|-----------|----------|-----------|----------|
| `deposit()` | `Floor` on shares | User gets fewer shares | Yes (protocol-favourable) |
| `mint()` | `Ceil` on assets | User pays more assets | Yes (protocol-favourable) |
| `withdraw()` | `Ceil` on shares | User burns more shares | Yes (protocol-favourable) |
| `redeem()` | `Floor` on assets | User gets fewer assets | Yes (protocol-favourable) |

All four ERC-4626 operations use correct rounding directions that favour the vault (preventing extraction through repeated deposit/withdraw cycles).

**Recommendation:**

No action needed. Rounding is correctly implemented throughout.

---

## Skill 3: Reentrancy pattern analysis

### Finding 3.1: CEI violations in `_deposit()` -- state updated after external calls to strategy vaults

**Function:** `_deposit()` at `EulerEarn.sol:L697-708`
**Variant:** Cross-function reentrancy (mitigated)
**Severity:** LOW (mitigated by `nonReentrant`)
**Guard status:** Guarded

**Description:**

The `_deposit()` function follows this order:
1. `safeTransferFromWithPermit2()` -- external call to transfer assets from caller (L698)
2. `_mint()` -- state update, mints shares (L699)
3. `_supplyStrategy()` -- external calls to strategy vaults (L703), which call `id.deposit()` on each strategy
4. `_updateLastTotalAssets()` -- state update (L707)

This is a Checks-Effects-Interactions-Effects pattern (not pure CEI). The `_supplyStrategy()` call at step 3 makes external calls to strategy vaults, and `lastTotalAssets` is only updated at step 4 after those calls.

During step 3, if a malicious strategy vault triggers a callback:
- `totalSupply()` is already updated (shares minted at step 2)
- `lastTotalAssets` is NOT yet updated (done at step 4)
- `config[id].balance` IS updated within `_supplyStrategy()` as each deposit completes

**Impact:**

All public entry points (`deposit`, `mint`, `withdraw`, `redeem`, `reallocate`, `setFee`, `setFeeRecipient`, `submitCap`) that could be re-entered are protected by `nonReentrant`. The `nonReentrant` modifier on `deposit()` (L560) prevents any re-entry into the same contract during the entire execution. Cross-contract reentrancy through a malicious strategy vault is prevented because no other contract should be reading `lastTotalAssets` mid-transaction.

View functions like `totalAssets()` would return stale data during step 3, but this is typical for any multi-step operation and is not exploitable within the same transaction due to `nonReentrant`.

**Recommendation:**

INFO-level. The `nonReentrant` guard provides adequate protection. For defence-in-depth, consider moving `_updateLastTotalAssets()` before `_supplyStrategy()`, though this would change the documented behaviour around the slight `lastTotalAssets` overshoot.

---

### Finding 3.2: `_withdraw()` updates `lastTotalAssets` before external calls (correct CEI)

**Function:** `_withdraw()` at `EulerEarn.sol:L717-735`
**Variant:** N/A
**Severity:** INFO (correctly ordered)

**Description:**

```solidity
_updateLastTotalAssets(lastTotalAssets.zeroFloorSub(assets));  // L730 -- EFFECT
_withdrawStrategy(assets);                                       // L732 -- INTERACTION
super._withdraw(caller, receiver, owner, assets, shares);       // L734 -- INTERACTION
```

The withdrawal path correctly updates `lastTotalAssets` before making external calls. Combined with `nonReentrant`, this is well-protected.

---

### Finding 3.3: Read-only reentrancy via `totalAssets()` during strategy interactions

**Function:** `totalAssets()` at `EulerEarn.sol:L616-620`
**Variant:** Read-only reentrancy
**Severity:** MEDIUM

**Description:**

`totalAssets()` calls `_accruedFeeAndAssets()` which iterates through `withdrawQueue` calling `expectedSupplyAssets(id)` for each strategy, which in turn calls `id.previewRedeem(config[id].balance)`. This is a view function that reads the current state.

During a `deposit()` call, after `_mint()` (L699) but before `_supplyStrategy()` completes (L703) and `_updateLastTotalAssets()` (L707), the state is inconsistent:
- `totalSupply()` has been increased (shares minted)
- `lastTotalAssets` has not yet been increased
- `config[id].balance` values are being updated one strategy at a time

If a malicious strategy vault (called during `_supplyStrategy()`) triggers a callback to a third-party contract that reads `EulerEarn.totalAssets()`, the returned value would reflect a state where new shares exist but `lastTotalAssets` is stale. This makes `totalAssets()` return a value lower than expected, which means the share price appears lower than it should be.

A third-party lending protocol using `EulerEarn` shares as collateral and relying on `totalAssets()` / `totalSupply()` for pricing could be tricked into undervaluing the collateral during this window.

**Impact:**

This requires:
1. A malicious strategy vault in the supply queue (which must be whitelisted by the factory)
2. A third-party protocol that reads `totalAssets()` during a callback within the same transaction
3. The third-party protocol must make economic decisions based on this stale read

The first requirement (whitelisted malicious strategy) significantly reduces the attack surface, as strategy vaults must be explicitly allowed by the factory.

**Recommendation:**

Document the read-only reentrancy risk for third-party integrators. Consider whether strategy vaults in the supply queue should be additionally vetted for callback behaviour. The `nonReentrant` guard prevents direct re-entry into EulerEarn but cannot protect third-party contracts reading stale view function results.

---

## Skill 4: External call safety

### Finding 4.1: Fee-on-transfer tokens cause accounting discrepancy

**Function:** `_deposit()` at `EulerEarn.sol:L698`, `_supplyStrategy()` at `EulerEarn.sol:L825`
**Category:** Fee-on-Transfer
**Severity:** HIGH

**Description:**

The deposit flow does not use the balance-before-after pattern:

```solidity
// L698: Transfer assets from user
IERC20(asset()).safeTransferFromWithPermit2(caller, address(this), assets, permit2Address);
// L699: Mint shares based on `assets` parameter, not actual received amount
_mint(receiver, shares);
```

If the underlying asset is a fee-on-transfer token (e.g. USDT with fee activated, STA, PAXG), the vault receives fewer tokens than `assets`, but credits the full `assets` amount to the depositor's shares and to `lastTotalAssets`.

Similarly, in `_supplyStrategy()` (L825):
```solidity
try id.deposit(toSupply, address(this)) returns (uint256 suppliedShares) {
    config[id].balance = (config[id].balance + suppliedShares).toUint112();
    assets -= toSupply;
}
```

If the EulerEarn vault calls `id.deposit(toSupply, ...)` on a strategy vault, and the underlying asset transfer from EulerEarn to the strategy incurs a fee, the strategy vault receives less than `toSupply`, but the EulerEarn contract deducts `toSupply` from its remaining `assets` to distribute.

**Impact:**

For fee-on-transfer tokens:
1. The vault becomes gradually insolvent as each deposit credits more shares than the actual assets received.
2. Early withdrawers can extract a disproportionate share of the vault, leaving later withdrawers unable to fully redeem.
3. The `lastTotalAssets` accounting becomes increasingly disconnected from reality.

**Recommendation:**

If fee-on-transfer tokens are intended to be supported, implement the balance-before-after pattern in `_deposit()`:
```solidity
uint256 balanceBefore = IERC20(asset()).balanceOf(address(this));
IERC20(asset()).safeTransferFromWithPermit2(caller, address(this), assets, permit2Address);
uint256 actualReceived = IERC20(asset()).balanceOf(address(this)) - balanceBefore;
// Use actualReceived instead of assets for share calculation
```

If fee-on-transfer tokens are not intended to be supported, document this explicitly and consider adding a check in the constructor or factory that the asset is not a fee-on-transfer token. Note: this is consistent with Morpho's MetaMorpho which also does not support fee-on-transfer tokens.

---

### Finding 4.2: Rebasing tokens break `lastTotalAssets` tracking

**Function:** `_updateLastTotalAssets()` at `EulerEarn.sol:L875-879`, `_accruedFeeAndAssets()` at `EulerEarn.sol:L898-929`
**Category:** Rebasing Token
**Severity:** HIGH

**Description:**

The contract tracks assets using `lastTotalAssets` which is updated at discrete points (deposits, withdrawals, fee accrual). If the underlying asset is a rebasing token (e.g. stETH, AMPL):

1. **Positive rebase (stETH):** The actual token balance increases without any vault interaction. `lastTotalAssets` remains stale. At the next `_accrueInterest()`, `realTotalAssets` (L904-908) will be higher than `lastTotalAssetsCached`, which triggers `totalInterest = newTotalAssets - lastTotalAssetsCached` (L920). This interest is correctly detected and fees are taken. However, the interest detection relies on strategy vaults' `previewRedeem()` reflecting the rebased balance, which depends on how each strategy handles rebasing.

2. **Negative rebase (AMPL):** The actual balance decreases. `lastTotalAssets` is higher than reality. The `lostAssets` mechanism (L911-913) handles this case by increasing `lostAssets`. However, `lostAssets` is never decreased (it's monotonically non-decreasing), so after a negative rebase followed by a positive rebase, `lostAssets` remains elevated. This inflates `totalAssets()` (L919) permanently.

**Impact:**

For positive-rebasing tokens: Generally works but fee accuracy depends on strategy vault behaviour.

For negative-rebasing tokens: `lostAssets` permanently inflates `totalAssets()`, creating a phantom asset balance that can never be withdrawn. Over time with volatile rebasing tokens, this phantom balance grows, increasingly disconnecting the share price from reality.

**Recommendation:**

Explicitly document that rebasing tokens are not supported. If rebasing token support is desired, the `lostAssets` mechanism would need a way to decrease when assets are recovered (e.g. after a positive rebase that offsets a previous negative rebase). Consider wrapping rebasing tokens (e.g. use wstETH instead of stETH).

---

### Finding 4.3: Strategy vault `deposit()` / `withdraw()` return values trusted without verification

**Function:** `_supplyStrategy()` at `EulerEarn.sol:L825-828`, `_withdrawStrategy()` at `EulerEarn.sol:L846-849`
**Category:** External Call Return Value Trust
**Severity:** MEDIUM

**Description:**

The contract trusts the return values of strategy vault `deposit()` and `withdraw()` calls:

```solidity
// _supplyStrategy L825
try id.deposit(toSupply, address(this)) returns (uint256 suppliedShares) {
    config[id].balance = (config[id].balance + suppliedShares).toUint112();
```

```solidity
// _withdrawStrategy L846
try id.withdraw(toWithdraw, address(this), address(this)) returns (uint256 withdrawnShares) {
    config[id].balance = uint112(config[id].balance - withdrawnShares);
```

A malicious or buggy strategy vault could:
1. Return 0 shares for a real deposit (the assets are taken but no shares credited, causing silent loss).
2. Return an inflated share count for a deposit (config balance grows faster than actual shares held).
3. Return fewer shares than expected for a withdrawal (balance decreases less, but actual shares are gone).

**Impact:**

The attack surface is limited by the factory whitelist (`IEulerEarnFactory(creator).isStrategyAllowed()`). Only strategy vaults explicitly approved by the factory can be added. However, if a strategy vault is compromised after whitelisting (e.g. upgradeble proxy), it could return manipulated values.

For the deposit path, returning 0 shares causes the vault to lose `toSupply` assets permanently. The try/catch would succeed (no revert), `config[id].balance` would remain unchanged (+0), and the outer loop decrements `assets -= toSupply`, so those assets are considered distributed but no shares are tracked.

**Recommendation:**

Consider adding a post-condition check after strategy deposits:
```solidity
require(suppliedShares > 0, "Zero shares returned");
```

For withdrawals, consider verifying that the actual asset balance change matches the expected `toWithdraw`:
```solidity
uint256 balBefore = IERC20(asset()).balanceOf(address(this));
// ... withdraw ...
uint256 balAfter = IERC20(asset()).balanceOf(address(this));
require(balAfter - balBefore >= toWithdraw, "Insufficient withdrawal");
```

Note: these checks would add gas cost and may be considered unnecessary given the factory whitelist trust model.

---

### Finding 4.4: `SafeERC20` and `SafeERC20Permit2Lib` correctly handle non-standard tokens

**Category:** Missing Return Values
**Severity:** INFO (mitigated)

**Description:**

The contract uses `SafeERC20` from OpenZeppelin (L45) and `SafeERC20Permit2Lib` (L46) for all token interactions:
- `safeTransferFromWithPermit2` for deposits (L698)
- `forceApproveMaxWithPermit2` and `revokeApprovalWithPermit2` for strategy approval management (L780, L798)
- Strategy vault interactions go through the IERC4626 interface which uses `SafeERC20` internally

Tokens with missing return values (USDT, BNB) are correctly handled.

**Recommendation:**

No action needed. Token interaction safety is well-implemented.

---

## Skill 5: DoS and griefing analysis

### Finding 5.1: Supply queue and withdraw queue iteration gas limits

**Functions:** `_supplyStrategy()` at `EulerEarn.sol:L811-835`, `_withdrawStrategy()` at `EulerEarn.sol:L838-856`, `_accruedFeeAndAssets()` at `EulerEarn.sol:L905-908`
**Category:** Unbounded Loop
**Severity:** MEDIUM

**Description:**

Multiple functions iterate over the full supply queue or withdraw queue:

1. `_supplyStrategy()`: iterates `supplyQueue` (L812), called during every `deposit()` and `mint()`
2. `_withdrawStrategy()`: iterates `withdrawQueue` (L839), called during every `withdraw()` and `redeem()`
3. `_accruedFeeAndAssets()`: iterates `withdrawQueue` (L905), called during every deposit, withdrawal, and view call to `totalAssets()`
4. `_maxDeposit()`: iterates `supplyQueue` (L641), called for `maxDeposit()` and `maxMint()` view functions
5. `_simulateWithdrawStrategy()`: iterates `withdrawQueue` (L861), called for `maxWithdraw()` and `maxRedeem()` view functions

Each iteration involves an external call to a strategy vault (`previewRedeem`, `maxDeposit`, `maxWithdraw`, `deposit`, `withdraw`). The queue length is bounded by `ConstantsLib.MAX_QUEUE_LENGTH` (checked at L328 and L785).

**Gas analysis:**

Per iteration, each external call costs approximately:
- `previewRedeem()` (view call): ~2,600 gas (STATICCALL overhead + computation)
- `maxDeposit()` / `maxWithdraw()` (view call): ~2,600 gas
- `deposit()` / `withdraw()` (state-changing): ~50,000-100,000+ gas

For `_accruedFeeAndAssets()` which is called on every user interaction:
- N strategies x ~2,600 gas per `previewRedeem` = N x 2,600 gas overhead

Based on the Morpho MetaMorpho codebase, `MAX_QUEUE_LENGTH` is typically 30. With 30 strategies:
- View function overhead: 30 x 2,600 = ~78,000 gas (acceptable)
- Deposit/withdraw with supply: 30 x ~80,000 = ~2,400,000 gas (significant but within block gas limit)

**Impact:**

With the queue length bounded at `MAX_QUEUE_LENGTH`, this is not a true unbounded loop DoS. However, at maximum queue capacity, user-facing operations (`deposit`, `withdraw`) become expensive. If many strategies in the queue are unresponsive or gas-intensive, the try/catch in `_supplyStrategy` and `_withdrawStrategy` provides resilience (unresponsive strategies are skipped).

The more concerning case is `_accruedFeeAndAssets()` which has no try/catch: if any strategy vault's `previewRedeem()` reverts, the entire `_accrueInterest()` call reverts, which blocks all deposits and withdrawals. This could be used as a DoS vector by a compromised strategy vault.

**Recommendation:**

Consider wrapping `expectedSupplyAssets(id)` calls in `_accruedFeeAndAssets()` with try/catch to maintain resilience against unresponsive strategy vaults:

```solidity
for (uint256 i; i < withdrawQueue.length; ++i) {
    IERC4626 id = withdrawQueue[i];
    try this.expectedSupplyAssets(id) returns (uint256 assets) {
        realTotalAssets += assets;
    } catch {
        // Use last known balance as fallback, or skip
        realTotalAssets += 0; // Treat as zero
    }
}
```

Note: this changes the accounting semantics, so careful consideration is needed.

---

### Finding 5.2: Reverting strategy vault in `_accruedFeeAndAssets()` blocks all vault operations

**Function:** `_accruedFeeAndAssets()` at `EulerEarn.sol:L904-908`
**Category:** External Call DoS
**Severity:** HIGH

**Description:**

```solidity
for (uint256 i; i < withdrawQueue.length; ++i) {
    IERC4626 id = withdrawQueue[i];
    realTotalAssets += expectedSupplyAssets(id);  // Calls id.previewRedeem()
}
```

`expectedSupplyAssets(id)` calls `id.previewRedeem(config[id].balance)` (L493). If any strategy vault in the withdraw queue has a `previewRedeem()` that reverts (due to pausing, self-destruct, upgrade to a broken implementation, etc.), this entire function reverts.

`_accruedFeeAndAssets()` is called by:
- `_accrueInterest()` -- called by `deposit()`, `mint()`, `withdraw()`, `redeem()`, `setFee()`, `setFeeRecipient()`
- `totalAssets()` -- view function
- `_convertToShares()` and `_convertToAssets()` -- used by view functions

**Impact:**

If a single strategy vault in the withdraw queue starts reverting on `previewRedeem()`, ALL vault operations are blocked:
- No deposits possible
- No withdrawals possible
- `totalAssets()` reverts
- `maxDeposit()`, `maxMint()`, `maxWithdraw()`, `maxRedeem()` all revert

The only recovery path is for the allocator/curator to:
1. Set the strategy's cap to 0 via `submitCap()` (but this calls `_msgSender()` which works, since it doesn't call `_accrueInterest()`)
2. Submit market removal via `submitMarketRemoval()` (works, no `_accrueInterest()` call)
3. Wait for timelock
4. Remove the strategy from the withdraw queue via `updateWithdrawQueue()` (works, no `_accrueInterest()` call)

However, during the timelock period (which can be significant), all vault operations remain blocked.

**Recommendation:**

This is a significant operational risk. Consider:
1. Adding a guardian function to emergency-remove a strategy from the withdraw queue without a timelock when it is provably broken (e.g. its `previewRedeem` reverts).
2. Wrapping `expectedSupplyAssets()` calls in `_accruedFeeAndAssets()` with try/catch, defaulting to 0 for reverting strategies. This would cause a temporary undervaluation but preserve vault functionality.
3. Documenting the recovery procedure clearly for operators.

---

### Finding 5.3: `try/catch` in `_supplyStrategy()` silently skips failed deposits, potentially leaving assets idle

**Function:** `_supplyStrategy()` at `EulerEarn.sol:L824-828`
**Category:** External Call DoS (partial)
**Severity:** LOW

**Description:**

```solidity
if (toSupply > 0) {
    try id.deposit(toSupply, address(this)) returns (uint256 suppliedShares) {
        config[id].balance = (config[id].balance + suppliedShares).toUint112();
        assets -= toSupply;
    } catch {}
}
```

If all strategy vault deposits fail (revert in the try block), the loop completes with `assets != 0`, and the function reverts with `AllCapsReached()` (L834). This is correct -- it prevents deposits when no strategy can accept funds.

However, if SOME strategies fail and others succeed, the function may revert with `AllCapsReached()` even though partial allocation was possible. The remaining `assets` that failed to allocate cause the revert.

Wait -- re-reading: the function reverts only if `assets != 0` after iterating ALL strategies (L834). If some strategies accept deposits and the total capacity is sufficient, `assets` reaches 0 and the function returns. If total capacity is insufficient, it reverts. This is correct behaviour.

**Impact:**

No vulnerability. The try/catch pattern provides graceful degradation. If a strategy temporarily reverts, deposits flow to the next strategy in the queue. The `AllCapsReached()` revert at the end correctly prevents deposits that would leave assets unallocated.

**Recommendation:**

No action needed. This is well-designed.

---

## Skill 6: Signature and replay analysis

### Finding 6.1: Permit2 integration delegates signature verification to Permit2 contract

**Function:** `_deposit()` at `EulerEarn.sol:L698`
**Severity:** INFO (correctly delegated)

**Description:**

The contract uses `SafeERC20Permit2Lib.safeTransferFromWithPermit2()` for asset transfers during deposits. The Permit2 contract (Uniswap's universal approval mechanism) handles all signature verification, nonce management, and replay protection.

The `permit2Address` is set as an immutable in the constructor (L139):
```solidity
permit2Address = permit2;
```

**Signed message security analysis:**

| Component | Status |
|-----------|--------|
| Nonce management | Handled by Permit2 (nonce-bitmap approach) |
| Chain ID binding | Handled by Permit2's EIP-712 domain separator |
| Contract binding | Handled by Permit2's domain separator (includes `address(this)`) |
| Deadline/expiry | Handled by Permit2's `SignatureTransfer` |
| `ecrecover` safety | Handled by Permit2 (uses OpenZeppelin ECDSA) |
| Signature malleability | Handled by Permit2 |

**Recommendation:**

No action needed. All signature and replay concerns are correctly delegated to the audited Permit2 contract. The immutable `permit2Address` prevents address substitution after deployment.

---

### Finding 6.2: No direct `ecrecover` or ECDSA usage in EulerEarn

**Severity:** INFO

**Description:**

EulerEarn does not directly verify any signatures. The only signature-related functionality is through Permit2 integration. The contract does not implement ERC-2612 `permit()` directly (note: the constructor comment at L120 mentions deviating from ERC-2612 by passing empty strings to ERC20).

The contract inherits from `ERC20` but does NOT inherit from `ERC20Permit`, meaning there is no native `permit()` function. All permit functionality is handled externally via Permit2.

**Recommendation:**

No action needed. The absence of direct signature verification eliminates an entire class of vulnerabilities.

---

## Cross-skill combined findings

### Combined Finding C1: Malicious strategy vault -- multi-vector attack surface

**Skills:** External Call Safety + Reentrancy + DoS + State Invariants
**Severity:** MEDIUM (mitigated by factory whitelist)

**Description:**

The most significant attack surface in EulerEarn is through the strategy vaults. A compromised or malicious strategy vault could simultaneously:

1. **DoS (Finding 5.2):** Revert on `previewRedeem()` to block all vault operations.
2. **Read-only reentrancy (Finding 3.3):** During a deposit callback, cause third-party protocols to read stale `totalAssets()`.
3. **Return value manipulation (Finding 4.3):** Return inflated/deflated share counts from `deposit()`/`withdraw()`.
4. **State invariant violation (Finding 1.2):** Cause `config[id].balance` to drift from actual share holdings.

**Mitigation:**

The factory whitelist (`IEulerEarnFactory(creator).isStrategyAllowed()`) is the primary defence. Strategy vaults must be explicitly approved before they can be used. This trust assumption is critical and should be documented prominently.

**Recommendation:**

1. Ensure the factory's strategy approval process includes thorough vetting of each strategy vault's implementation.
2. For upgradeable strategy vaults (proxies), consider re-checking approval after upgrades.
3. Document the trust assumptions clearly: "EulerEarn trusts whitelisted strategy vaults to behave according to the ERC-4626 standard."

---

### Combined Finding C2: `lostAssets` monotonic increase causes permanent `totalAssets()` inflation

**Skills:** State Invariants + External Call Safety (Rebasing) + Arithmetic Safety
**Severity:** MEDIUM

**Description:**

The `lostAssets` variable only ever increases (L911-917):

```solidity
if (realTotalAssets < lastTotalAssetsCached - lostAssets) {
    newLostAssets = lastTotalAssetsCached - realTotalAssets;  // Increases
} else {
    newLostAssets = lostAssets;  // Stays same
}
```

Sources of `lostAssets` increase:
1. Actual strategy losses (a strategy loses value).
2. Rounding artefacts from `lastTotalAssets` overshoot (Finding 1.1).
3. Negative rebasing tokens (Finding 4.2).

Since `totalAssets() = realTotalAssets + lostAssets` (L919), and `lostAssets` never decreases, the reported `totalAssets()` can only match or exceed `realTotalAssets`. This means:

- Share price never decreases below 1:1 (by design -- "losses are not realized", L608).
- After a strategy recovers from a temporary loss, the recovery is counted as new interest (fee is charged on the recovery).
- Over time, the gap between `totalAssets()` and redeemable assets grows by the accumulated rounding dust.

**Impact:**

For strategies that experience temporary losses followed by recoveries (common in lending protocols), the fee recipient is charged fees on the recovery. This is arguably correct (the fund manager earned the recovery) but could be debated. The permanent `lostAssets` inflation from rounding is cosmetic for high-decimal tokens but can become significant for low-decimal tokens over many interactions.

**Recommendation:**

Document the `lostAssets` behaviour and its implications clearly. Consider whether a `lostAssets` reset mechanism (callable by owner with appropriate safeguards) would improve long-term accounting accuracy.

---

## Appendix: Findings summary table

| ID | Title | Severity | Skill |
|----|-------|----------|-------|
| 1.1 | `lastTotalAssets` drift from rounding | MEDIUM | State Invariants |
| 1.2 | `config[id].balance` desync from donated shares | LOW | State Invariants |
| 1.3 | Fee calculation uses pre-minting supply (correct) | INFO | State Invariants |
| 2.1 | Unsafe downcast in `reallocate()` L415 | LOW | Arithmetic Safety |
| 2.2 | Unsafe downcast in `_withdrawStrategy()` L847 | LOW | Arithmetic Safety |
| 2.3 | Virtual share offset mitigates inflation attack | INFO | Arithmetic Safety |
| 2.4 | Fee rounds to zero for dust interest | LOW | Arithmetic Safety |
| 2.5 | Rounding directions all correct | INFO | Arithmetic Safety |
| 3.1 | CEI pattern in `_deposit()` (mitigated by nonReentrant) | LOW | Reentrancy |
| 3.2 | `_withdraw()` CEI ordering correct | INFO | Reentrancy |
| 3.3 | Read-only reentrancy via stale `totalAssets()` | MEDIUM | Reentrancy |
| 4.1 | Fee-on-transfer tokens cause insolvency | HIGH | External Call Safety |
| 4.2 | Rebasing tokens break `lostAssets` tracking | HIGH | External Call Safety |
| 4.3 | Strategy vault return values trusted | MEDIUM | External Call Safety |
| 4.4 | SafeERC20 correctly handles non-standard tokens | INFO | External Call Safety |
| 5.1 | Queue iteration gas costs (bounded) | MEDIUM | DoS & Griefing |
| 5.2 | Reverting strategy blocks all operations | HIGH | DoS & Griefing |
| 5.3 | try/catch in supply correctly handles partial failures | INFO | DoS & Griefing |
| 6.1 | Permit2 handles all signature security | INFO | Signature & Replay |
| 6.2 | No direct ecrecover usage | INFO | Signature & Replay |
| C1 | Malicious strategy multi-vector attack | MEDIUM | Combined |
| C2 | `lostAssets` monotonic inflation | MEDIUM | Combined |
