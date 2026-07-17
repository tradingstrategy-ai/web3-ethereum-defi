# BENJI Ethereum contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, or smart-contract security.

## Identification

| Field | Finding |
| --- | --- |
| Fund | Franklin OnChain U.S. Government Money Fund (`FOBXX`) |
| Token | `BENJI` — one token represents one fund share according to the issuer |
| Primary chain context | Stellar is the primary BENJI venue; this note covers its distinct Ethereum representation. |
| Ethereum fund-token proxy | [`0x3DDc84940Ab509C11B20B76B466933f40b750dc9`](https://etherscan.io/address/0x3ddc84940ab509c11b20b76b466933f40b750dc9#code) |
| Proxy contract | `ERC1967Proxy` (OpenZeppelin) |
| Current implementation | [`0x20ca56F1215c3376B25bBa1f2F9D3701c5dEF4C5`](https://etherscan.io/address/0x20ca56f1215c3376b25bba1f2f9d3701c5def4c5#code) — `MoneyMarketFund_V6` |
| Token decimals | 18, as reported by the Etherscan token tracker |
| Verification status | The proxy has exact creation and runtime source matches in [Sourcify](https://sourcify.dev/server/v2/contract/1/0x3ddc84940ab509c11b20b76b466933f40b750dc9). The current implementation has Sourcify `match` (not `exact_match`) verification and Etherscan presents matching verified source for `MoneyMarketFund_V6`; record this distinction when relying on source reproduction. |

The canonical issuer [Benji DevHub contract registry](https://digitalassets.franklintempleton.com/benji/benji-contracts/)
lists this exact address as the Ethereum BENJI Fund Token. It separately lists
the primary Stellar BENJI asset issuer
`GBHNGLLIE3KWGKCHIKMHJ5HVZHYIK7WTBE4QF5PLAKL4CJGSEU7HZIW5`; do not
confuse the Stellar asset with this Ethereum contract or with `iBENJI`.

## Ethereum deployment architecture

BENJI is a proprietary multi-contract system, not a standalone generic ERC-20.
The issuer registry publishes these companion Ethereum modules:

| Module | Address | Role indicated by issuer |
| --- | --- | --- |
| Fund Token | [`0x3DDc…50dc9`](https://etherscan.io/address/0x3ddc84940ab509c11b20b76b466933f40b750dc9) | Share-token proxy discussed in this note. |
| Registry Module | [`0xD764…77d40`](https://etherscan.io/address/0xD7644d80575678C027CED844bbeEF5Ad12277d40) | BENJI registry component. |
| Token Registry Module | [`0x14DD…0427E`](https://etherscan.io/address/0x14DD78f8Ca45231dCe301AdEae179dcbEE40427E) | Token registry component. |
| Authorization Module | [`0x55dd…5b162`](https://etherscan.io/address/0x55dd370DeDe1AD474d3543Be06452615d3B5b162) | Whitelist/authorisation component. |
| Transactional Module | [`0x648a…ab4f5`](https://etherscan.io/address/0x648a6e41B4e445506b848cE49FfEF827651ab4f5) | Transaction-management component. |
| Transfer Agent Module | [`0x8C8B…9c666`](https://etherscan.io/address/0x8C8Bfc3151C2161a4baD77268e246A08e5D9c666) | Transfer-agent component. |
| Intent Validation Module | [`0xBA53…F8a93`](https://etherscan.io/address/0xBA5314385d4A849f8D8dBFb867b67547683F8a93) | Intent validation component. |
| MultiSig Module | [`0xA2Bd…B8b66`](https://etherscan.io/address/0xA2Bd91Fb0c8258134706629edf7464C14bAB8b66) | Multi-signature/access-control component. |

The registry also confirms implementations on Stellar, Polygon, Arbitrum,
Avalanche, Aptos, Base, and Solana. Those are individual chain deployments,
not interchangeable token addresses.

## Contract surface

The proxy delegates to upgradeable `MoneyMarketFund_V6`. Explorer source and
ABI show OpenZeppelin ERC-20, AccessControl, and UUPS upgradeability elements,
plus Franklin-specific transfer/recordkeeping logic.

| Function group | Functions | Description |
| --- | --- | --- |
| ERC-20 share token | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `increaseAllowance`, `decreaseAllowance`, `transfer`, `transferFrom` | Basic balance and allowance surface for fund shares. Transfer availability is separately switchable and restricted. |
| Share servicing | `mintShares`, `burnShares`, `getShareHoldings`, `getSharesOutstanding`, `hasHoldings`, `hasEnoughHoldings`, `getAccountsBalances` | Role-controlled share issuance/destruction and transfer-agent accounting/holder-list queries. |
| Transfer controls | `enable`/`disableInstantTransfer`, `enable`/`disableERC20Transfer`, `enable`/`disableERC20ThirdPartyTransfer`, `is…Enabled` | Privileged circuit breakers for direct, third-party ERC-20, and administrator “instant” transfers. Verified source describes ordinary ERC-20 transfers and `transferFrom` as available only between authorised shareholders when enabled. |
| Administrator settlement | `instantTransfer`, `instantCXTransferIn`, `instantCXTransferOut`, `transferShares`, `adminApprove` | Privileged direct, cross-chain, and approval-assisted fund-share servicing with memo fields. These are not ordinary permissionless bridging methods. |
| Price and holder maintenance | `lastKnownPrice`, `updateLastKnownPrice`, `updateHolderInList`, `removeEmptyHolderFromList` | Administrative NAV/reference-price update and holder-list maintenance. |
| Roles and upgrade | `DEFAULT_ADMIN_ROLE`, `ROLE_TOKEN_OWNER`, `grantRole`, `revokeRole`, `renounceRole`, `hasRole`, `getRoleAdmin`, `upgradeTo`, `upgradeToAndCall`, `proxiableUUID` | OpenZeppelin role control and UUPS upgrades. |

Neither the proxy nor `MoneyMarketFund_V6` exposes the ERC-4626 methods
`asset`, `deposit`, `withdraw`, `redeem`, `convertToShares`, or
`convertToAssets`. It is consequently a **permissioned tokenised mutual-fund
share contract**, not an ERC-4626 vault.

## Protocol-family conclusion

**Conclusion: Franklin Templeton Benji proprietary transfer-agent and
recordkeeping platform — high confidence.**

The identification is supported by the issuer-maintained registry, which
enumerates the exact token and the co-ordinated Registry, Authorisation,
Transactional, Transfer Agent, Intent Validation, and MultiSig modules. The
Franklin Templeton [technology description](https://www.franklintempleton.com/about-us/our-teams/specialist-investment-managers/digital-assets/digital-assets-technology)
calls Benji its proprietary blockchain-integrated recordkeeping system and
states that the transfer agent maintains the official shareholder record. The
implementation name (`MoneyMarketFund_V6`) and privileged issuance, transfer
control, cross-chain, price, and holder-maintenance functions match this
transfer-agent design.

The contract uses known *components* — OpenZeppelin ERC-20, AccessControl,
ERC-1967, and UUPS code — but it is not a deployment of a generic public
fund/vault protocol. In particular, it does not match ERC-4626, Centrifuge,
Securitize, Ondo, or Superstate interfaces.

## Public source, GitHub, and documentation

| Resource | Link | Finding |
| --- | --- | --- |
| Token-proxy explorer/source | [Etherscan: `0x3DDc…50dc9`](https://etherscan.io/address/0x3ddc84940ab509c11b20b76b466933f40b750dc9#code) | Officially identified BENJI ERC-1967 proxy and active implementation link. |
| Implementation explorer/source | [Etherscan: `0x20ca…EF4C5`](https://etherscan.io/address/0x20ca56f1215c3376b25bba1f2f9d3701c5def4c5#code) | `MoneyMarketFund_V6` ABI and matching source, including transfer-control comments and interfaces. |
| Independent verification | [Sourcify: proxy](https://sourcify.dev/server/v2/contract/1/0x3ddc84940ab509c11b20b76b466933f40b750dc9), [implementation](https://sourcify.dev/server/v2/contract/1/0x20ca56f1215c3376b25bba1f2f9d3701c5def4c5) | Exact proxy match; implementation `match`, rather than an exact metadata match. |
| Issuer contract documentation | [Benji DevHub contract registry](https://digitalassets.franklintempleton.com/benji/benji-contracts/) | Canonical multi-chain fund-token and module addresses. |
| Issuer product/platform documentation | [Franklin Templeton Digital Assets technology](https://www.franklintempleton.com/about-us/our-teams/specialist-investment-managers/digital-assets/digital-assets-technology) | Fund/share relationship and proprietary recordkeeping/transfer-agent architecture. |
| Public issuer GitHub source repository | None found | Searches for `MoneyMarketFund_V6`, `instantCXTransferIn`, `TransferAgentModule`, and the verified source path did not locate a Franklin Templeton-maintained public GitHub repository. The Etherscan/Sourcify source and DevHub registry are the public source references. |
| Known component family | [OpenZeppelin Contracts](https://github.com/OpenZeppelin/openzeppelin-contracts), [OpenZeppelin Contracts Upgradeable](https://github.com/OpenZeppelin/openzeppelin-contracts-upgradeable) | Explorer source imports OpenZeppelin ERC-20, AccessControl, ERC-1967 proxy, and UUPS upgradeability components. |

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned
**47,880,180.750413470304337021 BENJI** (18 decimals). The ABI exposes
`lastKnownPrice()` and privileged `updateLastKnownPrice()`, providing an
administrator-maintained reference price; it must be checked against issuer NAV
and freshness requirements before use.

## Integration implications

- Classify as a **Benji permissioned tokenised fund share**, not an ERC-4626
  vault and not an arbitrary freely transferable ERC-20.
- Resolve the active implementation every time an integration is refreshed;
  the fund token is upgradeable.
- Follow the issuer's authorisation/transfer-agent modules and the transfer
  status flags before considering a transfer or `transferFrom` executable.
- Treat `lastKnownPrice` as an administrator-maintained reference and verify
  current NAV from issuer fund materials before using it for valuation.
- Keep Ethereum BENJI, Stellar BENJI, iBENJI, and the other chain-specific
  BENJI representations as separate identifiers in metadata.
