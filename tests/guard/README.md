Integration tests for [GuardV0](../../contracts/guard/) and
[TradingStrategyModuleV0](../../contracts/safe-integration/) smart contracts.

Tests validate that the guard correctly blocks unauthorised trades and allows legitimate ones across:

- Uniswap V2 and V3
- Aave V3
- ERC-4626 and ERC-7540 vaults
- CowSwap presigned orders
- GMX V2 perpetuals
- Hypercore native vaults
- 1delta leveraged trading

See the [guard README](../../contracts/guard/README.md) for the full test module listing.
