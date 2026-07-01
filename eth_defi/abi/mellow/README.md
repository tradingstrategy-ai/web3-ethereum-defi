Mellow Core Vault ABI fragments.

These are minimal read-only ABI fragments used by the initial Mellow scanner
and adapter:

- `Factory.json` exposes the Core Vault `Created` event used for discovery.
- `Vault.json` exposes component and asset/queue accessors on the canonical
  Core Vault contract.
- `Oracle.json` exposes `getReport(asset)`, whose `priceD18` value is used for
  historical share-price reads.
- `FeeManager.json` exposes read-only fee configuration accessors. Mellow fees
  are configured in D6 precision and paid in vault shares.
- `ERC20.json` exposes token metadata and `totalSupply()` for tokenised
  ShareManager contracts.

Sources:

- https://docs.mellow.finance/core-vaults
- https://docs.mellow.finance/core-vaults/core-deployments
- https://github.com/mellow-finance/flexible-vaults/blob/main/src/managers/FeeManager.sol
- https://etherscan.io/address/0x014e6DA8F283C4aF65B2AA0f201438680A004452
- https://etherscan.io/address/0x4Ce1ac8F43E0E5BD7A346A98aF777bF8fbeA1981
