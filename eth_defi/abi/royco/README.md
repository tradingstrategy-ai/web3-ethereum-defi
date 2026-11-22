# Royco contract interfaces

| Filename | Source | Fetched at (UTC) | Reason to fetch |
| --- | --- | --- | --- |
| [WrappedVault.json](WrappedVault.json) | Original ABI source unrecorded. See the [WrappedVault deployment and interface notes](../../erc_4626/vault_protocol/royco/README-Royco.md#onchain-wrappedvault-api). | Unrecorded | Identify the reward wrapper through `previewRateAfterDeposit`. |
| [RoycoSeniorTranche.json](RoycoSeniorTranche.json) | [Verified Ethereum senior tranche](https://etherscan.io/address/0x1ba515a409dd702105415cdaae439059aa0b402a#code) | Unrecorded | Runtime interface used to read both tranche types. |
| [RoycoJuniorTranche.json](RoycoJuniorTranche.json) | [Verified Ethereum junior tranche](https://etherscan.io/address/0x059bc7aa5000a26aae2601cfbf060653adf8fd91#code) | Unrecorded | Preserve the junior tranche interface for comparison. |

The stored tranche ABIs differ in the constructor parameter name (`_stParams` and `_jtParams`). Their runtime methods match.
