# Plutus ABI sources

`HedgeVaultV2.json` is the verified implementation ABI of the Plutus Hedge
vault, which has been upgraded to an ERC-7540-style asynchronous redemption
contract (`requestRedeem` / `fulfillRedeem` / `pendingRedeemRequest` /
`claimableRedeemRequest` / `cancelRedeemRequest`, events `RedeemRequested` /
`RedeemFulfilled` / `RedeemCancelled`, and typed errors including
`UseRequestRedeem()` `0x797f246a`, `WithdrawalsArePaused()` `0xe14e66da`,
`DepositsArePaused()` `0x5a65d188`, `RequestNotClaimable()` `0x7570897f`).

Fetched on 2026-07-25 from the Arbiscan (Etherscan v2) verified implementation
`0xf2b0b9cceaf4a58807168bbf99499c7093bb46d1` (`ContractName=HedgeVaultV2`)
behind the ERC-1967 proxy
[0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a](https://arbiscan.io/address/0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a).
A fixed-block Arbitrum fork regression proves the async lifecycle decodes for
the tested route; it does not assert that a later proxy upgrade is identical.
