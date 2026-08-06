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

Vault-protocol additions must follow
[the vault support contract](../../eth_defi/erc_4626/README-vault-protocol-support.md):
exercise manager-generated calls through ``SimpleVaultV0`` and ``GuardV0``,
parse protocol events and token payouts, and keep mock-only settlement or
liquidity overrides distinct from live-fork solvency evidence. The focused
``test_guard_async_mock_settlement.py`` suite covers the operator/mock boundary
and the YieldNest ``ignore_liquidity`` fixture.

See the [guard README](../../contracts/guard/README.md) for the full test module listing.
