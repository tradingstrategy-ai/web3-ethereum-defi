# VaultMorpho implementation ABI

`VaultMorpho.json` is the verified Ethereum implementation ABI at
[`0xf392a5b96d3248440bb4841b4f50f0862060c1a3`](https://etherscan.io/address/0xf392a5b96d3248440bb4841b4f50f0862060c1a3#code),
used by vault `0x21ed44c18c926c60092b1b2985e2c999421a5a69` at block 26402309.
The verified source and ABI were retrieved on 13 November 2026.

The implementation is named `VaultMorpho`, uses Morpho Blue markets and has an
additional `tvlCap` check in `deposit()`. Its owning protocol and upstream GitHub
repository have not been identified. It must not inherit canonical Morpho
attribution merely because it exposes `MORPHO()`.

See the [canonical integration guide](../../../docs/source/vaults/vault_morpho/index.rst)
for the deployment evidence, capability detection and metadata boundaries.
The ABI supports contract reads; it is not proof of official protocol provenance.

## Reviewed public implementations

Permission review: 15 November 2026. The proxy mapping below was read on Ethereum
at block **26402309**. All four implementation sources have SHA-256
`4707c3641938c3d30f3c753de36a5a0ffe973a40a52f35bfbb067cb36308bf4c`.
Their deposit/mint, ERC-20 transfer and withdraw/redeem methods have no account
allowlist. Owner/curator/allocator permissions govern administration, not public
entry and exit. Caps and available market liquidity still constrain transactions.
Identical source does not mean identical deployment state or bytecode: constructor
immutables and compiler metadata may differ.

| Ethereum proxy | Reviewed implementation and verified source |
| --- | --- |
| `0x21ed44c18c926c60092b1b2985e2c999421a5a69` | [`0xf392a5b96d3248440bb4841b4f50f0862060c1a3`](https://etherscan.io/address/0xf392a5b96d3248440bb4841b4f50f0862060c1a3#code) |
| `0x52a965c435cffbc3eebf9200550cc597f4e54029` | [`0xfa0d6e598d13e9220e5de48db4ed087c8f8847b3`](https://etherscan.io/address/0xfa0d6e598d13e9220e5de48db4ed087c8f8847b3#code) |
| `0x9376bed03f9653e3927880f0d9024f612227a70a` | [`0x816dcddcb7949bd8556058b47e2f638b163d658d`](https://etherscan.io/address/0x816dcddcb7949bd8556058b47e2f638b163d658d#code) |
| `0xd2f62c8e654dbcbc03abfc2f9bfcd7f84027a5d8` | [`0x13d8602fa8dc916704194ec25bf73bffd6b39fed`](https://etherscan.io/address/0x13d8602fa8dc916704194ec25bf73bffd6b39fed#code) |

`REVIEWED_PUBLIC_IMPLEMENTATIONS` is a support boundary for these reviewed
implementations, not a whitelist of approved depositors. `False` from
`is_whitelisted_deposit()` means no depositor whitelist is required. Another
implementation or chain produces unknown policy, never an inferred public or
permissioned classification. To add support, resolve the implementation, review
its verified source and account-related hooks, record that evidence, and extend
the fixed-block regression coverage.

The implementation address is read from the standard
[ERC-1967 logic-contract storage slot](https://eips.ethereum.org/EIPS/eip-1967#logic-contract-address):
`bytes32(uint256(keccak256("eip1967.proxy.implementation")) - 1)`.
This is a proxy-storage read, not handcrafted function calldata. ERC-1967 proxies
need not expose a callable implementation getter, and the stored VaultMorpho
implementation ABI does not expose one. `fetch_implementation()` therefore uses
`eth_getStorageAt` at the requested block, following the existing Atoma adapter
pattern. Ordinary VaultMorpho contract methods still use the stored ABI.
