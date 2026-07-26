# YieldNest ABI sources

`Vault.json` is the full verified implementation ABI of the YieldNest vault,
including the callable ERC-4626 interface, the `Deposit` and `Withdraw` events
used for receipt analysis, and the vault's custom errors — notably
`ExceededMaxRedeem(address,uint256,uint256)` `0xb8b8b59c` and
`ExceededMaxWithdraw(address,uint256,uint256)` `0xd929e443`, which the adapter
decodes to gate buffer-limited redemptions.

Fetched on 2026-07-25 from the Etherscan v2 verified implementation
`0xb46d7014c1a29b6a82d8ecde5ad29d5b09ac7a1b` (`ContractName=Vault`) behind the
transparent proxy ynRWAx
[0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8](https://etherscan.io/address/0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8).
This replaces the earlier hand-curated interface-plus-events file; the full ABI
is a superset (also verified on
[Blockscout](https://eth.blockscout.com/address/0xb46d7014c1a29b6a82d8ecde5ad29d5b09ac7a1b?tab=contract)).
A fixed-block Ethereum fork regression proves the interface, events and errors
decode for the tested historical route; it does not assert that a later proxy
upgrade is identical.

The verified implementation has a public ``BaseVault.deposit`` route gated
only by the global pause state. The adapter consequently reports a
permissionless depositor policy; pausing and max-deposit capacity remain
separate availability signals.
