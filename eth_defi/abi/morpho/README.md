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

The separately maintained [VaultMorpho ABI](../vault_morpho/README.md) belongs
to an unattributed implementation, not canonical MetaMorpho. Its contract
compatibility must not be used as evidence of Morpho protocol ownership.

`MorphoMarketV1AdapterV2.json` comes from verified Ethereum adapter
[`0x4336bcdbd4e099f76ee47e8e26fd9e8513ceeff5`](https://etherscan.io/address/0x4336bcdbd4e099f76ee47e8e26fd9e8513ceeff5#code),
retrieved on 14 November 2026. The same verified contract name/interface was
checked at `0x0854c79ec9600fd1d02caa14ef0527f93bb5e4cc`.
`IVaultV2.json` additionally imports `liquidityData()` from verified Ethereum
VaultV2 `0x9480034d908989b006d78bdbbd7bd509c92e8bbc` (source retrieved
13 November, interface extended 14 November 2026).


Permission-reader ABIs
----------------------

`VaultV2.json` is the full verified ABI of the non-proxy Ethereum VaultV2
[`0xd827f29506a10a64c5cc75993d8ef58daaf7746a`](https://etherscan.io/address/0xd827f29506a10a64c5cc75993d8ef58daaf7746a#code).
Its source/ABI was retrieved on 13 November 2026 and added here on 15 November.
The reviewed Base VaultV2 `0x036a01efddc87f6634ffde0533ee528b90fc7a45` exposes
the same four gate getters. The full contract ABI includes getters omitted by
`IVaultV2.json`; gate policy reads use ABI-bound functions.

`MorphoVaultV1Adapter.json` is the verified ABI of non-proxy Base adapter
[`0x8B6E43CCE1961D3671a39Fe8D9E711E69ddD74ce`](https://basescan.org/address/0x8B6E43CCE1961D3671a39Fe8D9E711E69ddD74ce#code),
retrieved on 15 November 2026. It supplies `morphoVaultV1()` and `parentVault()`.
The other reviewed Base adapter `0x5b211DA4Cd92cfb9CCCFbd1De78289955EB236CD`
has the same contract name and interface. No proxy implementation address is
needed for these directly deployed contracts. Both files are bare verified ABI
arrays, loaded with `get_deployed_contract()`; selectors and return decoding
are generated through the ABI helpers rather than handwritten call payloads.
