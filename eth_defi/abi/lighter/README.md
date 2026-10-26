# Lighter ABI sources

`ZkLighter.json` is a hand-maintained fragment of the Lighter L1 custody
contract interface. It holds only the functions used by `eth_defi.lighter`
and the guard tests: `USDC_ASSET_INDEX`, `addressToAccountIndex`,
`changePubKey`, `deposit`, `withdraw`, `withdrawPendingBalance` and
`getPendingBalance`.

| Item | Value |
|------|-------|
| Chain | Ethereum mainnet (chain id 1) |
| Proxy | [`0x3b4d794a66304f130a4db8f2551b0070dfcf5ca7`](https://etherscan.io/address/0x3b4d794a66304f130a4db8f2551b0070dfcf5ca7) |
| Implementation (`getTarget()` on 2026-10-26, block 26278601) | [`0xe16c893252616dd49913969f145e733b96a3e5a7`](https://etherscan.io/address/0xe16c893252616dd49913969f145e733b96a3e5a7) |
| Earlier implementation | [`0x831ef69bab8af8b1037a4961b8d0674b124e7008`](https://etherscan.io/address/0x831ef69bab8af8b1037a4961b8d0674b124e7008) |

`getPendingBalance(address,uint16) returns (uint128)` (selector `0xd1cbc64f`)
was added on 2026-10-26. Its selector is present in the bytecode of both
implementations above, and a call through the proxy on mainnet decodes with
this fragment.
