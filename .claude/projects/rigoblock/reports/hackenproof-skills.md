# Rigoblock SmartPool security audit report

**Target:** Rigoblock SmartPool (ERC-1967 proxy-based pool/fund management protocol)
- Proxy: `0xEfa4bDf566aE50537A507863612638680420645C` (Solidity 0.8.17)
- Implementation: `0x1DB955265B8DC18715cAb12E805F9b71Fa545420` (Solidity 0.8.28, SmartPool)
- 45 Solidity source files, ~3,143 lines

**Methodology:** HackenProof bug bounty triage workflow -- scope verification, severity classification, PoC validation.
**Classification track:** Smart Contract (EVM/Solidity)

---

## Scope verification

All findings reference in-scope contracts within the SmartPool implementation deployed at the implementation address. The proxy at the proxy address delegates all calls to this implementation. The analysis covers the full implementation source tree including core actions, owner actions, pool value computation, fallback routing, libraries, and the proxy contract.

---

## [MEDIUM] Single EOA pool owner controls all privileged functions with no timelock or multisig protection

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinOwnerActions.sol:42-45`
- **Description:** The pool owner (`0xcA9F5049c1Ea8FC78574f94B7Cf5bE5fEE354C31`) is an EOA that controls all privileged functions guarded by the `onlyOwner` modifier: `setOwner`, `setTransactionFee`, `changeSpread`, `changeMinPeriod`, `setKycProvider`, `changeFeeCollector`, `setAcceptableMintToken`, and `purgeInactiveTokensAndApps`. There is no timelock, multisig, or governance delay on any of these operations. A compromised owner key enables immediate, unrestricted reconfiguration of all pool parameters.
- **Impact:** If the EOA private key is compromised, an attacker can instantaneously: (1) set transaction fee to the maximum 1% and redirect it to an attacker-controlled fee collector, (2) set the spread to the maximum 5%, (3) set a malicious KYC provider to block all mints except by the attacker, (4) set the minimum lockup to 30 days to trap existing holders, (5) transfer ownership. These actions combined enable value extraction from the pool and denial of service to all pool token holders. The fee and spread changes take effect on the very next mint/burn transaction, giving holders no opportunity to exit first.
- **Recommendation:** Use a multisig or governance contract as pool owner rather than an EOA. Implement a timelock on sensitive parameter changes (fee, spread, KYC provider, min period) to give pool token holders an exit window before changes take effect.

---

## [MEDIUM] Pool token balances use uint208 with unchecked truncation enabling silent overflow on large mints

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:200,206`
- **Description:** User balances are stored as `uint208` in the `UserAccount` struct (lines 200, 206 in `_allocateMintTokens`). The cast `uint208(feePool)` and `uint208(mintedAmount)` perform a narrowing conversion from `uint256` to `uint208` without any overflow check. While `totalSupply` is tracked as `uint256`, individual user balances silently truncate if they would exceed `2^208 - 1`. The `uint208` type can hold up to approximately `4.11 * 10^62`, which for an 18-decimal token is approximately `4.11 * 10^44` tokens. In practice this requires extreme amounts, but the protocol does not enforce that `totalSupply` is bounded to fit within `uint208`, meaning a mismatch between `totalSupply` (uint256) and the sum of all `userBalance` fields (uint208) could theoretically develop.
- **Impact:** If a single user's balance approaches or exceeds `2^208`, the narrowing cast silently truncates the value, resulting in the user receiving far fewer tokens than they should while `totalSupply` records the correct (larger) amount. This creates an accounting discrepancy between total supply and the sum of user balances, effectively destroying user tokens while the protocol's NAV calculation uses the inflated total supply. The practical exploitability is limited by the need for extreme token quantities but is enabled by the lack of any explicit bounds check.
- **Recommendation:** Add an explicit check that `mintedAmount <= type(uint208).max` before the narrowing cast, or use SafeCast for the uint208 conversion just as the contract already uses SafeCast for uint256-to-int256 conversions elsewhere.

---

