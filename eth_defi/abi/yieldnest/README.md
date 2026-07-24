# YieldNest ABI sources

`Vault.json` contains the callable interface used by the YieldNest adapter and
the standard ERC-4626 `Deposit` and `Withdraw` event definitions. The tested
adapter currently uses `Deposit` for synchronous receipt analysis; `Withdraw`
is retained for a future maturity-aware redemption manager.

The event signatures were taken on 2026-07-23 from the
[Blockscout-verified implementation](https://eth.blockscout.com/address/0xb46d7014c1a29b6a82d8ecde5ad29d5b09ac7a1b?tab=contract) of the ynRWAx proxy.
The fixed-block fork regression proves that the events decode for the tested
historical route; it does not assert that a later proxy upgrade is identical.
The proxy address is
[0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8](https://eth.blockscout.com/address/0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8?tab=contract).
