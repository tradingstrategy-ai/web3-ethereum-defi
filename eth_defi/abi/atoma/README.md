# Atoma contract interfaces

These interfaces were retrieved on 2026-10-28 from the verified implementation
contracts using Etherscan API v2 (`chainid=42161`, `module=contract`,
`action=getabi`). The ABI files contain the returned interface array, formatted
as JSON. Load them through `eth_defi.abi.get_deployed_contract()` at the proxy
address. `IAtomaVaultEvents.json` remains the separate discovery interface.

| Interface | Verified implementation | Tested block | SHA-256 of committed file |
| --- | --- | ---: | --- |
| `AtomaVault.json` | [0x9161be115867c347adfb4c88a80e759920de86dc](https://arbiscan.io/address/0x9161be115867c347adfb4c88a80e759920de86dc#code) | 519150162 | `5c0fa4a5d1838787ac1dc3d7363e5c1e2c5f2e78c086a6539c079f51fc08f209` |
| `AtomaVaultLegacy.json` | [0x9521b08303ae010e85e24fc15d5334a0e506641e](https://arbiscan.io/address/0x9521b08303ae010e85e24fc15d5334a0e506641e#code) | 487039644 | `ae9a916e219358ff5d3ecf40e18a8372ff3c8fc597452b00eaf2880b3d0a9e27` |

Both Arbitrum proxies use the corresponding implementation at these blocks:

- [Atoma Index, `0xcc56410e1a136af0eceb7241c6ae394f4d8b581c`](https://arbiscan.io/address/0xcc56410e1a136af0eceb7241c6ae394f4d8b581c#code).
- [Atoma RWA, `0x1c788e14d8e5b446e3f71b5142e2edabcab36da1`](https://arbiscan.io/address/0x1c788e14d8e5b446e3f71b5142e2edabcab36da1#code).

Execution is restricted to the current interface/version and these two
addresses. The older interface supports historical receipt interpretation and
canonical-fork readers, not execution certification. Resolve ERC-1967 storage
at the operation block; do not infer a version from a failed getter.

The verified source is `src/AtomaVault.sol` in each explorer's source bundle.
`WithdrawalClaimed.assets` is **net** USDC in both versions. The current version
retains the exit fee in `accruedWithdrawalFees`; the older version transfers it
to the operator during the claim. Current deposits lock only newly minted
shares, and current settled claims are subject to `shortfallIndexWad` haircuts.
`getEpoch()` returns `(totalShares, settlementNav, settled)`;
`settledUnclaimedAssets()` tracks reserved liabilities. No version permits
ordinary ERC-4626 redemption or user cancellation.
