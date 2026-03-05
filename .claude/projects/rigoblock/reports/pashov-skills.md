# Security Review -- Rigoblock SmartPool

---

## Scope

|                                  |                                                        |
| -------------------------------- | ------------------------------------------------------ |
| **Mode**                         | ALL (full implementation + proxy)                      |
| **Files reviewed**               | `SmartPool.sol` . `ISmartPool.sol` . `MixinActions.sol`<br>`MixinOwnerActions.sol` . `MixinConstants.sol` . `MixinImmutables.sol`<br>`MixinStorage.sol` . `MixinPoolState.sol` . `MixinPoolValue.sol`<br>`MixinStorageAccessible.sol` . `MixinAbstract.sol` . `MixinFallback.sol`<br>`MixinInitializer.sol` . `NavImpactLib.sol` . `VirtualStorageLib.sol`<br>`ReentrancyGuardTransient.sol` . `SafeTransferLib.sol` . `EnumerableSet.sol`<br>`ApplicationsLib.sol` . `TransientStorage.sol` . `SlotDerivation.sol`<br>`TransientSlot.sol` . `StorageLib.sol` . `VersionLib.sol`<br>`NavComponents.sol` . `ExternalApp.sol` . `Applications.sol` . `Crosschain.sol`<br>`IEApps.sol` . `IECrosschain.sol` . `IEOracle.sol` . `IMinimumVersion.sol`<br>`IAuthority.sol` . `IExtensionsMap.sol` . `IKyc.sol` . `IRigoblockPoolProxyFactory.sol`<br>`ISmartPoolActions.sol` . `ISmartPoolEvents.sol` . `ISmartPoolFallback.sol`<br>`ISmartPoolImmutable.sol` . `ISmartPoolInitializer.sol` . `ISmartPoolOwnerActions.sol`<br>`ISmartPoolState.sol` . `IStorageAccessible.sol` . `IERC20.sol`<br>`RigoblockPoolProxy (Contract.sol)` |
| **Confidence threshold (1-100)** | 75                                                     |

---

## Methodology

Applied the Pashov skills parallelised audit methodology. All 170 attack vectors from the reference corpus were triaged (Skip/Borderline/Survive), with surviving vectors subjected to deep-pass analysis including FP gate validation (concrete attack path, entry point reachability, guard absence) and confidence scoring per the judging framework.

---

## Findings

[80] **1. Extension Fallback Delegatecall Routing Ignores Caller Identity for Extension-Mapped Selectors**

`MixinFallback.fallback` . Confidence: 80

**Description**

The fallback function routes calls to extensions using `delegatecall` or `staticcall` based on the `shouldDelegatecall` flag returned by `_extensionsMap.getExtensionBySelector(msg.sig)`. For extension-mapped selectors (where `target != address(0)`), the delegatecall/staticcall decision is made entirely by the extensionsMap configuration, NOT based on `msg.sender`. This means that if an extension function is mapped with `shouldDelegatecall = true` (e.g., `donate()` in ECrosschain, which must modify pool state), then ANY external caller -- not just the pool owner or trusted bridge contracts -- can trigger a delegatecall to that extension in the pool's context. The security of all state-modifying extension functions depends entirely on the extension's internal access control, with no caller validation at the fallback routing layer. A misconfigured or insufficiently guarded extension would allow arbitrary callers to modify pool state including virtual supply, token registries, and NAV parameters.

**Fix**

```diff
  // For extension-mapped selectors, enforce caller restrictions for delegatecall
  if (target == _ZERO_ADDRESS) {
      target = IAuthority(authority).getApplicationAdapter(msg.sig);
      require(target != _ZERO_ADDRESS, PoolMethodNotAllowed());
      ...
      shouldDelegatecall = msg.sender == pool().owner;
  }
+ // For extension targets with delegatecall, also verify caller is authorized
+ if (shouldDelegatecall && target != _ZERO_ADDRESS) {
+     // Extensions that modify state should validate caller at the extension level
+     // OR: restrict delegatecall to owner/whitelisted callers at the fallback level
+ }
```
---

[80] **2. Oracle Price Manipulation via Flash Loan During Mint/Burn With Non-Base Tokens**

