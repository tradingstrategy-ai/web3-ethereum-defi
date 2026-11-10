# Wildcat interfaces

ABIs for the read-only Wildcat version 2 market adapter in
`eth_defi/erc_4626/vault_protocol/wildcat/`, fetched on 2026-11-10.

- `WildcatMarket.json`: the version 2 market, from the Ethereum deployment
  record `deployments/mainnet/WildcatMarket-0xac3216fa28f81b8fae150fb5626ca79c7a570daf/output.json`
  of [`wildcat-finance/v2-protocol`](https://github.com/wildcat-finance/v2-protocol)
  at commit `f5a2614`. Markets are deployed with CREATE2 from init code stored
  at that address and are not individually verified on Etherscan or Sourcify.
- `OpenTermHooks.json`: the open-term access-control hooks template, from
  `deployments/mainnet/OpenTermHooks-0x4c62b4844c8371f321541e8d564a4b3896cecec7/output.json`
  at the same commit.
- `FixedTermHooks.json`: the fixed-term access-control hooks template, from
  `deployments/mainnet/FixedTermHooks-0x7e49caba6fb53cdc70cd98829731a2b8d76dfc36/output.json`
  at the same commit.
- `WildcatArchController.json`: the market registry
  [`0xfEB516d9D946dD487A9346F6fee11f40C6945eE4`](https://etherscan.io/address/0xfEB516d9D946dD487A9346F6fee11f40C6945eE4),
  a full match on [Sourcify](https://sourcify.dev/server/v2/contract/1/0xfEB516d9D946dD487A9346F6fee11f40C6945eE4?fields=abi).

The `HookedMarket` structs returned by `getHookedMarket()` differ between the
two hooks templates, so the adapter selects the ABI from the hooks instance's
`version()`. The fixed-block fork tests in
`tests/erc_4626/vault_protocol/test_wildcat.py` decode every value the
adapter reads.