## [MEDIUM] Adapter fallback routing trusts Authority's return value without verifying adapter is a contract

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/sys/MixinFallback.sol:36-68`
- **Description:** In the `fallback()` function, when a function selector is not found in the `_extensionsMap`, the contract queries `IAuthority(authority).getApplicationAdapter(msg.sig)` (line 36) and then delegates or static-calls to the returned address (lines 50-68). The returned `target` address is only checked for being non-zero (`require(target != _ZERO_ADDRESS, PoolMethodNotAllowed())`), but there is no validation that the address contains code (i.e. `target.code.length > 0`). If the Authority returns an address that is not a contract (e.g. an EOA or a self-destructed contract), the `delegatecall` or `staticcall` to that address will succeed silently and return empty data, since calls to non-contract addresses always succeed at the EVM level. This depends on the Authority governance behaving correctly, but represents a missing safety check in the pool implementation itself.
- **Impact:** A delegatecall to a non-contract address succeeds with no return data and no revert. If the owner triggers a fallback call to such a selector, the transaction completes silently without performing the intended action. This could lead to missed state updates. For staticcall paths (non-owner callers), the returned empty data could be misinterpreted by callers expecting meaningful return values. The risk is downstream of Authority governance quality, but the pool should not trust the Authority output without validation.
- **Recommendation:** Add a `target.code.length > 0` check after resolving the adapter address from the Authority, similar to the contract-check already performed in `setKycProvider` and the constructor's immutables validation.

---

## [MEDIUM] The updateUnitaryValue function lacks reentrancy protection and can be called by anyone

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:80-90`
- **Description:** The `updateUnitaryValue()` function is `external` and callable by anyone without access control. The function's own NatSpec comment states "Reentrancy protection provided by calling functions (mint, burn, depositV3, donate)" -- however, `updateUnitaryValue()` itself is a direct entry point that is NOT wrapped in the `nonReentrant` modifier. It calls `_updateNav()` which calls `_computeTotalPoolValue()`, which in turn calls external contracts (`IEApps(address(this)).getAppTokenBalances(...)`, `IEOracle(address(this)).convertBatchTokenAmounts(...)`, and `IERC20(token).balanceOf(address(this))`). Since `_updateNav()` writes to storage (`poolTokens().unitaryValue`), and the function lacks reentrancy protection, a malicious token or application contract called during NAV computation could re-enter `updateUnitaryValue()` to manipulate the stored NAV before the first call completes.
- **Impact:** A reentrancy during NAV computation could allow manipulating the stored `unitaryValue`. Since `unitaryValue` directly determines how many pool tokens are minted for a given deposit and how much base token is returned on burn, a manipulated NAV could enable extracting value from the pool. The attack requires a malicious token or application that the pool already holds (which requires owner cooperation to add), limiting practical exploitability. The combination of open access + no reentrancy guard + storage writes + external calls to potentially untrusted contracts constitutes a meaningful design weakness.
- **Recommendation:** Add the `nonReentrant` modifier to `updateUnitaryValue()`. The comment indicating that reentrancy protection comes from calling functions is incorrect for this direct entry point.

---

## [MEDIUM] ERC-20 transfer/transferFrom/approve functions are no-ops that silently return false

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/sys/MixinAbstract.sol:7-19`
- **Description:** The `MixinAbstract` contract implements `transfer`, `transferFrom`, and `approve` as empty functions that return the default value `false` (since `success` is declared but never assigned). These are standard ERC-20 methods that any contract or protocol interacting with pool tokens as ERC-20 tokens would call. The functions do not revert -- they silently succeed while returning `false` and performing no state changes. Any integrating contract that checks the return value will see a failure, but any contract that does not check (which is common, especially for older contracts) will believe the transfer succeeded when no tokens actually moved.
- **Impact:** Pool tokens cannot be freely transferred between addresses. More critically, any DeFi protocol, DEX, lending platform, or smart contract wallet that attempts to use standard ERC-20 interactions with pool tokens will either: (1) silently fail if they do not check return values, leading to lost tokens or broken accounting, or (2) revert if they use SafeERC20-style calls that require `true`. This means pool tokens are non-composable and cannot be used in any standard DeFi context. Users holding pool tokens in smart contract wallets may find their tokens effectively trapped if the wallet uses `transfer` internally. The `balanceOf` and `totalSupply` functions work normally, so monitoring tools will show balances that cannot actually be moved, creating confusion.
- **Recommendation:** Either revert explicitly in these functions to make the non-transferability clear, or implement proper ERC-20 transfer functionality. If the design intent is non-transferable pool tokens, use a clear revert with a descriptive error message rather than a silent false return.

---

## [MEDIUM] Arbitrary storage reads via getStorageAt and getStorageSlotsAt expose all pool state

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/state/MixinStorageAccessible.sol:10-37`
- **Description:** The `getStorageAt(uint256 offset, uint256 length)` and `getStorageSlotsAt(uint256[] memory slots)` functions allow any external caller to read any storage slot of the proxy contract with no access control. While storage is technically readable on-chain by anyone with archive node access, these convenience functions make it trivial to efficiently read private mappings, internal state, and derived storage slots in a single call. The `getStorageAt` function is particularly concerning because it accepts an arbitrary `offset` and `length`, allowing sequential reads of large storage ranges. For the `length` parameter, there is no upper bound check, meaning a caller could request reading thousands of storage slots in a single call.
- **Impact:** (1) All "private" state is trivially readable by anyone, including user account balances, activation timestamps, operator mappings, and pool parameters. This is by design for the Gnosis Safe-style storage inspection pattern, but when combined with the single-EOA owner architecture, it means a frontrunner can read exact pool state and time their transactions for maximum extraction. (2) The unbounded `length` parameter in `getStorageAt` could be used to create gas-intensive view calls that may cause issues for RPC providers or off-chain consumers. (3) While this is a known pattern from Gnosis Safe, the exposure of activation timestamps and user balances in mappings enables precise griefing attacks where an attacker knows exactly when a user's lockup expires.
- **Recommendation:** Consider adding an upper bound on the `length` parameter in `getStorageAt` to prevent excessive gas consumption. Document the intentional exposure of all storage for integrator awareness.

