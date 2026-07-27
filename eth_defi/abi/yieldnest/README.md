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
A fixed-block Ethereum fork regression at block 25,598,869 proves the deposit
interface, events and errors decode for the tested historical route; it does
not assert that a later proxy upgrade is identical. After a 10 USDC deposit at
that block, ynRWAx minted 9,209,998,609,480,980,927 raw shares, while
``buffer()`` was the zero address and ``maxRedeem(owner)`` was zero. The direct
``redeem(1, owner, owner)`` call reverted with
``ExceededMaxRedeem(owner, 1, 0)``. Consequently the adapter does not advertise
a redeem capability or invent a queued settlement path until a deterministic
non-zero-capacity redemption route and receipt are available.
