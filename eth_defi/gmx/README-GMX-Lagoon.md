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

## Execution fee (keeper fee) funding

GMX orders require native ETH execution fees paid to keepers via `sendWnt()`. There are two ways to fund these fees:

### Safe-funded (default)

The Safe holds ETH and pays execution fees from its own balance. The asset manager calls `performCall(target, data, value)` where `value` tells the Safe how much ETH to send, but the asset manager's transaction itself carries no ETH (`msg.value == 0`).

```
Asset Manager ──(gas only)──▶ TradingStrategyModule.performCall(target, data, value)
                                        │
                                        ▼
                                Gnosis Safe ──(value ETH)──▶ GMX ExchangeRouter
```

Simple, but requires someone to top up the Safe with ETH periodically.

### Asset-manager-funded (opt-in, v0.4+)

The asset manager sends ETH with the `performCall` transaction. The module forwards `msg.value` to the Safe via its `receive()` fallback, then the Safe sends `value` ETH to the target as usual. No manual Safe top-up needed.

```
Asset Manager ──(gas + ETH)──▶ TradingStrategyModule.performCall{value: fee}(target, data, value)
                                        │ forwards msg.value to Safe
                                        ▼
                                Gnosis Safe ──(value ETH)──▶ GMX ExchangeRouter
```

Enable this in Python with `forward_eth=True`:

```python
wallet = LagoonGMXTradingWallet(vault, asset_manager, forward_eth=True)
```

The guard does not validate ETH amounts — all targets are governance-approved contracts and ETH only flows to those trusted targets. If `msg.value > value`, the excess stays in the Safe.

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

## Testnet (Arbitrum Sepolia)

GMX V2 is deployed on Arbitrum Sepolia (chain ID 421614) with full keeper support, so the complete trading flow (open/close positions, keeper execution) works on testnet.

### Deployment differences

- **No Lagoon factory on Sepolia** — both Gnosis Safe and all Lagoon contracts are deployed from scratch (`from_the_scratch=True, use_forge=True`)
- **No Chainlink price feeds** — USD cost estimates are unavailable on testnet
- **Testnet + simulation is not supported** — Anvil fork mode only works with mainnet

### Getting test tokens

GMX testnet uses its own `MintableToken` contracts — anyone can call `mint(account, amount)` directly.

| Token | Address | Arbiscan |
|-------|---------|----------|
| USDC.SG | `0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773` | [Write Contract](https://sepolia.arbiscan.io/address/0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773#writeContract) |
| USDC | `0x3321Fd36aEaB0d5CdfD26f4A3A93E2D2aAcCB99f` | [Write Contract](https://sepolia.arbiscan.io/address/0x3321Fd36aEaB0d5CdfD26f4A3A93E2D2aAcCB99f#writeContract) |
| WETH | `0x980B62Da83eFf3D4576C647993b0c1D7faf17c73` | [Write Contract](https://sepolia.arbiscan.io/address/0x980B62Da83eFf3D4576C647993b0c1D7faf17c73#writeContract) |
| BTC | `0xF79cE1Cf38A09D572b021B4C5548b75A14082F12` | [Write Contract](https://sepolia.arbiscan.io/address/0xF79cE1Cf38A09D572b021B4C5548b75A14082F12#writeContract) |

To mint 999 USDC.SG: call `mint(your_address, 999000000)` (6 decimals).

For testnet ETH: use the [LearnWeb3 faucet](https://learnweb3.io/faucets/arbitrum_sepolia/).

### USDC vs USDC.SG gotcha

There are two USDC variants on Arbitrum Sepolia. GMX markets use **USDC.SG** (`0x3253a335...`) as their stablecoin — not the regular USDC (`0x3321Fd36...`). The vault denomination and collateral symbol must match what the market accepts:

- Vault underlying: USDC.SG
- GMX order collateral symbol: `USDC.SG`
- GMX order symbol: `ETH/USDC.SG:USDC.SG`

Using the wrong USDC variant causes `"Not a valid collateral for selected market!"` because the `OrderArgumentParser` validates the resolved token address against the market's `long_token_address` / `short_token_address`.

### Dynamic market resolution

`GMX_POPULAR_MARKETS` only contains mainnet addresses. On testnet, market addresses are fetched dynamically using `fetch_all_gmx_markets(web3)` and matching by `market_symbol == "ETH"`.

### GMX contract addresses

Testnet contract addresses are fetched via `get_contract_addresses("arbitrum_sepolia")` from the hardcoded registry in `eth_defi/gmx/contracts.py` (not fetched from the GMX API like mainnet).

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
| [TradingStrategyModuleV0.sol](../../contracts/safe-integration/src/TradingStrategyModuleV0.sol) | Safe module with performCall (payable in v0.4+) |
| [GuardV0Base.sol](../../contracts/guard/src/GuardV0Base.sol) | Guard contract with GMX validation |
| [IGmxV2.sol](../../contracts/guard/src/lib/IGmxV2.sol) | GMX struct definitions for `abi.decode()` |
| [base_order.py](order/base_order.py) | GMX order building with multicall patterns |
| [wallet.py](lagoon/wallet.py) | LagoonGMXTradingWallet adapter |
| [test_guard_gmx_validation.py](../../tests/guard/test_guard_gmx_validation.py) | Unit tests for Guard GMX validation |
| [test_gmx_lagoon_integration.py](../../tests/gmx/lagoon/test_gmx_lagoon_integration.py) | Integration tests on Arbitrum fork |
