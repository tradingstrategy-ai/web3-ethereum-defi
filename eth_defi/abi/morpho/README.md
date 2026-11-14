# Morpho ABIs

| File | Contract | Source | Fetched |
|---|---|---|---|
| `IVaultV2.json` | Morpho Vault V2 interface | Existing interface used by `eth_defi.erc_4626.vault_protocol.morpho.vault_v2` | — |
| `MetaMorpho.json` | MetaMorpho V1.1 vault (`MetaMorphoV1_1`), not a proxy | Verified source of [King RSS USDC Vault on Base](https://basescan.org/address/0xf80c0529bd94c773844e459853cd91b9263dd525#code) (`0xf80c0529bd94c773844e459853cd91b9263dd525`), compiler v0.8.26, via the Etherscan v2 API | 2026-09-26 |
| `MorphoBlue.json` | Morpho Blue singleton (`Morpho`), not a proxy | Verified source of [Morpho Blue on Base](https://basescan.org/address/0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb#code) (`0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb` on Base and Ethereum; other chains have their own deployment, which a MetaMorpho vault returns from `MORPHO()`), compiler v0.8.19, via the Etherscan v2 API | 2026-09-26 |

`MetaMorpho.json` and `MorphoBlue.json` are used by
`eth_defi.vault_report.vault_probes` to read a MetaMorpho vault's withdraw
queue, its supply positions and each market's collateral, oracle and
liquidity. See the [Morpho documentation](https://docs.morpho.org/).

`VaultMorpho.json` is the verified Ethereum implementation ABI at
[`0xf392a5b96d3248440bb4841b4f50f0862060c1a3`](https://etherscan.io/address/0xf392a5b96d3248440bb4841b4f50f0862060c1a3#code),
used by `0x21ed44c18c926c60092b1b2985e2c999421a5a69`. Source/ABI were
retrieved on 13 November 2026 and imported on 14 November 2026. Its `tvlCap`
is an additional global restriction, separate from canonical MetaMorpho limits.
The canonical [Morpho integration guide](../../../docs/source/vaults/morpho/index.rst#vault-features-and-adapter-selection)
explains the two feature flags, detection predicates, adapter precedence, cap
semantics and preserved Morpho protocol identity. Detection adds one `tvlCap`
probe to the existing multicall on each chain.

`MorphoMarketV1AdapterV2.json` comes from verified Ethereum adapter
[`0x4336bcdbd4e099f76ee47e8e26fd9e8513ceeff5`](https://etherscan.io/address/0x4336bcdbd4e099f76ee47e8e26fd9e8513ceeff5#code),
retrieved on 14 November 2026. The same verified contract name/interface was
checked at `0x0854c79ec9600fd1d02caa14ef0527f93bb5e4cc`.
`IVaultV2.json` additionally imports `liquidityData()` from verified Ethereum
VaultV2 `0x9480034d908989b006d78bdbbd7bd509c92e8bbc` (source retrieved
13 November, interface extended 14 November 2026).
