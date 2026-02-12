# GMX Lagoon integration security analysis

Security architecture for GMX perpetuals trading through Lagoon vaults (Gnosis Safe), focusing on the Guard contract's validation of fund flows.

For usage, see [the tutorial](https://web3-ethereum-defi.readthedocs.io/tutorials/lagoon-gmx.html).

## Architecture

```
Asset Manager ──▶ TradingStrategyModule (Guard) ──▶ Gnosis Safe (Vault Funds)
                          │ validates
                          ▼
                  GMX ExchangeRouter (multicall)
```

## GMX multicall patterns

GMX V2 batches order operations via `ExchangeRouter.multicall()`. The Guard validates every inner call.

| Inner call | Purpose |
|------------|---------|
| `sendWnt(address,uint256)` | Send ETH for keeper execution fee |
| `sendTokens(address,address,uint256)` | Send ERC20 collateral to order vault |
| `createOrder(tuple)` | Create the GMX position order |

**Open long (native ETH)**: `[sendWnt(orderVault, collateral+fee), createOrder(...)]`

**Open short (ERC20)**: `[sendWnt(orderVault, fee), sendTokens(token, orderVault, amount), createOrder(...)]`

**Close position**: `[sendWnt(orderVault, fee), createOrder(...)]`

## Guard validation

The Guard ([GuardV0Base.sol](../../contracts/guard/src/GuardV0Base.sol)) iterates through every call in the multicall and validates each one. Unknown selectors revert the entire transaction.

### sendWnt / sendTokens

- Receiver must equal the configured OrderVault
- Token (for sendTokens) must be whitelisted

### createOrder

Decoded via `abi.decode()` with GMX struct definitions from [IGmxV2.sol](../../contracts/guard/src/lib/IGmxV2.sol):

```solidity
CreateOrderParams memory params = abi.decode(callData, (CreateOrderParams));

require(isAllowedReceiver(params.addresses.receiver));
require(isAllowedReceiver(params.addresses.cancellationReceiver));
require(isAllowedGMXMarket(params.addresses.market));
require(isAllowedAsset(params.addresses.initialCollateralToken));

for (uint256 i = 0; i < params.addresses.swapPath.length; i++) {
    require(isAllowedGMXMarket(params.addresses.swapPath[i]));
}
```

**Validated fields**: receiver, cancellationReceiver, market, collateral token, every swapPath market.

## Whitelist configuration

```solidity
guard.whitelistGMX(exchangeRouter, syntheticsRouter, orderVault, "GMX");
guard.allowReceiver(safeAddress, "Safe vault");
guard.whitelistGMXMarket(ethUsdMarket, "ETH/USD");
guard.whitelistToken(usdc, "USDC collateral");
```

All whitelist operations require `onlyGuardOwner`.

## Attack vectors (mitigated)

| Attack | Mitigation |
|--------|------------|
| Set `receiver` to attacker address | `isAllowedReceiver()` rejects non-whitelisted |
| Set `cancellationReceiver` to attacker | `isAllowedReceiver()` check |
| `sendWnt(attacker, amount)` in multicall | Receiver must equal orderVault |
| `sendTokens(token, attacker, amount)` | Receiver must equal orderVault |
| Inject unknown function in multicall | Unknown selectors revert |
| Short calldata to read garbage | `calls[i].length >= 4` check |
| Batch valid + invalid orders | Loop validates ALL calls; any failure reverts |
| `anyAsset` to bypass receiver checks | `anyAsset` only bypasses asset/market checks, not receiver checks |
| Non-whitelisted market in swapPath | Each swapPath entry checked via `isAllowedGMXMarket()` |

## Recommendations

1. Only whitelist the Safe address as a receiver
2. Use explicit market whitelisting rather than `anyAsset=true` — covers both primary market and swapPath
3. Monitor GMX contract upgrades that might change the `CreateOrderParams` struct layout (structs in [IGmxV2.sol](../../contracts/guard/src/lib/IGmxV2.sol) must match)
4. Test on fork before mainnet

## Test coverage

| Validation | Unit test | Integration test |
|------------|-----------|------------------|
| sendWnt receiver = orderVault | - | via real GMX |
| sendTokens receiver = orderVault | - | via real GMX |
| sendTokens token whitelisted | `test_gmx_market_whitelisted` | via real GMX |
| createOrder receiver whitelisted | `test_receiver_whitelisted` | via real GMX |
| createOrder cancellationReceiver | `test_receiver_whitelisted` | via real GMX |
| createOrder market whitelisted | `test_gmx_market_whitelisted` | via real GMX |
| createOrder collateral whitelisted | `test_any_asset_allows_non_whitelisted_asset` | via real GMX |
| createOrder swapPath whitelisted | `test_security_attack_scenario_non_whitelisted_swap_path` | via real GMX |
| swapPath with anyAsset | `test_any_asset_allows_non_whitelisted_swap_path_market` | - |

## Files

| File | Description |
|------|-------------|
| [GuardV0Base.sol](../../contracts/guard/src/GuardV0Base.sol) | Guard contract with GMX validation |
| [IGmxV2.sol](../../contracts/guard/src/lib/IGmxV2.sol) | GMX struct definitions for `abi.decode()` |
| [base_order.py](order/base_order.py) | GMX order building with multicall patterns |
| [wallet.py](lagoon/wallet.py) | LagoonGMXTradingWallet adapter |
| [test_guard_gmx_validation.py](../../tests/guard/test_guard_gmx_validation.py) | Unit tests for Guard GMX validation |
| [test_gmx_lagoon_integration.py](../../tests/gmx/lagoon/test_gmx_lagoon_integration.py) | Integration tests on Arbitrum fork |