---

## [MEDIUM] Fee collector activation timestamp is reset on every mint, potentially trapping fee tokens

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:199-203`
- **Description:** In `_allocateMintTokens` (line 201), the fee collector's `activation` timestamp is set to `uint48(block.timestamp) + _getMinPeriod()` on every single mint. This means that every time any user mints pool tokens, the fee collector's lockup period is reset. With the minimum lockup being 1 day and maximum 30 days, if mints happen frequently (e.g. daily), the fee collector can never burn their accumulated fee tokens because their activation timestamp is perpetually pushed forward. The same pattern occurs in `_allocateBurnTokens` (line 284), where the fee collector's activation is set to `block.timestamp + 1`, which is less problematic as it only locks for 1 second.
- **Impact:** If the pool has regular minting activity (at least once per min-period), the fee collector's accumulated pool tokens become effectively permanently locked. The fee collector keeps accumulating tokens (increasing their balance), but can never burn them to redeem value, as each new mint resets their lockup. This is a griefing vulnerability: any user can prevent the fee collector from ever redeeming fees by minting the minimum amount at intervals shorter than the min-period. With a min period of 30 days (the default), even monthly mints would perpetually reset the lock.
- **Recommendation:** Track the fee collector's activation separately, or only update the activation timestamp if it is currently in the past (i.e., only extend the lock if tokens were previously unlocked). Alternatively, use a different mechanism for fee token lockup that does not reset on every allocation.

---

## [MEDIUM] Owner-controlled delegatecall routing in fallback enables arbitrary state modification

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/sys/MixinFallback.sol:28-69`
- **Description:** The `fallback()` function implements a two-tier routing system. For selectors found in `_extensionsMap`, it uses the extension's `shouldDelegatecall` flag. For selectors resolved via `IAuthority(authority).getApplicationAdapter(msg.sig)`, the `shouldDelegatecall` flag is set to `true` if and only if `msg.sender == pool().owner` (line 47). This means the pool owner can execute arbitrary delegatecalls to any contract that the Authority has registered as an adapter for any selector. Since delegatecalls execute the target contract's code in the context of the pool (with full access to the pool's storage and balance), a malicious or compromised adapter can modify any storage slot, drain all funds, or brick the pool.

    The trust model depends entirely on the Authority governance correctly vetting adapters before whitelisting them. The owner cannot directly choose which adapter to call (that is determined by the Authority), but the owner triggers the delegatecall and is the only address that gets delegatecall routing for adapter selectors.
- **Impact:** If the Authority governance is compromised or makes an error in whitelisting an adapter, the pool owner can trigger delegatecalls to that adapter, executing arbitrary code in the pool's storage context. This could result in complete drain of all pool assets, modification of NAV/unitary values, or permanent bricking of the pool. The attack requires Authority governance compromise (or negligence), but the consequence is total loss of funds for all pool holders. The Authority at `0xe35129A1E0BdB913CF6Fd8332E9d3533b5F41472` is itself an external dependency whose security is outside the scope of this implementation.
- **Recommendation:** Consider adding a whitelist or allowlist at the pool level for which adapters the owner can delegatecall to, rather than trusting the Authority's global adapter registry. Alternatively, implement storage guards or sentinel checks after delegatecalls to critical adapters to detect unexpected state modifications.

---

## [MEDIUM] Burn function allows redemption in non-base tokens only when base token balance is insufficient, enabling oracle manipulation-based extraction

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/core/actions/MixinActions.sol:234-247`
- **Description:** The `_burn` function's `burnForToken` path (lines 234-247) allows a user to redeem pool tokens for a non-base token, but only when the pool's base token balance is less than the computed `netRevenue`. The check on line 241 is `require(netRevenue > baseTokenBalance, BaseTokenBalance())`, meaning the user can only get a non-base token if the pool genuinely does not hold enough base token. The conversion from base token value to `tokenOut` amount uses `IEOracle(address(this)).convertTokenAmount(baseToken, netRevenue.toInt256(), tokenOut)`, which relies on the oracle extension. If the oracle uses on-chain TWAP prices from Uniswap V4, these can be manipulated (albeit at cost) within the TWAP window. An attacker who can manipulate the oracle price between `_updateNav()` (which determines NAV and thus `netRevenue` in base token terms) and the `convertTokenAmount` call (which converts that base-denominated revenue to `tokenOut` amounts) could extract more value than they should.
- **Impact:** An attacker who can manipulate oracle prices could burn pool tokens and receive more of the non-base token than the pool tokens are worth, effectively extracting value from other pool holders. The attack is constrained by: (1) requiring the pool to not hold enough base token (requiring the owner to have deployed funds elsewhere), (2) requiring oracle price manipulation which has cost and may be constrained by TWAP parameters, (3) the spread acting as a friction. The severity is MEDIUM because the base-token-insufficiency requirement significantly limits the attack surface.
- **Recommendation:** Consider adding a tolerance check or a maximum slippage parameter for the oracle conversion in the burn path. The existing `amountOutMin` parameter partially mitigates this (the user themselves set it), but it does not protect other pool holders from an attacker who is willing to accept a "fair" `amountOutMin` that is still extractive due to oracle manipulation.

---

## [MEDIUM] Virtual supply manipulation through cross-chain operations can brick pool NAV computation

- **Severity:** MEDIUM
- **File:** `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/libraries/VirtualStorageLib.sol:24-26` and `/Users/moo/code/trade-executor/deps/web3-ethereum-defi/.claude/projects/rigoblock/src/implementation/src/protocol/libraries/NavImpactLib.sol:71-77`
- **Description:** The `VirtualStorageLib.updateVirtualSupply(int256 delta)` function directly adds the delta to the stored virtual supply without any bounds checking. The virtual supply is used in NAV calculation in `MixinPoolValue._updateNav()` (line 60: `int256 effectiveSupply = int256(components.totalSupply) + virtualSupply`). The `NavImpactLib.validateSupply()` function enforces that when virtual supply is negative, the effective supply must be at least `totalSupply / 8` (12.5%). However, there is no corresponding upper bound check when virtual supply is positive. A large positive virtual supply would inflate the `effectiveSupply`, causing `unitaryValue` to decrease (line 72: `components.unitaryValue = (components.netTotalValue * 10 ** components.decimals) / components.totalSupply`), which would cause minters to receive more pool tokens per unit of base token than they should, diluting existing holders.

    The `updateVirtualSupply` function is called from the crosschain extension (via delegatecall), which is controlled by the extension/adapter system. If the crosschain extension has a bug or is compromised, unbounded positive virtual supply inflation would directly devalue all existing pool tokens.
- **Impact:** Unbounded positive virtual supply inflation deflates the unitary value, meaning new minters get pool tokens at a discount while existing holders' tokens lose value. This is a direct dilution attack vector. The attack requires access to the crosschain extension's delegatecall path, which is governed by the extension system. The negative direction is bounded (minimum 12.5% effective supply), but the positive direction has no bound.
- **Recommendation:** Add an upper bound check on virtual supply, mirroring the lower bound check. For example, ensure that positive virtual supply does not exceed `totalSupply * (MINIMUM_SUPPLY_RATIO - 1)` or a similar configurable maximum. This would prevent extreme dilution through virtual supply inflation.
