# iBENJI contract research

## Identification

| Field | Finding |
| --- | --- |
| Fund | Franklin OnChain Institutional Liquidity Fund Ltd. |
| Token | iBENJI |
| Chain | Ethereum mainnet (chain ID 1) |
| Token / proxy | [`0x90276e9d4a023b5229e0c2e9d4b2a83fe3a2b48c`](https://etherscan.io/token/0x90276e9d4a023b5229e0c2e9d4b2a83fe3a2b48c) |
| Proxy contract | `ERC1967Proxy` (OpenZeppelin) |
| Current implementation | [`MoneyMarketFund_V6`](https://etherscan.io/address/0xa74372DFde0dF8a08a3Ac7b60c5379F90AC9C7DD#code) at `0xa74372DFde0dF8a08a3Ac7b60c5379F90AC9C7DD` |
| Verification | Both proxy and current implementation are exact matches in [Sourcify](https://sourcify.dev/server/v2/contract/1/0x90276e9d4a023b5229e0c2e9d4b2a83fe3a2b48c?fields=compilation,proxyResolution) / [implementation source and ABI](https://sourcify.dev/server/v2/contract/1/0xa74372DFde0dF8a08a3Ac7b60c5379F90AC9C7DD?fields=abi,compilation,sources). |

Franklin Templeton's [Benji contracts page](https://digitalassets.franklintempleton.com/benji/benji-contracts/) lists this exact Ethereum address as the iBENJI fund token. Etherscan labels it *Franklin OnChain Institutional Liquidity Fund Ltd. (iBENJI)* and identifies the same implementation. This is strong attribution evidence, rather than an inference from the ticker.

## Contract architecture and functions

The public token address is an OpenZeppelin `ERC1967Proxy`. Its current logic is the verified, UUPS-upgradeable `MoneyMarketFund_V6` contract, compiled with Solidity 0.8.18. The implementation inherits `ERC20Upgradeable`, `AccessControlUpgradeable`, and `UUPSUpgradeable`; it also talks to a separate `ModuleRegistry` and compliance modules. The source is published under **Business Source License 1.1**, rather than a conventional open-source licence.

Franklin lists these iBENJI companion contracts, which align with the verified implementation's imports and access checks:

| Component | Address | Purpose / verified name where checked |
| --- | --- | --- |
| Registry module | [`0xf70e…34F7`](https://etherscan.io/address/0xf70e2726C60644aD6EFe87289c2dF830f39D34F7#code) | `ModuleRegistry`; resolves modules by identifier. |
| Token registry | [`0x950f…23Ba`](https://etherscan.io/address/0x950fAE11DDdb4A10368cc4E4Fd93386A587e23Ba#code) | `TokenRegistry`. |
| Authorisation module | [`0x12aB…4066`](https://etherscan.io/address/0x12aBfF8Dca2d09D99019dFCC9bf07539a8264066#code) | EIP-1967 proxy resolving to `AuthorizationModule_V2`. |
| Transactional module | [`0x1933…0b76`](https://etherscan.io/address/0x1933797BBf8F901b69bb81245D5A82091a0e0b76#code) | EIP-1967 proxy; issuer designates it for transaction management. |
| Transfer-agent module | [`0xaB26…a0a5`](https://etherscan.io/address/0xaB266e4fa5D088cC440433C3EA1e066fD710a0a5#code) | EIP-1967 proxy resolving to `TransferAgentModule_V5`. |
| Intent-validation module | [`0x9B61…86d0`](https://etherscan.io/address/0x9B61815c4388C7e0a9EF32B5B2B8926C379786d0#code) | EIP-1967 proxy resolving to `IntentValidationModule`. |

Important functions of `MoneyMarketFund_V6` are:

| Function group | Functions | Meaning |
| --- | --- | --- |
| ERC-20 | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `approve`, `transfer`, `transferFrom` | Standard token interface, with compliance restrictions applied to transfers. |
| Issue and redeem | `mintShares`, `burnShares`, `instantCXTransferIn`, `instantCXTransferOut` | A privileged operator can mint/burn fund shares; the two `instantCX` functions mint or burn for a shareholder. |
| Investor controls | `enable/disableERC20Transfer`, `enable/disableERC20ThirdPartyTransfer`, `enable/disableInstantTransfer` | Privileged operator can turn transfer paths on and off. |
| Controlled movement | `instantTransfer`, `transferShares`, `adminApprove` | Role/module-controlled transfer without holder allowance, inter-module share movement, and administrator-set allowance. |
| Compliance and reporting | `getAccountsBalances`, `hasHoldings`, `hasEnoughHoldings`, `lastKnownPrice`, `updateLastKnownPrice` | Paginated authorised-holder/balance view and a privileged price update. |
| Administration | `grantRole`, `revokeRole`, `upgradeTo`, `upgradeToAndCall` | Role management and UUPS upgrades; upgrades are restricted by `ROLE_TOKEN_OWNER`. |

Transfer policy is explicit in the verified source: mints require an authorised recipient; ordinary transfers require authorised, non-frozen sender and recipient accounts and enabled transfer switches. This is an allowlist-based regulated-security token, not a permissionless money-market vault. `instantTransfer` lets an authorised administrator move shares between authorised, non-frozen shareholders without holder approval.

## Protocol conclusion

**Conclusion: Franklin Templeton's proprietary Benji Technology Platform — high confidence.** The fund/token attribution is confirmed independently by the issuer's address registry and Etherscan. The on-chain source names its internal `FT` modules and `MoneyMarketFund_V6`, while the issuer describes Benji as its proprietary blockchain-integrated recordkeeping and transfer-agency infrastructure. The implementation uses standard OpenZeppelin upgradeability and ERC-20 components, but it is not an instance of a separately identifiable public tokenisation protocol such as ERC-3643 or CMTAT.

The only public GitHub code match identified is the upstream OpenZeppelin dependency family; searches for `MoneyMarketFund_V6`, the `contracts/FT/infrastructure/modules` source path, and the distinctive `instantCXTransferIn` function did not locate an issuer-maintained public repository. Therefore, do not infer that an unaffiliated GitHub implementation is the deployed Franklin code; use the verified on-chain source above as the canonical source for this deployment.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned
**121,259,870.905245330937566509 iBENJI** (18 decimals). The ABI exposes
`lastKnownPrice()` and privileged `updateLastKnownPrice()`, so an
administrator-maintained reference price is available from the token contract;
verify its scale and freshness against the issuer's NAV before valuation.

## Links

| Resource | Link |
| --- | --- |
| Fund-token explorer | [Etherscan token / proxy](https://etherscan.io/token/0x90276e9d4a023b5229e0c2e9d4b2a83fe3a2b48c) |
| Implementation explorer/source | [Etherscan implementation](https://etherscan.io/address/0xa74372DFde0dF8a08a3Ac7b60c5379F90AC9C7DD#code) |
| Verified source and ABI | [Sourcify implementation record](https://sourcify.dev/server/v2/contract/1/0xa74372DFde0dF8a08a3Ac7b60c5379F90AC9C7DD?fields=abi,compilation,sources) |
| Issuer contract registry / docs | [Franklin Templeton Benji DevHub](https://digitalassets.franklintempleton.com/benji/benji-contracts/) |
| Issuer platform description | [Franklin Templeton digital-assets technology](https://www.franklintempleton.com/about-us/our-teams/specialist-investment-managers/digital-assets/digital-assets-technology) |
| Fund issuer filing | [SEC Form D](https://www.sec.gov/Archives/edgar/data/2068319/000206831925000001/xslFormDX01/primary_doc.xml) |
| Public GitHub repository | **None found for this proprietary implementation**. The implementation's OpenZeppelin base libraries are public at [OpenZeppelin Contracts](https://github.com/OpenZeppelin/openzeppelin-contracts) and [OpenZeppelin Contracts Upgradeable](https://github.com/OpenZeppelin/openzeppelin-contracts-upgradeable). |
