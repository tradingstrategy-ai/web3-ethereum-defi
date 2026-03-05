# EulerEarn deployment information

- **Contract**: `Arb Capital USDT Core` (`arbcapUSDTcore`)
- **Chain**: Ethereum mainnet (chain ID 1)
- **Underlying asset**: USDT (`0xdAC17F958D2ee523a2206206994597C13D831ec7`)
- **Decimals**: 6
- **Fee**: 5% (50000000000000000 / 1e18)
- **Timelock**: 0 seconds
- **Compiler**: Solidity v0.8.26, optimisation enabled (200 runs), EVM target: cancun
- **Verified on Blockscout**: 2025-12-15

## Contract information

| Name | Address | Source code | ABI |
|------|---------|-------------|-----|
| EulerEarn (vault) | `0x5c1EB5003bA931cCa7CA0fbf7901eF94037064bc` | `src/EulerEarn.sol` | `abi/EulerEarn.json` |
| EulerEarnFactory | `0x139d2f2d38c68f460d998746c7d422c833fb830d` | [Blockscout](https://eth.blockscout.com/address/0x139d2f2d38c68f460d998746c7d422c833fb830d) | N/A |
| EthereumVaultConnector (EVC) | `0x0C9a3dd6b8F28529d72d7f9cE918D493519EE383` | [Blockscout](https://eth.blockscout.com/address/0x0C9a3dd6b8F28529d72d7f9cE918D493519EE383) | N/A |
| Permit2 | `0x000000000022D473030F116dDEE9F6B43aC78BA3` | [Uniswap Permit2](https://github.com/Uniswap/permit2) | N/A |
| USDT (underlying asset) | `0xdAC17F958D2ee523a2206206994597C13D831ec7` | [Blockscout](https://eth.blockscout.com/address/0xdAC17F958D2ee523a2206206994597C13D831ec7) | N/A |

## Privileged addresses

| Role | Address | Variable | Type | Notes |
|------|---------|----------|------|-------|
| Owner | `0xAbcA0e0792C4CA2C25a8055e974BF31224a399d5` | `owner()` | EOA | Also serves as curator; single owner of the guardian Safe |
| Curator | `0xAbcA0e0792C4CA2C25a8055e974BF31224a399d5` | `curator()` | EOA | Same address as owner |
| Guardian | `0x87B5b4db1132aDdA8E793d4a4cfe5cA8D41A4a2c` | `guardian()` | Safe multisig (1-of-1, v1.4.1) | Single signer is the owner EOA (`0xAbcA0e...`) |
| Fee recipient | `0x87B5b4db1132aDdA8E793d4a4cfe5cA8D41A4a2c` | `feeRecipient()` | Safe multisig (1-of-1, v1.4.1) | Same Safe as guardian |
| Pending owner | `0x0000000000000000000000000000000000000000` | `pendingOwner()` | N/A | No pending ownership transfer |

## Security observations

- The **owner**, **curator**, **guardian**, and **fee recipient** are all controlled by a single EOA (`0xAbcA0e...`). The guardian and fee recipient go through a 1-of-1 Safe multisig, but this Safe has the same EOA as its sole signer, providing no additional security benefit.
- The **timelock** is set to 0 seconds, meaning governance actions take effect immediately with no delay for users to react.
- There is **no pending ownership transfer** in progress.
- The **fee** is 5% of yield.