`MixinActions._mint` / `MixinActions._burn` . Confidence: 80

**Description**

When minting with a non-base token (`mintWithToken`) or burning for a non-base token (`burnForToken`), the token amount is converted to/from base token value using `IEOracle(address(this)).convertTokenAmount()`. The implementation-level protection against oracle manipulation is limited to a spread of 10-500 basis points (0.1%-5%). There is no implementation-level TWAP enforcement, deviation bounds, or multi-block delay. If the oracle extension uses spot prices or short-window TWAPs, an attacker could flash-loan manipulate the underlying price source (e.g., Uniswap V4 pool), call `mintWithToken` at the deflated price (getting excess shares), restore the price, and `burn` at the correct price for profit -- all within a single transaction. The spread of 10bps (current setting) provides negligible protection against flash-loan-scale manipulation. The `_computeTotalPoolValue` function also uses `convertBatchTokenAmounts` for NAV calculation, meaning the NAV itself can be manipulated if oracle prices are manipulable atomically.

**Fix**

```diff
  // In _mint, after oracle conversion:
  amountIn = uint256(
      IEOracle(address(this)).convertTokenAmount(tokenIn, amountIn.toInt256(), components.baseToken)
  );
+ // Add implementation-level sanity check: converted amount should be within
+ // reasonable bounds of the input amount based on known price range
+ // Or: enforce minimum TWAP window at the implementation level
```
---

[75] **3. Fee-on-Transfer Token Accounting Mismatch in mintWithToken**

`MixinActions._mint` . Confidence: 75

**Description**

When `mintWithToken` is called with a fee-on-transfer token (e.g., USDT with transfer tax, or deflation tokens), the function credits the user with shares based on the nominal `amountIn` parameter rather than the actual tokens received by the pool. The code executes `tokenIn.safeTransferFrom(msg.sender, address(this), amountIn)` followed by share calculation using `amountIn` directly, without measuring the pool's balance before and after the transfer. If a fee-on-transfer token is added to the accepted tokens set by the pool owner, the pool would systematically receive fewer tokens than accounted for, creating a deficit that existing holders bear when redeeming. Exploitation requires the pool owner to have added a fee-on-transfer token via `setAcceptableMintToken`, which also requires the token to have a valid price feed in the oracle extension.

---

[75] **4. updateUnitaryValue() Lacks Reentrancy Guard Enabling Cross-Function NAV Manipulation**

`MixinActions.updateUnitaryValue` / `MixinPoolValue._updateNav` . Confidence: 75

**Description**

The `updateUnitaryValue()` function is publicly callable and lacks the `nonReentrant` modifier. While `mint()` and `burn()` are protected by `nonReentrant`, the comment "Reentrancy protection provided by calling functions" is only accurate when `updateUnitaryValue()` is called indirectly through mint/burn. When called directly, or when re-entered during a token transfer callback within mint/burn (if an ERC-777-style token is in use), `updateUnitaryValue()` can write a manipulated NAV to storage. Specifically, if called during the window between a token transfer INTO the pool and the `totalSupply` increment in `_mint()`, the NAV computation would see increased token balances but unchanged total supply, producing an inflated `unitaryValue` that gets written to persistent storage. This inflated value would be used by subsequent mint operations (which each call `_updateNav()` but may find the already-written value if conditions haven't changed), potentially causing later depositors to receive fewer shares than deserved.

---

## Findings List

| # | Confidence | Title |
|---|---|---|
| 1 | [80] | Extension fallback delegatecall routing ignores caller identity for extension-mapped selectors |
| 2 | [80] | Oracle price manipulation via flash loan during mint/burn with non-base tokens |
| | | **Below Confidence Threshold** |
| 3 | [75] | Fee-on-transfer token accounting mismatch in mintWithToken |
| 4 | [75] | updateUnitaryValue() lacks reentrancy guard enabling cross-function NAV manipulation |

---

> This review was performed by an AI assistant using the Pashov skills methodology. AI analysis can never verify the complete absence of vulnerabilities and no guarantee of security is given. Team security reviews, bug bounty programmes, and on-chain monitoring are strongly recommended.
