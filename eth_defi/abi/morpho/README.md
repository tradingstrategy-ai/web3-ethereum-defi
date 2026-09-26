# Morpho ABIs

| File | Contract | Source | Fetched |
|---|---|---|---|
| `IVaultV2.json` | Morpho Vault V2 interface | Existing interface used by `eth_defi.erc_4626.vault_protocol.morpho.vault_v2` | — |
| `MetaMorpho.json` | MetaMorpho V1.1 vault (`MetaMorphoV1_1`), not a proxy | Verified source of [King RSS USDC Vault on Base](https://basescan.org/address/0xf80c0529bd94c773844e459853cd91b9263dd525#code) (`0xf80c0529bd94c773844e459853cd91b9263dd525`), compiler v0.8.26, via the Etherscan v2 API | 2026-09-26 |
| `MorphoBlue.json` | Morpho Blue singleton (`Morpho`), not a proxy | Verified source of [Morpho Blue on Base](https://basescan.org/address/0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb#code) (`0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb`, the same address on every chain), compiler v0.8.19, via the Etherscan v2 API | 2026-09-26 |

`MetaMorpho.json` and `MorphoBlue.json` are used by
`eth_defi.vault_report.vault_probes` to read a MetaMorpho vault's withdraw
queue, its supply positions and each market's collateral, oracle and
liquidity. See the [Morpho documentation](https://docs.morpho.org/).
