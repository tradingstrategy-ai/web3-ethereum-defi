# Asseto ABI sources

Existing AoABT interfaces support Asseto's permissioned fund products.

`RoyaltyVault.json` is the bare implementation ABI fetched on 2026-10-27 from
the [Soneium Blockscout API](https://soneium.blockscout.com/api/v2/smart-contracts/0x8CfDe91D3a4c55b426732f17dF1745E6d0603e73).
It is used only for the following IP Vault deployment, which is still under verification:

- Chain: Soneium (1868).
- Proxy: `0x2Ba3FC5BDbBE7BE2630fB71e759739625246bCd9`.
- Implementation: `0x8CfDe91D3a4c55b426732f17dF1745E6d0603e73`.
- Source file: `project/contracts/RoyaltyVault.sol`, Solidity `^0.8.28`,
  compiled with `0.8.36+commit.8a079791` and OpenZeppelin 5.6.1.
- Source verification: 2026-10-24 18:48:14 UTC.
- Canonical identity: [official deployment documentation](https://ipusd.gitbook.io/ipusd-docs/technical-reference/contracts-roles-and-disclosures).

No matching public development repository was found. The valuation methods
are compatible with the shared reader, but the standard deposit and redemption
methods always revert. The library does not certify transaction support.
