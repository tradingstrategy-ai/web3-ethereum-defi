# Security Review -- EulerEarn

> This review was performed by an AI assistant using the Pashov Skills solidity-auditor methodology. AI analysis can never verify the complete absence of vulnerabilities and no guarantee of security is given. Team security reviews, bug bounty programmes, and on-chain monitoring are strongly recommended. For a consultation regarding your projects' security, visit [https://www.pashov.com](https://www.pashov.com)

---

## Scope

|                                  |                                                        |
| -------------------------------- | ------------------------------------------------------ |
| **Mode**                         | `EulerEarn.sol`                                        |
| **Files reviewed**               | `src/EulerEarn.sol`                                    |
| **Confidence threshold (1-100)** | 75                                                     |

---

## Findings

| # | Confidence | Title |
|---|---|---|
| 1 | [80] | Arithmetic underflow in `_withdrawStrategy` try body is not caught by catch, causing withdrawal DoS |
| | | **Below Confidence Threshold** |
| 2 | [60] | Read-only reentrancy via strategy vault callbacks exposes transitional state to external consumers |
| 3 | [55] | Duplicate entries in supply queue inflate `maxDeposit` return value |

---

[80] **1. Arithmetic underflow in `_withdrawStrategy` try body is not caught by catch, causing withdrawal DoS**

`EulerEarn._withdrawStrategy` (line 838-856) -- Confidence: 80

**Description**

In `_withdrawStrategy`, when a strategy vault's `withdraw()` call succeeds, the returned `withdrawnShares` value is used in an unsafe `uint112` cast inside the try success block (line 847: `config[id].balance = uint112(config[id].balance - withdrawnShares)`). In Solidity, the `catch` block only catches reverts originating from the external call expression itself -- panics or reverts occurring in the success block's body propagate upward normally and are NOT caught by the catch. If a strategy vault's `withdraw()` returns a `withdrawnShares` value that exceeds `config[id].balance` (possible due to rounding differences between `previewRedeem` and actual share burning, or a non-standard ERC4626 implementation), the subtraction underflows, causing a panic that reverts the entire user withdrawal transaction. Since the try/catch was intended to skip misbehaving vaults and continue to the next strategy, this breaks that safety mechanism and can result in a DoS on all withdrawals if even one strategy vault in the withdraw queue exhibits this rounding behaviour.

The same pattern exists in `_supplyStrategy` (line 826: `config[id].balance = (config[id].balance + suppliedShares).toUint112()`) where `toUint112()` SafeCast can revert inside the try body if the sum exceeds `type(uint112).max`, though this is less likely in practice.

**Fix**

```diff
  function _withdrawStrategy(uint256 assets) internal {
      for (uint256 i; i < withdrawQueue.length; ++i) {
          IERC4626 id = withdrawQueue[i];

          uint256 toWithdraw = UtilsLib.min(maxWithdrawFromStrategy(id), assets);

          if (toWithdraw > 0) {
-             // Using try/catch to skip vaults that revert.
-             try id.withdraw(toWithdraw, address(this), address(this)) returns (uint256 withdrawnShares) {
-                 config[id].balance = uint112(config[id].balance - withdrawnShares);
-                 assets -= toWithdraw;
-             } catch {}
+             // Using try/catch to skip vaults that revert.
+             try id.withdraw(toWithdraw, address(this), address(this)) returns (uint256 withdrawnShares) {
+                 // Clamp withdrawnShares to config balance to prevent underflow from rounding discrepancies.
+                 uint112 currentBalance = config[id].balance;
+                 if (withdrawnShares > currentBalance) {
+                     withdrawnShares = currentBalance;
+                 }
+                 config[id].balance = uint112(currentBalance - withdrawnShares);
+                 assets -= toWithdraw;
+             } catch {}
          }

          if (assets == 0) return;
      }

      if (assets != 0) revert ErrorsLib.NotEnoughLiquidity();
  }
```

---

[60] **2. Read-only reentrancy via strategy vault callbacks exposes transitional state to external consumers**

`EulerEarn._withdrawStrategy` / `EulerEarn._supplyStrategy` -- Confidence: 60

**Description**

During `_withdrawStrategy` and `_supplyStrategy`, the contract makes external calls to strategy vaults (`id.withdraw()`, `id.deposit()`). These external calls may trigger callbacks (e.g., through the underlying asset's transfer hooks) during which EulerEarn's public view functions like `totalAssets()`, `convertToShares()`, and `convertToAssets()` can be called. At that point, `lastTotalAssets` has already been updated but `config[id].balance` values are only partially updated across the iteration of the withdraw/supply queue, causing these view functions to return values that do not represent a consistent state. External protocols that rely on EulerEarn's `totalAssets()` for pricing or collateral valuation could make incorrect decisions based on this transitional state.

---

[55] **3. Duplicate entries in supply queue inflate `maxDeposit` return value**

`EulerEarn.setSupplyQueue` / `EulerEarn._maxDeposit` -- Confidence: 55

**Description**

The `setSupplyQueue` function (line 325) accepts a user-supplied array of strategy vaults and validates that each entry has a non-zero cap, but does not check for duplicate entries. If an allocator (privileged role) sets a supply queue containing the same vault multiple times, the `_maxDeposit()` view function (line 640) iterates the queue and sums `min(cap - supplyAssets, maxDeposit)` for each entry. Since `supplyAssets` is read fresh per iteration but cap room can appear available for each duplicate, the reported `maxDeposit` is inflated. Users relying on `maxDeposit()` to determine their deposit amount would have their deposit revert with `AllCapsReached` when the actual cap is reached after the first iteration processes the vault.
