# Frankencoin Shares interfaces

Read `eth_defi/abi/README.md` for the shared ABI loader conventions.

- `FCS.json`: Ethereum contract
  [0xDb861830D9Ae2d1fCF99fA0cfd3973de382B0B5b](https://etherscan.io/address/0xDb861830D9Ae2d1fCF99fA0cfd3973de382B0B5b#code).
  Fetched from Etherscan v2 `getsourcecode` on 2026-09-30. The verified name
  is `FCS`, compiler is Solidity 0.8.24, and `Proxy=0`: there is no separate
  implementation address. The inherited accounting and governance interfaces
  are included. Verified `FCS.sol` and `FCSMintRedeem.sol` matched the official
  repository at commit `8b4c4ab67bb361b91d58c474b87f4608fc4c0566`.
- `Equity.json`: application-exported interface from
  [the official Equity ABI export](https://github.com/Frankencoin-ZCHF/Frankencoin/blob/8b4c4ab67bb361b91d58c474b87f4608fc4c0566/exports/abis/equity/Equity.ts),
  retrieved on 2026-09-30. It is bound to the non-proxy Ethereum FPS contract
  [0x1bA26788dfDe592fec8bcB0Eaff472a42BE341B2](https://etherscan.io/address/0x1bA26788dfDe592fec8bcB0Eaff472a42BE341B2#code),
  confirmed through FCS's immutable `FPS1()` pointer. Its redemption and
  valuation views are covered by the fixed-block integration test.

Load these with `get_deployed_contract(web3, "frankencoin/FCS.json", address)`
and the corresponding `Equity.json` path. The ZCHF ERC-20 surface uses the
shared token helpers; no custom ZCHF ABI is required by the adapter.
