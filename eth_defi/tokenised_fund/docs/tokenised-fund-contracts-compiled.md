# Tokenised fund contract research

Compiled on 2026-07-17 from the individual fund-contract research notes. Each section retains the original investigation, including supply and ABI price-availability findings.

## Contents

- BELIF Ethereum contract research
- BENJI Ethereum contract research
- BlackRock ICS US Dollar Liquidity Fund (CASHx) contract research
- CUMIU contract research
- DCP contract research
- FDIT Ethereum contract research
- FILQ contract research
- Franklin OnChain U.S. Government Money Fund (gBENJI) contract research
- iBENJI contract research
- Janus Henderson Anemoy Treasury Fund (JTRSY) contract research
- My OnChain Net Yield Fund (MONY) contract research
- Ondo Short-Term US Government Treasuries (OUSG) contract research
- State Street Galaxy Onchain Liquidity Sweep Fund (SWEEP) contract research
- thBILL contract research
- ULTRA Arbitrum contract research
- Ondo U.S. Dollar Yield (USDY) contract research
- USTB contract research
- USTBL Ethereum contract research
- USYC contract research
- WTGXX contract research

## BELIF Ethereum contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, or smart-contract security.

## Identification

| Field | Finding |
| --- | --- |
| Fund | Bosera Liquidity Income Fund SP |
| Token | `BELIF` — Bosera Liquidity Income Fund SP share token |
| Primary chain | Ethereum mainnet |
| Token / proxy | [`0x237c717df1b60501f8d029d3fe7385fd090df180`](https://etherscan.io/address/0x237c717df1b60501f8d029d3fe7385fd090df180#code) |
| Proxy contract | `ERC1967Proxy` (OpenZeppelin) |
| Active implementation | [`0xc7e64F6Ced1678ee4fB393A9053a47F303bA8454`](https://etherscan.io/address/0xc7e64f6ced1678ee4fb393a9053a47f303ba8454#code) — `CMTAT_PROXY` |
| Token decimals | 18, as reported by the Etherscan token tracker |
| Verification | Sourcify returned no published verification record for either address. Etherscan exposes the proxy and a **similar-match** source for the implementation; its source page warns that constructor differences can affect behaviour. The contract family and ABI are nevertheless unambiguous. |

Public RWA catalogues identify the issuer as Bosera and parent platform as
Libeara, and describe BELIF as a permissioned fund token. The on-chain
implementation, however, identifies itself as a CMTAT deployment rather than
embedding an issuer-specific contract name.

## Contract surface

`CMTAT_PROXY` is an upgradeable implementation of the
[CMTA CMTAT](https://github.com/CMTA/CMTAT) security-token framework. Its
Etherscan source tree contains the framework's mandatory ERC-20, mint, burn,
pause, enforcement, and RuleEngine modules, plus its optional validation
module and a custom `NAVModule`.

| Function group | Functions | Description |
| --- | --- | --- |
| ERC-20 and allowances | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `increaseAllowance`, `decreaseAllowance`, `transfer`, `transferFrom` | Standard token and allowance surface, subject to rule-engine and enforcement checks. |
| Transfer validation | `detectTransferRestriction`, `messageForTransferRestriction`, `validateTransfer`, `ruleEngine`, `setRuleEngine` | EIP-1404-compatible restriction discovery plus an external CMTA RuleEngine. A caller can preflight a transfer and retrieve a reason code/message. |
| Issuance and redemption | `mint`, `mintBatch`, `burn`, `burnBatch` | Role-controlled creation/destruction. Each method supplies NAV; burn additionally records a reason. |
| NAV / fund data | `latestNAV`, `getNAV`, `setLatestNAV`, `setNAV`, `currentEpoch`, `currencyNAV`, `NAVScalingFactor` | Stores per-epoch and latest NAV values on-chain. This is a pricing record, not an ERC-4626 asset-conversion interface. |
| Enforcement | `freeze`, `unfreeze`, `frozen`, `ENFORCER_ROLE` | Privileged account freeze control with a reason string. |
| Operations / metadata | `pause`, `unpause`, `paused`, `setFlag`, `setInformation`, `setTerms`, `setTokenId` | Emergency stop and security-token metadata fields. |
| Roles and upgrade | `DEFAULT_ADMIN_ROLE`, `MINTER_ROLE`, `PAUSER_ROLE`, `SNAPSHOOTER_ROLE`, `DEBT_ROLE`, `DEBT_CREDIT_EVENT_ROLE`, `grantRole`, `revokeRole`, `upgradeTo`, `upgradeToAndCall` | AccessControl roles and UUPS proxy upgrade controls. |

The ABI does not contain ERC-4626 `asset`, `deposit`, `withdraw`, `redeem`,
`convertToShares`, or `convertToAssets` methods. BELIF is consequently a
**permissioned CMTAT tokenised-fund share**, not an ERC-4626 vault.

## Protocol-family conclusion

**Conclusion: CMTA CMTAT security-token framework with NAV and RuleEngine
modules; Bosera/Libeara product deployment — high confidence.**

The active contract's name (`CMTAT_PROXY`), Etherscan source-file paths, and
ABI precisely match CMTAT's modular architecture: EIP-1404 validation,
RuleEngine integration, issuer-controlled freeze/enforcement, pause controls,
role-based mint/burn, and UUPS upgrades. The separate NAV module makes this
variant suitable for a fund share rather than a generic security token.

The CMTAT framework is a public CMTA reference implementation for tokenising
financial instruments. It is a protocol/framework dependency, not evidence
that CMTA is the fund issuer. Bosera is the fund issuer and Libeara is the
reported tokenisation platform; no product-specific Libeara or Bosera source
repository for this deployment was located in public GitHub search.

## Public source, GitHub, and documentation

| Resource | Link | Finding |
| --- | --- | --- |
| Token-proxy explorer/source | [Etherscan: `0x237c…f180`](https://etherscan.io/address/0x237c717df1b60501f8d029d3fe7385fd090df180#code) | ERC-1967 proxy, active implementation, proxy upgrade history, and token tracker. |
| Implementation explorer/source | [Etherscan: `0xc7e6…8454`](https://etherscan.io/address/0xc7e64f6ced1678ee4fb393a9053a47f303ba8454#code) | `CMTAT_PROXY` ABI and similar-match source tree showing CMTAT, RuleEngine, and NAV modules. Treat this as a similar-match reference rather than an exact published source verification. |
| Sourcify | [Proxy lookup](https://sourcify.dev/server/v2/contract/1/0x237c717df1b60501f8d029d3fe7385fd090df180), [implementation lookup](https://sourcify.dev/server/v2/contract/1/0xc7e64f6ced1678ee4fb393a9053a47f303ba8454) | No Sourcify record was available when checked. |
| Framework source | [CMTA/CMTAT](https://github.com/CMTA/CMTAT) | Public CMTAT security-token framework and source family matching the implementation. |
| Rule-engine source and documentation | [CMTA/RuleEngine](https://github.com/CMTA/RuleEngine) | Public RuleEngine used by CMTAT to validate restrictions on transfers and other token operations. |
| Framework documentation | [CMTA RuleEngine overview](https://cmta.ch/standards/ruleengine-for-cmtat) | CMTA description of deploying a RuleEngine alongside CMTAT for conditional transfer validation. |
| Product/platform context | [BELIF RWA listing](https://defillama.com/rwa/asset/BELIF), [Libeara](https://libeara.com/), [Libeara GitHub organisation](https://github.com/libeara) | Public catalogue attributes BELIF to Bosera / Libeara and labels it permissioned; no product-specific public source repository was found. |

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **102,796,944.2 BELIF**
(18 decimals). The CMTAT ABI exposes `latestNAV()`, `getNAV(epoch)`,
`currencyNAV()`, and `NAVScalingFactor()`, making issuer-administered on-chain
NAV/reference-price data available subject to scale and epoch validation.

## Integration implications

- Classify BELIF as a **CMTAT permissioned security/fund token**, not an
  ERC-4626 vault or freely transferable ERC-20.
- Preflight transfers with `detectTransferRestriction` and
  `messageForTransferRestriction`; a standard ERC-20 `transfer` can fail due
  to the configured RuleEngine or a frozen account.
- Account for privileged freeze, pause, mint/burn, RuleEngine replacement, and
  UUPS-upgrade paths in any risk or integration assessment.
- Use `latestNAV` / `getNAV(epoch)` as the contract's reference price data but
  verify NAV methodology and fund redemption terms with the issuer.
- Re-resolve the active implementation and source-verification status before
  production integration, because the proxy is upgradeable and the current
  source evidence is an Etherscan similar match rather than Sourcify metadata.

## BENJI Ethereum contract research

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

## BlackRock ICS US Dollar Liquidity Fund (CASHx) contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| BlackRock ICS US Dollar Liquidity Fund, KAIO-tokenised (`CASHx`) | Ethereum ERC-20 proxy, [`0x42975aAe7A124257E7fDa7f5E8382F51449B784A`](https://etherscan.io/address/0x42975aae7a124257e7fda7f5e8382f51449b784a#code) | OpenZeppelin `ERC1967Proxy`; active implementation [`SecurityToken`](https://etherscan.io/address/0x83fd2337eda0855b64e7194e2f46203927eb3360#code) at `0x83fD2337EDA0855B64E7194e2f46203927Eb3360` | A permissioned ERC-20 security token in KAIO's (formerly Libre) registry/order-book architecture. It validates investor eligibility during transfers, permits only its subscription book to issue and redemption book to burn, and is role-upgradeable through a customised UUPS implementation. It is not an ERC-4626 vault. | No public KAIO/Libre Solidity repository or deployment-specific GitHub source was located. The [verified Etherscan source](https://etherscan.io/address/0x83fd2337eda0855b64e7194e2f46203927eb3360#code) is the reproducible code reference. | [KAIO smart-contract overview](https://docs.kaio.xyz/how-kaio-works/smart-contracts), [KAIO architecture](https://docs.kaio.xyz/how-kaio-works/architecture), [RWA.xyz CASHx asset record](https://app.rwa.xyz/assets/CASHx) |

## On-chain identification and verification

RWA.xyz identifies the Ethereum primary token for the KAIO-tokenised
BlackRock ICS US Dollar Liquidity Fund as
[`0x42975aAe7A124257E7fDa7f5E8382F51449B784A`](https://etherscan.io/address/0x42975aae7a124257e7fda7f5e8382f51449b784a#code).
Live calls report the name `Libre SAF VCC - USD I Money Market A1`, symbol
`CASHx`, and 18 decimals. This is the KAIO/Libre A1 tokenised share class, not
a generic BlackRock ERC-20.

Etherscan classifies the token as an OpenZeppelin `ERC1967Proxy` and identifies
its active implementation as
[`0x83fD2337EDA0855B64E7194e2f46203927Eb3360`](https://etherscan.io/address/0x83fd2337eda0855b64e7194e2f46203927eb3360#code).
Calling the token's `getImplementation()` on Ethereum returns the same address.
The implementation is an **exact** Etherscan source match, named
`SecurityToken`, compiled with Solidity `0.8.28`, optimiser 200 runs and the
Cancun EVM target. The proxy's source is a similar-match OpenZeppelin
`ERC1967Proxy`; that thin dispatcher does not contain the fund-token logic.

Sourcify returned no match for either the proxy or the active implementation
during this check. The verified Etherscan implementation source, rather than
Sourcify, is therefore the source of record for the current deployment.

The separate address `0xcf2ca1b21e6f5da7a2744f89667de4e450791c79` is not used
in this report: RWA.xyz lists `0x4297…784A` as CASHx's primary Ethereum token
and lists `0xcf2…1c79` separately with zero Ethereum supply. Treat it as a
separate representation rather than silently substituting it for the primary
share token.

## Contract surface and behaviour

`SecurityToken` is a KAIO/Libre permissioned share-token contract. Its ABI and
verified source show an OpenZeppelin upgradeable ERC-20 combined with KAIO's
investor, instrument, role, and operations registries. It has no ERC-4626 or
ERC-7540 surface (`asset`, `totalAssets`, `deposit`, `withdraw`, `redeem`,
`requestDeposit`, and `requestRedeem` are absent).

| Area | Material functions | Behaviour |
| --- | --- | --- |
| ERC-20 | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `increaseAllowance`, `decreaseAllowance` | Standard ERC-20 reads and approvals. The verified `_update` path rejects zero-value operations, checks whether the instrument is paused, and applies eligibility/operations-engine checks before movement. |
| Investor-aware accounting | `getInstrumentId`, `getHolders`, `getInvestorBalance(investorId)` | Binds the token to a KAIO instrument and maintains holder/investor balances through the investor registry. Token balances are consequently not a stand-alone permission signal. |
| Issuance and redemption lifecycle | `issue(investor,amount)`, `burn(amount)` | Only the configured subscription book can call `issue`; only the redemption book can call `burn`. These are issuer-platform lifecycle operations, not public subscription/redemption methods. |
| Forced movement | `forceTransfer(from,to,amount,role)` | Only the redemption book or KAIO gateway may force a transfer, subject to a supplied authorised role. This is a material administrator/settlement capability. |
| Administration | `changeNameAndSymbol(name,symbol,role)` | The caller must possess the role passed into the call, as validated by the role registry. It can change the token metadata. |
| Upgradeability | `getImplementation`, `upgradeToWithRole`, `upgradeToWithRoleAndCall` | The implementation uses `LibreUUPSUpgradeable`, a UUPS/EIP-1967 design that authorises upgrades through the supplied role. Standard `upgradeTo` and `upgradeToAndCall` deliberately revert. |

The implementation's custom errors make the intended constraints explicit:
`ISecurityToken_OnlySubscriptionBookCanIssueTokens`,
`ISecurityToken_OnlyRedemptionBookCanBurnTokens`,
`ISecurityToken_OnlyRedemptionBookOrGatewayCanForceTransfer`,
`ISecurityToken_InstrumentPaused`, `ISecurityToken_InvalidSender`, and
`ISecurityToken_InvalidReceiver`.

## Known protocol and fund conclusion

**Conclusion: KAIO/Libre proprietary permissioned security-token architecture
for the CASHx fund share class — high confidence.**

The exact verified implementation source names its author as Libre and imports
`IInstrumentRegistry`, `IInvestorRegistry`, `IRoleRegistry`, and
`IOperationsEngine`. Its distinctive `issue`, `forceTransfer`,
`getInstrumentId`, role-gated upgrades, and subscription/redemption-book
references match KAIO's public architecture documentation: KAIO says each
instrument issues a permissioned security token, operates through investor and
instrument registries, and uses per-instrument subscription and redemption
order books.

Web and GitHub searches for `SecurityToken`, `LibreUUPSUpgradeable`, and the
deployment's distinctive custom errors did not find a public KAIO/Libre source
repository containing this Solidity code. Results for similarly named
security-token systems (such as ERC-3643/T-REX) are not this implementation:
their identity-registry interfaces and function names differ. Accordingly,
classify this as a proprietary KAIO/Libre system, not as ERC-3643, ERC-4626,
or another known open-source fund-token deployment.

KAIO documents a registry-based architecture: investor, instrument, fund,
dealer, jurisdiction, and role registries provide the surrounding controls; a
rules engine applies compliance modules; and subscription/redemption books
execute the token lifecycle. The ERC-20 is one component of the product, not
the complete subscription, pricing, or redemption system.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned
**275,817.125781019762931102 CASHx** (18 decimals). The primary `SecurityToken`
ABI has no share-price or NAV accessor; obtain NAV from the KAIO/issuer fund
and order-book lifecycle instead.

## Integration implications

- Track `0x4297…784A` as the primary Ethereum CASHx ERC-20, but resolve and
  monitor its EIP-1967 implementation because authorised upgrades can change
  executable behaviour.
- Do not treat CASHx as an ERC-4626 vault or call `issue`/`burn` as public
  investment functions. The supported lifecycle flows are controlled by KAIO's
  subscription and redemption books.
- A normal ERC-20 interface is insufficient proof that an address can receive
  or transfer the token. KAIO documents investor approval, instrument-specific
  rules, and compliance checks; simulate the intended route or use KAIO's
  onboarding/API before attempting a transfer.
- `forceTransfer` and role-gated upgrades are issuer/platform powers that need
  explicit operational-risk treatment. Monitor the implementation and related
  registry addresses if using this token in production.

## Primary sources

- [Etherscan primary CASHx proxy](https://etherscan.io/address/0x42975aae7a124257e7fda7f5e8382f51449b784a#code)
- [Etherscan verified `SecurityToken` implementation](https://etherscan.io/address/0x83fd2337eda0855b64e7194e2f46203927eb3360#code)
- [RWA.xyz CASHx asset record](https://app.rwa.xyz/assets/CASHx)
- [KAIO smart-contract overview](https://docs.kaio.xyz/how-kaio-works/smart-contracts)
- [KAIO architecture and permissioned-token description](https://docs.kaio.xyz/how-kaio-works/architecture)

## CUMIU contract research

Checked on 2026-07-17. This note identifies the Ethereum token contract and
its observable technical interface. It is not an assessment of investor
eligibility, legal ownership, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| ChinaAMC USD Digital Money Market Fund — Class I USD (CUMIU) | Ethereum ERC-20, [`0x85D38585c3aC08268F598282a84b7c0Ddfc0d04F`](https://etherscan.io/token/0x85d38585c3ac08268f598282a84b7c0ddfc0d04f) | OpenZeppelin `ERC1967Proxy`; active UUPS logic [`0x37686928DDf77BE20fc4199f03f0638d90D9907E`](https://etherscan.io/address/0x37686928ddf77be20fc4199f03f0638d90d9907e#code), source-labelled `CMTAT_PROXY` | A permissioned fund-share ERC-20 built from the CMTA Token (CMTAT) security-token framework. Its rule engine governs transfers; privileged roles can mint/burn with NAV metadata, update NAV by epoch, pause, freeze/unfreeze holders, configure terms/information, and upgrade the UUPS implementation. | [CMTA/CMTAT reference Solidity implementation](https://github.com/CMTA/CMTAT), [CMTA/RuleEngine](https://github.com/CMTA/RuleEngine). No public ChinaAMC or Libeara deployment repository was found. | [ChinaAMC fund page / tokenisation FAQ](https://www.chinaamc.com.hk/product/chinaamc-usd-digital-money-market-fund-listedclass/), [CMTA CMTAT standard](https://cmta.ch/standards/cmta-token-cmtat), [RWA.xyz CUMIU asset record](https://app.rwa.xyz/assets/CUMIU) |

## On-chain verification

Etherscan identifies `0x85D3…d04F` as *ChinaAMC USD Digital Money Market Fund
Class I USD (CUMIU)* with 18 decimals. Its source is an **Exact Match**
OpenZeppelin `ERC1967Proxy` and Etherscan resolves the active EIP-1967
implementation to
[`0x37686928DDf77BE20fc4199f03f0638d90D9907E`](https://etherscan.io/address/0x37686928ddf77be20fc4199f03f0638d90d9907e#code).
The latest visible proxy upgrade to that implementation occurred on 2025-07-31.

The implementation page labels its source as `CMTAT_PROXY`, compiled with
Solidity `0.8.17` (optimiser enabled, 200 runs), and exposes CMTAT's
`CMTAT_BASE`, rule-engine, enforcement, mint, burn, pause, validation, and NAV
modules. Etherscan classifies this deployment as a **Similar Match** to source
at another address, explicitly warning that a differing constructor can alter
actual behaviour. It is therefore strong protocol-family evidence and an ABI
reference, but not an exact-source-verification claim for this implementation.

Sourcify did not return a hosted full- or partial-match metadata package for
the proxy during this research. The proxy's exact Etherscan verification and
the implementation's explorer-labelled source are the available deployment
references.

## Contract surface and behaviour

| Area | Functions | Effect |
| --- | --- | --- |
| ERC-20 | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, two `approve` overloads, `transfer`, `transferFrom`, `increaseAllowance`, `decreaseAllowance` | ERC-20-compatible share interface, subject to transfer restrictions. |
| Transfer-compliance engine | `ruleEngine`, `setRuleEngine`, `validateTransfer`, `detectTransferRestriction`, `messageForTransferRestriction` | Consults an external CMTAT/EIP-1404 rule engine before token movement and exposes a restriction code/message for integrators. |
| Mint, burn, and NAV | `mint`, `mintBatch`, `burn`, `burnBatch`, `latestNAV`, `getNAV`, `setLatestNAV`, `setNAV`, `currentEpoch`, `currencyNAV`, `NAVScalingFactor` | Privileged issuance/redemption operations carry NAV values; NAV can be stored by epoch and read from the token. |
| Enforcement and emergency control | `freeze`, `unfreeze`, `frozen`, `pause`, `unpause`, `paused` | `ENFORCER_ROLE` can freeze accounts with a reason; `PAUSER_ROLE` can halt token operations. |
| Legal/instrument metadata | `tokenId`, `setTokenId`, `terms`, `setTerms`, `information`, `setInformation`, `flag`, `setFlag` | Stores mutable instrument identifiers, terms, information, and a flag field. |
| Roles and upgrades | `grantRole`, `revokeRole`, `renounceRole`, `hasRole`, `MINTER_ROLE`, `ENFORCER_ROLE`, `PAUSER_ROLE`, `SNAPSHOOTER_ROLE`, `upgradeTo`, `upgradeToAndCall`, `proxiableUUID` | Access-controlled administration and UUPS implementation upgrades. |

The CMTAT ABI's `RuleEngineTransferRestricted` error and transfer-restriction
functions are direct evidence that a successful generic ERC-20 ABI decode does
not imply a token can move to arbitrary wallets. The current rule-engine
address and role members must be read at integration time.

## Protocol-family conclusion

**Conclusion: a CMTAT security-token deployment (high confidence), configured
for ChinaAMC/Libeara's permissioned fund platform; not ERC-4626.**

The on-chain source path and contract name directly identify the CMTA Token
reference architecture: `CMTAT_PROXY`, `CMTAT_BASE`, `RuleEngineModule`,
`EnforcementModule`, `MintModule`, `BurnModule`, `NAVModule`, and EIP-1404
interfaces. The public [CMTA reference repository](https://github.com/CMTA/CMTAT)
describes CMTAT as an ERC-20-compatible security-token framework for
tokenising financial instruments, with optional compliance modules; CMTA's
standard documentation lists the same Solidity reference implementation and
separate RuleEngine project.

ChinaAMC identifies the product as a tokenised money-market fund, and the
fund's prospectus material describes the permissioned Libeara platform as an
Ethereum-smart-contract system recording tokenised-share ownership. RWA.xyz
independently identifies CUMIU as the Class I USD token on the Libeara
platform. This supports the issuer/platform attribution, while the CMTAT
source evidence supports the implementation family. Neither the token ABI nor
the documented framework exposes the ERC-4626 `asset`, `deposit`, `withdraw`,
or `redeem` interface.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **5,301,526.7589 CUMIU**
(18 decimals). The CMTAT ABI exposes `latestNAV()`, `getNAV(epoch)`,
`currencyNAV()`, and `NAVScalingFactor()`, so on-chain NAV/reference-price data
is available; consumers must apply the declared scaling and epoch semantics.

## Integration implications

- Classify CUMIU as a **permissioned tokenised fund/security token**, not a
  permissionless money-market vault. Transfer status depends on the externally
  configured rule engine and possibly frozen-account status.
- Track the ERC-1967 proxy address and resolve the current implementation at
  run time. Both the proxy and CMTAT's UUPS logic are upgradeable.
- Do not equate `totalSupply` with fund value: the token separately stores
  issuer-administered NAV values and epochs. Fetch the current NAV route and
  confirm scaling/currency before valuation.
- Handle roles as material operational risk: `MINTER_ROLE` and enforcement
  controls can issue/burn and freeze token positions, while the upgrade
  authorisation controls code changes.
- Use CMTAT's `detectTransferRestriction`/`messageForTransferRestriction`
  before attempting operational transfers where a node/provider permits
  simulation; an allowlist/rule failure should be an expected outcome, not a
  generic token error.

## Primary sources

- [Etherscan CUMIU proxy/token](https://etherscan.io/token/0x85d38585c3ac08268f598282a84b7c0ddfc0d04f)
- [Etherscan active `CMTAT_PROXY` implementation](https://etherscan.io/address/0x37686928ddf77be20fc4199f03f0638d90d9907e#code)
- [CMTA CMTAT standard documentation](https://cmta.ch/standards/cmta-token-cmtat)
- [CMTA CMTAT reference Solidity code](https://github.com/CMTA/CMTAT)
- [CMTA RuleEngine code](https://github.com/CMTA/RuleEngine)
- [ChinaAMC USD Digital Money Market Fund product page](https://www.chinaamc.com.hk/product/chinaamc-usd-digital-money-market-fund-listedclass/)
- [RWA.xyz CUMIU fund record](https://app.rwa.xyz/assets/CUMIU)

## DCP contract research

Checked on 2026-07-17. This note identifies the public Ethereum deployment and
technical interface; it is not an assessment of investor eligibility, legal
ownership, custody, note terms, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Guggenheim Treasury Services Digital Commercial Paper (DCP / GDCP) | Ethereum ERC-1155 transparent proxy, [`0xb5710A6FeDe27d1048C75B157BD3403BA08cdBe0`](https://etherscan.io/token/0xb5710a6fede27d1048c75b157bd3403ba08cdbe0) | `TransparentUpgradeableProxy`; current verified implementation [`CPTOKEN_V3`](https://etherscan.io/address/0xe0dd433372ac31c0055b7a40663033cfb3542671#code) at `0xe0DD433372Ac31C0055b7a40663033cfb3542671` | Zeconomy/AmpFi.Digital's proprietary, upgradeable ERC-1155 commercial-paper instrument. Token IDs carry independently issuable balances; the contract combines programme-scoped eligibility, issuance/burning, locked balances, payment/settlement state, beneficiary-aware transfers, EIP-712 administrator authorisation, and an HTLC hook. | No public issuer repository for `CPTOKEN_V3`, `PROGRAM_V3`, or AmpFi.Digital's deployed contract suite was found in GitHub searches. | [Zeconomy platform](https://www.zeconomy.com/platform), [Guggenheim/Zeconomy DCP launch announcement](https://www.businesswire.com/news/home/20240926279305/en/Guggenheim-Treasury-Services-Taps-Zeconomy-Platform-to-Issue-the-First-Ever-On-Chain-Digital-Commercial-Paper), [RWA.xyz Zeconomy profile](https://app.rwa.xyz/platforms/zeconomy) |

## On-chain verification

Etherscan labels the supplied address as the **Guggenheim Treasury Services
Digital Commercial Paper (GDCP)** ERC-1155 proxy. Its proxy source is verified
as an **Exact Match** of OpenZeppelin `TransparentUpgradeableProxy`, compiled
with Solidity `0.8.20` and 200 optimiser runs. The active EIP-1967
implementation is
[`0xe0DD433372Ac31C0055b7a40663033cfb3542671`](https://etherscan.io/address/0xe0dd433372ac31c0055b7a40663033cfb3542671#code).

The implementation is separately Etherscan **Exact Match** source verified as
`CPTOKEN_V3`, also compiled with Solidity `0.8.20` and 200 optimiser runs. Its
verified source tree includes the proprietary files
`CPTOKEN_V3.sol`, `CPTOKEN.sol`, `PROGRAM.sol`, `PROGRAM_V3.sol`,
`ZEC_EIP712.sol`, and `MTHTLC_V1.sol`, together with OpenZeppelin's upgradeable
ERC-1155, initialisation, nonce, reentrancy-guard, and transparent-proxy
components. Etherscan records the active implementation upgrade on 2025-10-01.

Sourcify v2 provides an exact match for the proxy but does not return a
full- or partial-match package for the active `CPTOKEN_V3` implementation.
The verified Etherscan implementation is therefore the deployment-specific
source and ABI reference.

## Contract surface and behaviour

| Area | Functions | Effect |
| --- | --- | --- |
| Multi-token ledger | `balanceOf`, `balanceOfBatch`, `totalSupply(tokenId)`, `exists`, `uri`, `name`, `symbol`, `supportsInterface` | An ERC-1155 collection rather than a single ERC-20 balance. Supply and ownership are token-ID-specific. |
| Issue and retire notes | overloaded `issueToken`, `burnToken` | Creates/retire token-ID balances. Issuance accepts a programme ID, beneficiary, lock amount, and operational notes; one overload additionally accepts a `zenocoin` value. |
| Transfers and approval | `setApprovalForAll`, `isApprovedForAll`, standard and extended `safeTransferFrom`/`safeBatchTransferFrom` | Supports ERC-1155 movement plus extended transfer methods carrying from/to beneficiary addresses and a note. The implementation ABI has `NotWhiteListed(programId,address)` and `IssuanceNotPermitted` errors, demonstrating programme-scoped access control. |
| Locked balances | `getLockedTokens`, `authorizeLock`, `authorizeUnlock` | Tracks token-ID-specific locked amounts and permits authorised lock/unlock operations. Locking is part of the contract's settlement/operational model, not a generic ERC-1155 feature. |
| Payment and maturity state | `markPaid`, `isPaid`, `validateBatchPayment` | Records and queries a token-ID payment/settlement flag, including batch validation. This is consistent with note-level lifecycle administration but does not by itself prove or price a legal redemption claim. |
| Administration and authorisation | `getContractOwner`, `transferContractOwnership`, `getDomainSeparator`, `setDomainSeparator`, `nonce` | Uses a custom `ZEC_EIP712` domain and signed administrator-authorisation structure for ownership transfer, rather than a simple public `Ownable` transfer flow. |
| Programme and cross-system hooks | `programV2Init`, `setHtlc` | Initialises the linked programme V2 logic and configures a mint-to-pay HTLC address. These are bespoke AmpFi.Digital components. |
| Upgrade control | transparent-proxy admin and `Upgraded` events | The user-facing token address is a TransparentUpgradeableProxy. Resolve its implementation dynamically and monitor the proxy-admin upgrade authority. |

The DCP launch announcement describes the product as Ethereum-based digital
commercial paper issued by Guggenheim Treasury Services through Zeconomy's
AmpFi.Digital platform. The later XRPL deployment is a separate network
representation/issuance rail; it does not turn this Ethereum ERC-1155 contract
into an XRP Ledger token or make it an ERC-20 token.

## Protocol-family conclusion

**Conclusion: Zeconomy/AmpFi.Digital's proprietary commercial-paper
tokenisation protocol (high confidence), implemented as a permissioned,
multi-token ERC-1155 system; not ERC-4626 or a conventional ERC-20 fund
token.**

The conclusion is direct from the verified implementation: `CPTOKEN_V3` has a
custom `PROGRAM_V3` and `ZEC_EIP712` architecture, extends ERC-1155 rather
than ERC-20, and exposes the distinctive note-lifecycle functions for issuance,
locking, payment marking, beneficiary-aware transfer, and HTLC configuration.
It is also independently consistent with the issuer announcement that identifies
AmpFi.Digital as the platform for issuance, trading, and governance of GTS's
Ethereum DCP.

Searches for the exact contract/source names (`CPTOKEN_V3`, `PROGRAM_V3`,
`MTHTLC_V1`, and `ZEC_EIP712`), the implementation address, and AmpFi.Digital
did not locate a public issuer GitHub repository. Thus the protocol family is
known, but the Etherscan-verified source must be used for the exact deployed
version; it cannot be mapped to a public Git revision. The source tree uses
OpenZeppelin components, but this is not evidence that the bespoke DCP logic is
an OpenZeppelin standard or a generic protocol such as ERC-3643, T-REX, CMTAT,
or ERC-4626.

## On-chain supply and ABI price availability

No aggregate total supply is available: DCP is ERC-1155 and its ABI exposes
`totalSupply(tokenId)` only. Supply must therefore be queried per commercial
paper token ID, which requires the relevant programme/issuance records. The ABI
has no share-price or NAV function; it records note lifecycle and payment state
rather than a fund-share price.

## Integration implications

- Treat DCP as an **ERC-1155, token-ID-specific instrument**, not as a single
  fungible ERC-20. Holdings, supply, locks, payment flags, and metadata need a
  token-ID dimension.
- Do not infer permissionless transferability from ERC-1155 support. The
  verified ABI exposes programme-specific whitelist and issuance restrictions,
  and extended transfer methods carry beneficiary/operational fields.
- Do not treat `isPaid(tokenId)` as an independently sufficient proof of legal
  payment, maturity, NAV, or redeemability. Reconcile it with the programme's
  off-chain documents and administrator workflow.
- For a settlement integration, account for locked balances and the distinct
  issue, transfer, lock/unlock, and payment-marking operations. A wallet's raw
  `balanceOf` may include units that are not operationally transferable.
- Track the transparent proxy address, current implementation, and upgrades.
  The current implementation changed after the original Ethereum launch, so
  hard-coding a historical ABI/source version is unsafe.
- Keep the Ethereum and XRPL rails separate when reporting supply or assets;
  the public materials describe multi-rail issuance, and a shared product name
  is not evidence that balances can be summed without issuer reconciliation.

## Primary sources

- [Etherscan GDCP ERC-1155 proxy](https://etherscan.io/token/0xb5710a6fede27d1048c75b157bd3403ba08cdbe0)
- [Etherscan verified `CPTOKEN_V3` implementation](https://etherscan.io/address/0xe0dd433372ac31c0055b7a40663033cfb3542671#code)
- [Sourcify exact-match GDCP proxy](https://sourcify.dev/server/v2/contract/1/0xb5710a6fede27d1048c75b157bd3403ba08cdbe0?fields=all)
- [Zeconomy platform](https://www.zeconomy.com/platform)
- [Guggenheim Treasury Services and Zeconomy DCP launch announcement](https://www.businesswire.com/news/home/20240926279305/en/Guggenheim-Treasury-Services-Taps-Zeconomy-Platform-to-Issue-the-First-Ever-On-Chain-Digital-Commercial-Paper)
- [RWA.xyz Zeconomy profile](https://app.rwa.xyz/platforms/zeconomy)

## FDIT Ethereum contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, or smart-contract security.

## Identification

| Field | Finding |
| --- | --- |
| Fund | Fidelity Treasury Digital Fund — OnChain Class (`FYOXX`) |
| Token | `FDIT` — Fidelity Digital Interest Token |
| Primary chain | Ethereum mainnet |
| Token / dispatcher | [`0x48ab4e39ac59f4e88974804b04a991b3a402717f`](https://etherscan.io/address/0x48ab4e39ac59f4e88974804b04a991b3a402717f#code) |
| Dispatcher contract | `ERC20RevocableComplianceToken` |
| Active basic package | [`0x98712eb572de9b8a1df756d45da6e80dffd18645`](https://etherscan.io/address/0x98712eb572de9b8a1df756d45da6e80dffd18645#code) — `ERC20RevocableComplianceTokenBasicPackageUpgradable` |
| Token decimals | 18, passed to the dispatcher constructor |
| Verification | Sourcify reports an **exact** creation- and runtime-bytecode match for the dispatcher, basic package, freeze, clawback, compliance, and transfer-control packages checked. |

Fidelity's [current summary prospectus](https://www.actionsxchangerepository.fidelity.com/ShowDocument/documentPDF.htm?applicationId=FIIS&clientId=Fidelity&collectionId=1079405&criticalIndicator=N&docFormat=pdf&docName=4.B-CTDLO-SUM.PDF&docType=SUMS&pdfReaderStatus=Y&securityId=09053&securityIdType=SASFN)
identifies the underlying fund and its OnChain (`FYOXX`) class. Etherscan's
token record describes FDIT as representing ownership in the Fidelity Treasury
Digital Fund.

## Contract architecture and surface

FDIT is **not** an ERC-1967 or beacon proxy. Its deployed
`ERC20RevocableComplianceToken` is a small dispatcher: on each call, its
fallback looks up the four-byte method selector in an ERC-7201-namespaced
`methodsImplementations` mapping and `delegatecall`s the associated package.
The mapping itself is changeable through the framework's controlled updates
machinery. A conventional explorer proxy checker consequently reports no proxy
even though the functional surface is modular and upgradeable.

The constructor configured the initialisation package
[`0x37116DC2B2d8cC1A26446288CE193BD2CA25f632`](https://etherscan.io/address/0x37116DC2B2d8cC1A26446288CE193BD2CA25f632#code),
`ERC20RevocableComplianceTokenInitPackageUpgradable`. This package initialises
the controller, update repository, role/context data, and interfaces, then
removes the one-time `initialize` selector. Reading the live dispatcher through
`getPackageAddressForFunction(bytes4)` confirmed the package assignments below
on 2026-07-17.

| Function group | Active package / functions | Description |
| --- | --- | --- |
| Basic ERC-20 and issuance | [`0x9871…8645`](https://etherscan.io/address/0x98712eb572de9b8a1df756d45da6e80dffd18645#code) — `ERC20RevocableComplianceTokenBasicPackageUpgradable`; `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `mint`, `burn(address,uint256)`, batch transfer/mint/burn/approve | The live basic package implements ordinary accounting plus privileged issuance/burn and batch operations. Its source is exact verified. |
| Transfer controls | [`0x88d7…05D3`](https://etherscan.io/address/0x88d72c8c147229aed87e3746208babe42d9905d3#code) — `ERC20RevocableComplianceTokenTransferrablePackageUpgradable`; `enableTransfers`, `disableTransfers`, `isTransferEnabled` | Administrative global transfer switch. Exact Sourcify source match. |
| Whitelist compliance | [`0x8a81…e9D9`](https://etherscan.io/address/0x8a8151ae93c465f427e3abe69b77d2cacafde9d9#code) — `ERC20RevocableComplianceTokenCompliancePackageUpgradable`; `setCompliance`, `removeCompliance`, `getCompliance` | Configures an external compliance/whitelist context. Exact Sourcify source match. |
| Freeze controls | [`0x6140…d440`](https://etherscan.io/address/0x6140898e0adab716747841a10e46779c7669d440#code) — `ERC20RevocableComplianceTokenFreezePackageUpgradable`; `freeze`, `unfreeze`, partial and batch freeze methods, `availableBalanceOf` | Account and partial-balance freeze controls. Exact Sourcify source match. |
| Recovery / clawback | [`0x0B20…A354`](https://etherscan.io/address/0x0b20f6ef06bc1b1fa5ca2e8f2a615bcdce28a354#code) — `ERC20RevocableComplianceTokenClawbackPackageUpgradable`; `clawback`, `batchClawback` | Agent-controlled recovery transfer from one account to another. Exact Sourcify source match. |
| Permit and governance | Permit package [`0x75bD…CC1F`](https://etherscan.io/address/0x75bdfeb2f2fb93595bb102ebe6a5fd7b8414cc1f#code); `permit`, `nonces`, `DOMAIN_SEPARATOR`; package/update/controller/role functions | The verified framework interfaces include EIP-2612 permit plus controller, role contexts, package lookup, and versioned update functions. Sourcify did not have a record for the current permit package when checked. |

The active basic package includes fund-control methods, but neither its ABI nor
the dispatcher contains ERC-4626 `asset`, `deposit`, `withdraw`, `redeem`,
`convertToShares`, `convertToAssets`, or `totalAssets`. **FDIT is a
permissioned, modular ERC-20 fund-share token, not an ERC-4626 vault.**

## Protocol-family conclusion

**Conclusion: DTCC Digital Assets / formerly Securrency Compliance Aware Token
Framework (CATF), specifically its `ERC20RevocableComplianceToken` package
family — high confidence.**

The deployed and exact-verified source carries a DTCC copyright notice and
labels itself “Compliance Aware Token Framework (ERC-20)”. The contract and
package names, selector-routing update design, external compliance context,
freeze, clawback, and transfer switch match DTCC's description of CATF:
real-time regulatory rule enforcement across the token lifecycle. The
framework is the technology layer; Fidelity is the fund issuer.

GitHub and web searches for the exact contract and package names did not locate
a public DTCC/Securrency CATF Solidity repository or a Fidelity FDIT source
repository. This is consistent with the source licence (`BUSL-1.1`) and does
not prevent validation of the published deployed source: the dispatcher and
material packages above are exact verified through Sourcify. The public
OpenZeppelin imports are library dependencies only.

## Public source, GitHub, and documentation

| Resource | Link | Finding |
| --- | --- | --- |
| Token-dispatcher explorer/source | [Etherscan: `0x48ab…717f`](https://etherscan.io/address/0x48ab4e39ac59f4e88974804b04a991b3a402717f#code) | `ERC20RevocableComplianceToken` constructor, source paths, and token tracker. Etherscan identifies the custom proxy/dispatcher and basic package. |
| Exact source verification | [Sourcify dispatcher source](https://sourcify.dev/server/v2/contract/1/0x48ab4e39ac59f4e88974804b04a991b3a402717f?fields=compilation,sources), [basic package source and ABI](https://sourcify.dev/server/v2/contract/1/0x98712eb572de9b8a1df756d45da6e80dffd18645?fields=compilation,abi,sources) | Exact creation and runtime verification. The other control-package addresses above were separately checked through Sourcify. |
| Framework documentation | [DTCC tokenisation service](https://www.dtcc.com/digital-assets/tokenization) | DTCC says its Compliance Aware Token Framework provides compliance/distribution controls, including mint, burn, force transfer, clawback, pause, and freeze. |
| Framework case study | [DTCC CATF case study](https://www.dtcc.com/-/media/Files/Downloads/Digital-Assets/WisdomTreeCaseStudy.pdf) | DTCC describes CATF as its real-time regulatory-rule enforcement framework for token issuance, distribution, and transaction operations. |
| Fund documentation | [Fidelity Treasury Digital Fund OnChain prospectus](https://www.actionsxchangerepository.fidelity.com/ShowDocument/documentPDF.htm?applicationId=FIIS&clientId=Fidelity&collectionId=1079405&criticalIndicator=N&docFormat=pdf&docName=4.B-CTDLO-SUM.PDF&docType=SUMS&pdfReaderStatus=Y&securityId=09053&securityIdType=SASFN) | Official current prospectus for the underlying Fidelity Treasury Digital Fund OnChain class. |
| GitHub | — | No public CATF or FDIT Solidity repository was found in GitHub/web searches on 2026-07-17. Public OpenZeppelin sources are dependencies, not the CATF implementation. |

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **73,801,963.32 FDIT**
(18 decimals). Neither the dispatcher nor its active basic/compliance packages
expose a share-price or NAV accessor; use Fidelity's official fund NAV source.

## Integration implications

- Classify FDIT as a **permissioned CATF ERC-20 fund-share token** with
  method-level package delegation, not a standard immutable ERC-20 or ERC-4626
  vault.
- Check the compliance context, transfer-enabled flag, frozen state, and
  available (not merely total) balance before treating a transfer as feasible.
- Account for controller/agent powers to mint, burn, freeze, claw back, change
  compliance context, pause operations, and update selected method packages.
- Resolve the package address for every method required by an integration from
  the live dispatcher. Do not assume all token methods share one implementation
  or that the currently verified basic package controls the compliance path.
- Re-check package mapping and Sourcify verification immediately before
  production use; the framework permits component-level upgrades.

## FILQ contract research

Checked on 2026-07-17. This note identifies the public Ethereum token
deployments and their technical interfaces; it is not an assessment of
investor eligibility, legal ownership, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Fidelity USD Digital Liquidity Fund, accumulating class (FILQ-A) | Ethereum ERC-20 UUPS proxy, [`0x54a4fc78431f9201824643e99bec891bb7462a1d`](https://etherscan.io/address/0x54a4fc78431f9201824643e99bec891bb7462a1d#code) | `UUPSProxy`; current verified implementation [`SygToken`](https://sourcify.dev/server/v2/contract/1/0x7030fe438be6ed196b8886616bbf5a245c267339?fields=all) at `0x7030fe438Be6Ed196B8886616BBF5a245c267339` | A 2-decimal, permissioned fund-share ERC-20 using Sygnum's current `SygToken` standard. It has SygFactory-backed wallet roles/blacklisting, issuer mint/burn and forced-transfer powers, pause control, EIP-2612 permits, an optional Chainlink data-feed interface, and timelocked UUPS upgrades. | No public repository for this exact 2026 `SygToken` source tree was found. [Sygnum's historical security-token contracts](https://github.com/sygnumbank/solidity-equity-token-contracts) are a documented predecessor/family reference, not the deployed source. | [FILQ product page](https://www.sygnum.com/filq/), [Sygnum FILQ launch architecture](https://www.sygnum.com/news/sygnum-powers-fidelity-internationals-first-tokenized-product-launch-with-moodys-aaa-mf-assessment/), [Fidelity prospectus](https://www.fidelityinternational.com/legal/documents/FISGF/en/pr.fisgf.en.xx.pdf) |
| Fidelity USD Digital Liquidity Fund, distributing class (FILQ-D) | Ethereum ERC-20 UUPS proxy, [`0xf0db6f529581e7f6ebac7a7f6882923c00fc3a66`](https://etherscan.io/address/0xf0db6f529581e7f6ebac7a7f6882923c00fc3a66#code) | `UUPSProxy`; same current `SygToken` implementation | The separate 2-decimal distributing share class. The implementation and permission manager are the same as FILQ-A; its configured data-feed address is distinct. | Same as FILQ-A. | Same as FILQ-A. |

## On-chain verification

The two supplied addresses are not duplicate deployments: direct calls identify
`0x54a4...2a1d` as **Fidelity USD Digital Liquidity Fund-Acc** / `FILQ-A` and
`0xf0db...3a66` as **Fidelity USD Digital Liquidity Fund-Dist** / `FILQ-D`.
Both report two decimals and store the same EIP-1967 implementation address,
`0x7030fe438Be6Ed196B8886616BBF5a245c267339`.

Sourcify v2 reports **exact creation and runtime matches** for both token
proxies as `UUPSProxy`, and an exact match for the shared implementation as
`SygToken`. The verified `SygToken` source is Solidity `0.8.30` and declares
Sygnum as the author/security contact. Its source tree includes `SygFactory`
permission-management interfaces, `ERC20SygToken`, roles, pausing, Chainlink
data-feed support, fund recovery, and Sygnum's timelocked UUPS components.
The public Etherscan pages linked above remain useful explorer references; the
Sourcify exact matches are the source-verification evidence used in this note.

Each proxy's `getPermissionManagerAddress()` returns
[`0x7427f3E0e32eb1ee19516aa5c6AbC99267a3eC89`](https://etherscan.io/address/0x7427f3e0e32eb1ee19516aa5c6abc99267a3ec89),
which is the external authority consulted for wallet roles. The class-specific
`getPriceFeedOracleAddress()` values are:

| Token class | Configured oracle address |
| --- | --- |
| FILQ-A | [`0x0c6c789A375cC4ee9CE6008715C915A91dA5AC5c`](https://etherscan.io/address/0x0c6c789a375cc4ee9ce6008715c915a91da5ac5c) |
| FILQ-D | [`0x7484379D1Af1B718DCCC6BB5e58AAdbcB6E4866A`](https://etherscan.io/address/0x7484379d1af1b718dccc6bb5e58aadbcb6e4866a) |

## Contract surface and behaviour

| Area | Functions | Effect |
| --- | --- | --- |
| ERC-20 and permit | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `permit`, `nonces`, `DOMAIN_SEPARATOR` | Standard ERC-20 mechanics plus EIP-2612 approval, all subject to SygToken's permission and pause checks. |
| Wallet permissioning | `getPermissionManagerAddress`, `isTokenUser`, `isTokenUserManager`, `isMinterBurner`, `isOperator`, `isPauser`, `isRoleManager`, `isSystem`, `updateTokenUser`, `blacklistTokenUser` | Roles are delegated to the external SygFactory permission manager. The verified source requires approved token users for token movement and makes blacklist status override whitelist status. |
| Issuance and exceptional movement | `mint`, `burn`, `forcedTransfer`, `updateMinterBurner` | Authorised roles can create/burn fund-share tokens and force a transfer to an approved recipient. These are issuer/administrator functions, not public ERC-4626 vault flows. |
| Price and metadata | `getPrice`, `getBundleData`, `getPriceFeedOracleAddress`, `getPriceFeedDescription`, `getPriceFeedDecimals`, `updatePricefeedOracleAddress`, `getTokenURI`, `updateTokenURI` | Provides an optional Chainlink AggregatorV3 or bundle-feed interface and token metadata URI. Sygnum states that Chainlink publishes FILQ's daily NAV and distribution metrics. |
| Emergency and recovery | `pause`, `unpause`, `isPaused`, `recoverFunds` | Authorised operators can halt token actions; the contract can recover accidentally held ETH/ERC-20 assets. |
| Upgrade control | `upgradeToAndCall`, `executeUpgrade`, `cancelUpgrade`, `getScheduledOperationDetails`, `getImplementation` | Proxy logic uses a Sygnum UUPS implementation with a scheduled/timelocked upgrade flow. The implementation's upgrade authorisation is bound to the permission manager. |

Sygnum describes FILQ as Ethereum ERC-20 tokens in a permissioned model where
only approved wallets transact and a transfer agent maintains access and
ownership records. Its product material says that the accumulating class
compounds yield into NAV whereas the distributing class pays monthly dividends
with a constant one-USD NAV structure. The token contract itself does not
expose an ERC-4626 `asset`, `deposit`, `withdraw`, or `redeem` interface.

## Protocol-family conclusion

**Conclusion: Sygnum's proprietary `SygToken`/Desygnate permissioned
tokenisation protocol (high confidence), not ERC-4626.**

This is a direct source-level identification, not merely a thematic match. The
active implementation is explicitly called `SygToken`, its exact verified
source names Sygnum and delegates permissions to `SygFactory`, and Sygnum's
official FILQ announcement says Desygnate provides the on-chain fund registry,
smart-contract settlement, and stablecoin subscription/redemption architecture.
The ABI's role, blacklist, forced-transfer, price-feed, and timelocked upgrade
families match a regulated security-token standard rather than a permissionless
money-market vault.

Sygnum's public GitHub repository documents an older `SygnumToken` security
token family with whitelist, role, mint/burn, pause, freeze/confiscation, and
proxy functionality. It is compelling historical/protocol-family evidence, but
it uses older source names and Solidity tooling and must **not** be treated as
the exact deployed `SygToken` version. Searches for the exact names
`SygToken`, `UUPSTimelockUpgradeable`, and the verified 2026 source paths did
not locate a public issuer repository containing this implementation.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` returned **448,264.28 FILQ-A** and
**300,377.53 FILQ-D** on Ethereum (both 2 decimals). The `SygToken` ABI exposes
configured Chainlink bundle-feed accessors. FILQ-A uses proxy
`0x0c6c...ac5c` and data id `02000001220700030000000000000000`; FILQ-D uses
proxy `0x7484...866a` and data id `02000001230700030000000000000000`.
Both proxies resolve to DataFeedsCache `0x16b5...1433`, which emits
`BundleReportUpdated(bytes16,uint256,bytes)` for every accepted daily report.

`getPrice()` is the single-value AggregatorV3 route and reverts for these
bundle feeds. In both reviewed schemas NAV/share is the second 32-byte bundle
word. `bundleDecimals()` scales it by four decimals for FILQ-A and two for
FILQ-D. Current and fixed-block reads use `latestBundle()`; historical report
discovery uses the cache event through Hypersync.

## Integration implications

- Track the two token proxies as distinct FILQ share classes, while treating
  their common implementation and permission manager as shared infrastructure.
  Do not merge their supplies merely because both represent the same fund.
- Classify FILQ as a **permissioned tokenised fund / ERC-20**, not an
  ERC-4626 vault. Standard token reads are available, but transfers, approvals,
  permits, subscriptions, and redemptions are subject to approved-wallet and
  operational processes.
- Do not assume a wallet can transfer based solely on its ERC-20 balance. The
  token checks user eligibility through SygFactory and has issuer-authorised
  blacklisting, pausing, mint/burn, and forced-transfer capabilities.
- Use the official product/transfer-agent workflow for issue and redemption.
  The issuer documents 24/7 eBanking access after onboarding, with market-hour
  settlement behaviour and potential out-of-hours queueing or fees.
- Resolve proxy implementations at integration time and monitor timelocked
  upgrades, the external permission-manager address, role membership, pause
  state, each class's configured oracle, data id, bundle decimals and cache
  aggregator. Chainlink NAV establishes valuation but does not establish
  independent redeemability.

## Primary sources

- [Sourcify exact-match FILQ-A `UUPSProxy`](https://sourcify.dev/server/v2/contract/1/0x54a4fc78431f9201824643e99bec891bb7462a1d?fields=all)
- [Sourcify exact-match FILQ-D `UUPSProxy`](https://sourcify.dev/server/v2/contract/1/0xf0db6f529581e7f6ebac7a7f6882923c00fc3a66?fields=all)
- [Sourcify exact-match `SygToken` implementation](https://sourcify.dev/server/v2/contract/1/0x7030fe438be6ed196b8886616bbf5a245c267339?fields=all)
- [Verified Chainlink DataFeedsCache](https://etherscan.io/address/0x16b53825c8ceaea593507274d4c1aaec9e261433#code)
- [Sygnum FILQ page](https://www.sygnum.com/filq/)
- [Sygnum's FILQ launch and Desygnate architecture](https://www.sygnum.com/news/sygnum-powers-fidelity-internationals-first-tokenized-product-launch-with-moodys-aaa-mf-assessment/)
- [Fidelity International Strategies Funds SPC prospectus](https://www.fidelityinternational.com/legal/documents/FISGF/en/pr.fisgf.en.xx.pdf)
- [Sygnum historical security-token GitHub repository](https://github.com/sygnumbank/solidity-equity-token-contracts)

## Franklin OnChain U.S. Government Money Fund (gBENJI) contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Franklin OnChain U.S. Government Money Fund, international share class (`gBENJI`) | Stellar classic issued asset: `gBENJI:GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP` | **No custom smart contract.** Classic Stellar `credit_alphanum12` asset, administered by its issuer account; it has the protocol-provided Stellar Asset Contract (SAC) identity `CAZGJD4BG6RLFQIAGPDPSX3IR73CBSVDEIBUDQGDZ3RCGGSOYSVBDSM7` for Soroban interoperability. | A seven-decimal, issuer-controlled Stellar fund-share asset. Holders use trustlines, and the issuer has required-authorization, revocation, and clawback flags. The SAC identifier is a built-in wrapper for a classic asset, not evidence of Franklin-deployed Soroban/Wasm token code. There is no EVM contract. | No public gBENJI Soroban/Wasm programme repository was found. The Stellar asset model is protocol-native; [Stellar's SAC implementation documentation](https://developers.stellar.org/docs/tokens/stellar-asset-contract) is the framework reference. | [Franklin Stellar TOML](https://www.franklintempleton.com/.well-known/stellar.toml), [Franklin BENJI contract/address hub](https://digitalassets.franklintempleton.com/benji/benji-contracts/), [Stellar.expert asset](https://stellar.expert/explorer/public/asset/gBENJI-GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP) |

## Identification: classic asset, not a custom Soroban token

gBENJI is unambiguously a **classic Stellar issued asset**. Its identifier has
the Stellar classic form `asset_code:issuer_G_account`; the asset code
`gBENJI` is seven characters and the issuer is the `G...` account
`GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP`.
Horizon reports its type as `credit_alphanum12`, rather than a contract token
with a `C...` contract address.

Franklin Templeton's issuer-controlled
[`stellar.toml`](https://www.franklintempleton.com/.well-known/stellar.toml)
lists that exact account as the gBENJI issuer. It describes the asset as one
share of the Franklin OnChain U.S. Government Money Fund, identifies the
underlying fund/share class as `LU2900381208`, specifies seven display
decimals, and directs investors to the gBENJI institutional website for buying,
selling, and transfers. Franklin's
[developer address hub](https://digitalassets.franklintempleton.com/benji/benji-contracts/)
independently lists the same Stellar fund-token issuer address.

The associated official
[Horizon asset record](https://horizon.stellar.org/assets?asset_code=gBENJI&asset_issuer=GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP&limit=1)
reports:

| Field | Finding |
| --- | --- |
| Asset type | `credit_alphanum12` |
| Asset code / issuer | `gBENJI` / `GD5J…BXRP` |
| Asset contract ID | `CAZGJD4BG6RLFQIAGPDPSX3IR73CBSVDEIBUDQGDZ3RCGGSOYSVBDSM7` |
| Authorised trustlines | 14 at the time queried |
| Issuer controls | `auth_required: true`, `auth_revocable: true`, `auth_clawback_enabled: true` |

Stellar distinguishes classic assets issued by `G...` accounts from custom
contract tokens issued by `C...` addresses. A classic asset is uniquely
identified by its code plus issuer, exactly as gBENJI is. The
[`GD5J…BXRP` issuer account on Stellar.expert](https://stellar.expert/explorer/public/account/GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP)
is therefore the relevant on-chain control point, not an EVM address or
custom-token contract.

## What the Soroban contract ID means

Every Stellar classic asset has a deterministic/reserved address for the
protocol-provided **Stellar Asset Contract** (SAC). Stellar documents that the
SAC is a special built-in contract implementing CAP-46-6 and the SEP-41 token
interface, through which Soroban contracts can interact with classic
trustline-based assets. Anyone can initiate deployment of that built-in wrapper;
the asset issuer need not be involved.

Consequently, Horizon's gBENJI `contract_id`
`CAZG…DSM7` should be recorded as the SAC identity if a Soroban integration
needs one, but it is not a Franklin-authored Soroban programme, an independently
auditable Wasm deployment, or a replacement for the `gBENJI:GD5J…BXRP`
asset identifier. The classic asset's issuance, trustlines, authorisation and
clawback policy remain governed by Stellar protocol operations and the issuer
account flags.

No public Franklin source identifies a custom gBENJI Soroban programme, calls,
Wasm hash, or separate transfer-agent contract. A GitHub/web search of the
issuer, exact asset identifier, `gBENJI`, and `Soroban` found no such
repository or contract. Etherscan and Sourcify do not apply to this
non-EVM asset.

## Asset behaviour and controls

| Area | Classic Stellar mechanism | Effect for gBENJI |
| --- | --- | --- |
| Asset identity | `gBENJI:issuer` | Both code and issuer must match; a ticker alone is not sufficient to identify the asset. |
| Holding | Trustline on the holder account | A recipient needs a gBENJI trustline and issuer authorisation. The asset record showed authorised and unauthorised trustlines, consistent with a permissioned fund asset. |
| Issue/distribution | Issuer account sends the credit asset | There is no public `mint()` ABI. Issuance is a Stellar payment/issuer-account operation and is controlled through the issuer's multi-signature account policy. |
| Transfers | Stellar payment operations between authorised trustlines | The issuer's `AUTH_REQUIRED` setting prevents an unapproved trustline from holding the asset. The institutional transfer process remains subject to Franklin onboarding. |
| Revocation and clawback | `AUTH_REVOCABLE` and `AUTH_CLAWBACK_ENABLED` issuer flags | The issuer retains protocol-level ability to revoke authorisation and claw back assets under Stellar's defined operations; this is a material administrative control. |
| Soroban use | Built-in SAC at `CAZG…DSM7` | Permits SEP-41-style interactions if the SAC is used, while preserving the classic asset's issuer controls. It does not make gBENJI a custom smart-contract token. |

## Fund and protocol conclusion

**Conclusion: Franklin Templeton gBENJI is a permissioned classic Stellar
fund-share asset, not a custom Soroban contract and not an EVM smart contract
— high confidence.**

The issuer-controlled TOML, Franklin address hub, and Horizon's
`credit_alphanum12` classification agree on the code-plus-issuer asset. The
fund's restrictions are implemented using Stellar's native trustline and issuer
authorisation/clawback framework. This is the classic Stellar protocol family,
with the generic built-in SAC available only as the interoperable Soroban
surface.

Franklin's public multi-chain developer hub lists modular EVM contracts for
BENJI on Ethereum-compatible chains, but for gBENJI it lists only the Stellar
fund-token issuer address. Do not project the EVM registry/authorisation/
transfer-agent module architecture onto this Stellar asset without direct
issuer evidence.

## On-chain supply and ABI price availability

At 2026-07-17, Horizon reported **54,534,218.0235468 gBENJI** in authorised
trustline balances. As a Stellar classic asset, gBENJI has no issuer-written
token ABI and no on-chain share-price accessor; its deterministic Stellar Asset
Contract wrapper does not change that fact.

## Integration implications

- Store the canonical Stellar identifier as
  `gBENJI:GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP`,
  not only `gBENJI` and not the SAC ID alone.
- Model it as a classic asset with trustlines and issuer-controlled
  authorisation, revocation, and clawback, rather than ERC-20/ERC-4626 or a
  custom Soroban contract.
- For a Soroban integration, use the deterministic SAC identity only after
  confirming the target interface and current issuer policy; do not assume it
  offers a fund-subscription/redemption API.
- Use Franklin's institutional gBENJI route for investment, redemption and
  transfer eligibility. An authorised on-chain trustline does not replace the
  issuer's legal and operational onboarding requirements.

## Primary sources

- [Franklin Templeton `stellar.toml`](https://www.franklintempleton.com/.well-known/stellar.toml)
- [Franklin BENJI developer address hub](https://digitalassets.franklintempleton.com/benji/benji-contracts/)
- [Stellar Horizon gBENJI asset record](https://horizon.stellar.org/assets?asset_code=gBENJI&asset_issuer=GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP&limit=1)
- [Stellar.expert gBENJI asset](https://stellar.expert/explorer/public/asset/gBENJI-GD5J73EKK5IYL5XS3FBTHHX7CZIYRP7QXDL57XFWGC2WVYWT326OBXRP)
- [Stellar assets: classic versus contract tokens](https://developers.stellar.org/docs/learn/fundamentals/stellar-data-structures/assets)
- [Stellar Asset Contract documentation](https://developers.stellar.org/docs/tokens/stellar-asset-contract)

## iBENJI contract research

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

## Janus Henderson Anemoy Treasury Fund (JTRSY) contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Janus Henderson Anemoy Treasury Fund (`JTRSY`) | Ethereum ERC-20, [`0x8c213ee79581ff4984583c6a801e5263418c4b86`](https://etherscan.io/address/0x8c213ee79581ff4984583c6a801e5263418c4b86#code) | `Tranche`; a direct deployment, **not a proxy** | A 6-decimal, permissionable Centrifuge tranche/share token. It is an ERC-20 with EIP-2612 permit, ERC-1404 transfer-restriction queries, ERC-7575 share-token vault links, and an external compliance-hook callback on mint, burn, and transfer. The token is not itself an ERC-4626/7540 vault or the subscription/redemption contract. | [centrifuge/liquidity-pools](https://github.com/centrifuge/liquidity-pools), [`Tranche.sol`](https://github.com/centrifuge/liquidity-pools/blob/main/src/token/Tranche.sol) | [Centrifuge token compliance](https://docs.centrifuge.io/developer/protocol/features/token-compliance/), [share-token concept](https://docs.centrifuge.io/user/concepts/share-tokens/), [Centrifuge app pool](https://app.centrifuge.io/pool/281474976710660) |

## On-chain identification and verification

Etherscan identifies the address as **Janus Henderson Anemoy Treasury Fund
(JTRSY)**, with six decimals, source code verified, and contract name
`Tranche`. The deployment is also an exact creation- and runtime-bytecode match
in [Sourcify](https://sourcify.dev/server/v2/contract/1/0x8c213ee79581ff4984583c6a801e5263418c4b86).
Sourcify reports the fully qualified source name
`src/token/Tranche.sol:Tranche`, Solidity `0.8.26`, optimiser enabled with 500
runs, Cancun EVM target, deployment block `20460672`, and deployment
transaction
[`0x503224f5…c8ddc742`](https://etherscan.io/tx/0x503224f5582af888011900a2e5dcfbe57a7668de67f8b555ae0d9d3c8ddc742).

Sourcify's proxy-resolution result is `isProxy: false`, with no implementation
contracts. Integrations should therefore use the token address directly rather
than trying to resolve an EIP-1967 implementation.

The verified source tree names the contract `Tranche` and includes Centrifuge's
`Auth`, `ERC20`, `ITranche`, `IHook`, and `IERC7575` components. A public
GitHub search for the contract name and the distinctive `authTransferFrom`,
`setHookData`, and `updateVault` functions finds Centrifuge's public
`liquidity-pools` codebase. That is strong protocol-family evidence. Sourcify's
deployment-specific source is the source of record; do not assume the current
`main` branch is byte-for-byte the historic deployment without a separate build
comparison.

## Contract surface and behaviour

`Tranche` is the share token within Centrifuge's fund architecture. It has no
`asset`, `deposit`, `withdraw`, `redeem`, `requestDeposit`, or
`requestRedeem` function, so it must not be treated as a vault merely because
the wider Centrifuge system supports ERC-4626, ERC-7540, and ERC-7575.
The linked vaults, selected by deposited-asset address, are exposed separately
through `vault(asset)`.

| Area | Material functions | Behaviour |
| --- | --- | --- |
| ERC-20 and permit | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `permit`, `nonces`, `DOMAIN_SEPARATOR` | Standard ERC-20 reads/transfers and EIP-2612-style approvals by signature. `transfer` and `transferFrom` also invoke the configured hook. |
| Issuance and destruction | `mint(to,value)`, `burn(from,value)` | Only an authorised `ward` can mint or burn. The token limits total supply and each packed balance to `uint128`. Mint and burn invoke the same compliance hook as ordinary transfers. |
| Permissioning | `hook`, `file("hook",address)`, `hookDataOf`, `setHookData`, `checkTransferRestriction`, `detectTransferRestriction`, `messageForTransferRestriction` | An optional external `IHook` receives callbacks for standard transfers, minting, burning, and authorised transfers. It can reject the operation. The token also implements ERC-1404-style pre-flight restriction queries. Its per-account `bytes16` hook data is packed alongside the balance. |
| Vault association | `vault(asset)`, `updateVault(asset,vault_)`, `VaultUpdate` | Authorised operations associate a tokenised share class with a vault for a particular ERC-20 asset. This is an ERC-7575 share-token relationship, not evidence that the token contract is the vault. |
| Privileged transfers | `authTransferFrom(sender,from,to,value)` | An authorised `ward` can make an allowance-aware transfer while identifying a logical sender. The compliance hook receives a dedicated authorised-transfer callback. This is used by Centrifuge fund/vault flows. |
| Governance | `wards`, `rely(user)`, `deny(user)`, `file("name",string)`, `file("symbol",string)` | MakerDAO-style `Auth`: any current ward can add or remove wards and administer token metadata; a ward or the hook can set the hook reference/data as allowed by the verified source. |

The transfer hook is central to the security and operational model. If `hook`
is non-zero, `transfer`, `transferFrom`, `mint`, `burn`, and
`authTransferFrom` revert unless the relevant callback returns its expected
selector. `detectTransferRestriction` uses the hook's corresponding view check
and returns `0` (`transfer-allowed`) or `1` (`transfer-blocked`). This is a
generic hook interface: the share-token bytecode alone does not identify the
specific live restriction policy, so integration code should read the live
`hook()` address and inspect it before assuming transfers are unrestricted.

## Fund and protocol conclusion

**Conclusion: Centrifuge liquidity-pools `Tranche` token for the Janus
Henderson Anemoy Treasury Fund — high confidence.**

The conclusion is supported independently by the exact verified source name
and ABI, the Centrifuge public codebase match, Etherscan's JTRSY token label,
and Centrifuge's own [JTRSY announcement](https://centrifuge.io/blog/jtrsy-aa-plus-rating),
which says the fund is powered by Centrifuge and provides on-chain exposure to
short-duration US Treasury bills. Centrifuge's official documentation describes
its share tokens as ERC-20 tokens with ERC-1404 and modular transfer hooks, the
same architecture present in this deployment.

Janus Henderson is the fund's sub-investment manager, while Centrifuge/Anemoy
provide the tokenisation and fund infrastructure. The
[S&P Global report hosted by Centrifuge](https://centrifuge.mypinata.cloud/ipfs/QmQ9P1BuH6mBkN9Gs1aBZo34zX6NYigRZ84nu13Wi52CKC)
describes whitelisted wallet access, token issuance on supported chains, and
USDC subscription/redemption processing. It should not be read as proof that
any wallet can buy, receive, or redeem this ERC-20.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **783,691,014.920462
JTRSY** (6 decimals). The `Tranche` ABI does not expose a share-price or NAV
function; price accrual and valuation belong to the linked Centrifuge fund/vault
and its off-token accounting.

## Integration implications

- Identify JTRSY from the direct `Tranche` token address and standard ERC-20
  fields, but classify it as a **Centrifuge permissioned share token**, not an
  ERC-4626 vault.
- Before attempting a transfer, read `hook()` and either simulate the transfer
  or query `checkTransferRestriction`; transfer eligibility is deliberately
  delegated to the hook and can change with its configuration.
- Use the linked vault / Centrifuge pool route for subscription and redemption
  semantics. Token-level `mint` and `burn` are issuer-authorised accounting
  actions, not public investment entry points.
- Do not derive NAV or yield from `totalSupply`. Centrifuge describes share
  tokens as price-accruing; pricing and investor flows live in the fund/pool
  infrastructure rather than this ERC-20 contract.

## Primary sources

- [Etherscan token and verified source](https://etherscan.io/address/0x8c213ee79581ff4984583c6a801e5263418c4b86#code)
- [Sourcify exact-match record and deployment source](https://sourcify.dev/server/v2/contract/1/0x8c213ee79581ff4984583c6a801e5263418c4b86?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution)
- [Centrifuge `liquidity-pools` public source repository](https://github.com/centrifuge/liquidity-pools)
- [Centrifuge `Tranche.sol` public source](https://github.com/centrifuge/liquidity-pools/blob/main/src/token/Tranche.sol)
- [Centrifuge token-compliance documentation](https://docs.centrifuge.io/developer/protocol/features/token-compliance/)
- [Centrifuge JTRSY fund announcement](https://centrifuge.io/blog/jtrsy-aa-plus-rating)

## My OnChain Net Yield Fund (MONY) contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| My OnChain Net Yield Fund (`MONY`) | Ethereum ERC-20, [`0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46`](https://etherscan.io/address/0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46#code) | `FactTokenDiamond`, an EIP-2535 Diamond dispatcher with 16 live facets | A permissioned ERC-20 fund-share token built from a modular FACT Diamond implementation. The facets provide ERC-20 balances/allowances, token and account activation controls, minting, request-based redemption/burn flows, account stop codes, pausing, role/owner controls, and a timelock. It is not an ERC-4626 vault and has no direct public `deposit`, `withdraw`, or NAV function. | No public J.P. Morgan/Kinexys implementation repository located. The verified Diamond code attributes the architectural pattern to [EIP-2535](https://eips.ethereum.org/EIPS/eip-2535); see the [reference implementation](https://github.com/mudgen/diamond-3). | [J.P. Morgan launch announcement](https://www.prnewswire.com/news-releases/jp-morgan-asset-management-launches-its-first-tokenized-money-market-fund-302642262.html), [RWA.xyz MONY listing](https://app.rwa.xyz/assets/MONY) |

## On-chain identification and verification

J.P. Morgan Asset Management's [launch announcement](https://www.prnewswire.com/news-releases/jp-morgan-asset-management-launches-its-first-tokenized-money-market-fund-302642262.html)
names this exact Ethereum address as MONY's blockchain token address and says
the fund is powered by Kinexys Digital Assets and distributed through Morgan
Money. RWA.xyz independently identifies it as MONY's Ethereum ERC-20 token.

The address is an exact creation- and runtime-bytecode match in
[Sourcify](https://sourcify.dev/server/v2/contract/1/0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution).
The deployed dispatcher is `FactTokenDiamond`
(`contracts/FactTokenDiamond.sol:FactTokenDiamond`), compiled with Solidity
`0.8.17`; Sourcify classifies it as a direct deployment, not an EIP-1967 proxy.
That does **not** make its behaviour immutable: the dispatcher's verified
source sends each function selector to a facet with `delegatecall`, and its
Diamond-cut mechanism can add, replace, or remove selectors.

Unlike a conventional ERC-20 contract, the Diamond dispatcher's own verified
ABI is empty because its callable surface is supplied by facets. The live
`facets()` view returned 16 facet contracts during this check. All 16 were
separately source-verified as exact matches by Sourcify.

| Live facet | Purpose |
| --- | --- |
| [`ERC20FacetTransferBurnableStoppable`](https://etherscan.io/address/0x1aa6cf30f6b4f18f5cf12cc0f2c8543eaabe62d6#code) | ERC-20 `name`, `symbol`, `decimals`, supply, balance, allowance, approval, `transfer`, and `transferFrom`, with active-account, pause, token-active, stop-code, and burner-address checks. |
| [`ManagedAccountFacet`](https://etherscan.io/address/0x71ff8c0e761b7640c1e294bf9b1b05b3a7df77a6#code) | Activates/deactivates accounts; locks/unlocks balances; and permits account-admin `forceTransfer` and `forceBurn` operations. |
| [`MintableFacet`](https://etherscan.io/address/0xe70c597957e542209ed44f2fbae58370089e59be#code) | `TOKENIZATION_AGENT`-controlled, request-ID-based minting to an active account. |
| Burn facets | [`BurnPreparableFacet`](https://etherscan.io/address/0x981b3af7957841ce2c8869ee25055a693a89006b#code), [`BurnRespondableFacet`](https://etherscan.io/address/0x8c2fbc1df5918cb15b96ad4d57298988c73dac32#code), and [`BurnableFacet`](https://etherscan.io/address/0x88183f91a0a7315bd3e2102be7520c88ce9eff67#code) implement prepare/approve/reject/execute burn requests. A transfer to an address with the `BURNER` role creates a burn request. |
| [`StopCodesFacet`](https://etherscan.io/address/0x996440281f77b4d664878b0b14a10e909fc3310b#code) | `ACCOUNT_ADMIN` can set per-account controls that stop transfers from/to an account or redemption from it. |
| Pause/timelock facets | [`PausableFacetTimelockable`](https://etherscan.io/address/0xb1d84e1289942e45caa9c1103cbc867104f5f087#code), [`ManagedTokenFacetTimelockable`](https://etherscan.io/address/0xefbd67c1a8bc587599a475f720783282724e83b5#code), [`AccessControlFacetTimelockable`](https://etherscan.io/address/0xe79a30d2a2134fccecbfb9a79f39e6ab1637b3f#code), and [`TimelockableFacet`](https://etherscan.io/address/0xca76e9791de59331219bba4be1d05404eda8764c#code) provide pause/unpause, token activation, selected role changes, and queued delayed operations. |
| Diamond/ownership facets | [`DiamondCutFacet`](https://etherscan.io/address/0x6e4d1a8b0a6b2ed26c50873c2c75feac77385295#code), [`DiamondLoupeFacet`](https://etherscan.io/address/0x4d9832828a3898c9fcaf8799cacdcf709068ba24#code), [`ERC173FacetTimelockable`](https://etherscan.io/address/0x7d7d5c1c2c9d45f6c73db9ccf6236ceb84d71965#code), and [`ERC165Facet`](https://etherscan.io/address/0x66f853ea787118985be98a7f3a071b9fe1390231#code) support Diamond upgrades/introspection, two-step ownership, and interface registration/querying. |
| [`TokenMetadataFacet`](https://etherscan.io/address/0x5be781f803552a0532fb03f6513d5a4bacb665a9#code) | Token-admin metadata read/write path. |

## Contract surface and behaviour

| Area | Material functions | Behaviour |
| --- | --- | --- |
| ERC-20 | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom` | Standard ERC-20 fields and allowance mechanics, routed through a facet. Transfers require the token to be active and unpaused and the relevant accounts to be active. |
| Investor/account controls | `accountActive`, `activateAccount`, `deactivateAccount`, batch variants, `lock`, `unlock`, `lockBalanceOf`, `forceTransfer`, `forceBurn` | The account-admin role controls which accounts are active, can lock their token balance, and has forced movement/burn powers. These are issuer/transfer-agent controls, not ordinary investor actions. |
| Issuance | `mint(bytes32,address,uint256)` | A `TOKENIZATION_AGENT` mints against a request ID, only while the token is active/unpaused and the recipient account is active. |
| Redemption/burn workflow | `prepareBurn`, `burnRequestOf`, `approveBurn`, `rejectBurn`, `burn` | The Diamond supports asynchronous/request-based burn processing. Sending tokens to a `BURNER` address creates a burn request; authorised agents can approve/reject and execute the burn. |
| Transfer restrictions | `updateStopCodes`, `getStopCodes` | Per-account stop codes can prevent outgoing transfers, incoming transfers, or redemptions. They supplement active-account and pause checks. |
| Governance and upgrades | `grantRole`, `renounceRole`, `owner`, `transferOwnership`, `acceptOwnership`, `diamondCut`, `facets`, `facetAddress`, `setDelay`, `queueTransaction`, `cancelTransaction`, `isExecutable` | Ownership/roles and the Diamond cut control the executable implementation. Selected role/ownership/token-activation actions use timelock facets; `diamondCut` remains a separately privileged code-upgrade surface. |

## Known protocol and fund conclusion

**Conclusion: J.P. Morgan/Kinexys MONY fund token using a proprietary FACT
implementation built on the EIP-2535 Diamond standard — high confidence.**

The issuer and platform attribution comes from J.P. Morgan's announcement and
the RWA.xyz listing. The technical conclusion comes directly from the exact
verified source: `FactTokenDiamond`, `DiamondCut`, the `facets()` interface,
and 16 verified FACT-named facets. This is not a standard ERC-4626 vault,
Kinexys/JLTXX's Quorum deployment, or a generic Securitize/Centrifuge token.

GitHub and web searches for `FactTokenDiamond`, `fact.diamond.storage`, the
deployed facet names, and the imported `@odaplatform/da-fact-smartcontracts`
package did not locate an issuer-maintained public source repository. The
verified Etherscan/Sourcify packages are therefore the reproducible source
reference. The EIP-2535 reference implementation is pattern context, not
evidence of J.P. Morgan code provenance.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **102,080,329.02 MONY**
(4 decimals). The `FactTokenDiamond` ABI has no direct share-price or NAV
function; use Kinexys/issuer NAV and distribution records rather than treating
the token supply as a valuation input.

## Integration implications

- Track MONY as an EIP-2535 Diamond whose ERC-20 functions are facets. Do not
  model the dispatcher alone as a simple immutable ERC-20 or an ERC-4626 vault.
- Discover the live facet map with `facets()`/`facetAddress(bytes4)` at
  integration time and monitor `DiamondCut` events. A facet replacement can
  change token behaviour without moving the token address.
- Expect issuer-controlled active-account, stop-code, lock, forced-transfer,
  and forced-burn restrictions. A successful ERC-20 ABI decode does not mean
  an arbitrary wallet may receive, transfer, or redeem MONY.
- MONY's fund subscription/redemption is handled through Morgan Money and the
  issuer's operational workflow. The token's request-based burn functions do
  not independently establish public redemption eligibility or settlement.
- Use the issuer/platform's NAV and distribution records rather than deriving
  fund value from `totalSupply`; the dispatcher exposes no on-chain NAV oracle.

## Primary sources

- [Etherscan MONY Diamond dispatcher](https://etherscan.io/address/0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46#code)
- [Sourcify exact-match MONY Diamond source package](https://sourcify.dev/server/v2/contract/1/0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution)
- [J.P. Morgan Asset Management launch announcement](https://www.prnewswire.com/news-releases/jp-morgan-asset-management-launches-its-first-tokenized-money-market-fund-302642262.html)
- [RWA.xyz MONY asset listing](https://app.rwa.xyz/assets/MONY)
- [EIP-2535 Diamond standard](https://eips.ethereum.org/EIPS/eip-2535)
- [EIP-2535 reference implementation](https://github.com/mudgen/diamond-3)

## Ondo Short-Term US Government Treasuries (OUSG) contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Ondo Short-Term US Government Treasuries (`OUSG`) | Ethereum ERC-20 proxy, [`0x1B19C19393e2d034D8Ff31ff34c81252FcBbee92`](https://etherscan.io/address/0x1b19c19393e2d034d8ff31ff34c81252fcbbee92#code) | `TokenProxy` (EIP-1967 `TransparentUpgradeableProxy`); active implementation [`CashKYCSenderReceiver`](https://etherscan.io/address/0x1ceb44b6e515abf009e0ccb6ddafd723886cf3ff#code) at `0x1CEB44b6E515aBf009E0CCb6ddaFD723886cf3Ff` | A non-rebasing, NAV-appreciating, permissioned ERC-20 fund-share token. The implementation uses OpenZeppelin's upgradeable ERC-20/access-control/pause components and requires the transaction sender, source holder, and destination holder to satisfy Ondo's configured KYC registry. Separate Ondo contracts provide the price oracle and subscriptions/redemptions. | [Ondo public contract repository](https://github.com/ondoprotocol/usdy) (documents the shared Ondo RWAHub architecture); source-specific historic repository link in Ondo’s address registry now returns 404, so use the verified explorer/Sourcify source for this deployed implementation. | [OUSG overview](https://docs.ondo.finance/qualified-access-products/ousg/overview), [official address registry](https://docs.ondo.finance/addresses), [technical page](https://docs.ondo.finance/qualified-access-products/ousg/technical) |

## On-chain identification and verification

Etherscan labels the proxy as **Ondo Finance: OUSG Token**, identifies its
active implementation as `0x1CEB…f3Ff`, and records two proxy upgrades. The
proxy source is verified; it is `TokenProxy`, a thin wrapper around
OpenZeppelin's `TransparentUpgradeableProxy`. The current implementation is
`CashKYCSenderReceiver` at
[`0x1CEB44b6E515aBf009E0CCb6ddaFD723886cf3Ff`](https://etherscan.io/address/0x1ceb44b6e515abf009e0ccb6ddafd723886cf3ff#code).

Both contracts are exact creation- and runtime-bytecode matches in Sourcify:

| Contract | Sourcify finding |
| --- | --- |
| `TokenProxy` | [Exact match](https://sourcify.dev/server/v2/contract/1/0x1b19c19393e2d034d8ff31ff34c81252fcbbee92?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution), compiler `0.8.16`, optimiser 100 runs; proxy resolution identifies an EIP-1967 proxy and the active `CashKYCSenderReceiver` implementation. |
| `CashKYCSenderReceiver` | [Exact match](https://sourcify.dev/server/v2/contract/1/0x1CEB44b6E515aBf009E0CCb6ddaFD723886cf3Ff?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution), compiler `0.8.16`, optimiser 100 runs; fully qualified name `contracts/cash/token/CashKYCSenderReceiver.sol:CashKYCSenderReceiver`. |

The implementation source includes `KYCRegistryClientInitializable` and
OpenZeppelin `ERC20PresetMinterPauserUpgradeable`,
`AccessControlEnumerableUpgradeable`, and `PausableUpgradeable`. This is a
token proxy/implementation design, not an ERC-4626 vault contract. Its ABI has
no `asset`, `totalAssets`, `deposit`, `withdraw`, `redeem`, or
`convertToShares` function.

## Contract surface and behaviour

| Area | Material functions | Behaviour |
| --- | --- | --- |
| ERC-20 | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `increaseAllowance`, `decreaseAllowance` | Standard ERC-20 interface, but token movement is subject to the KYC check below. |
| KYC configuration | `kycRegistry`, `kycRequirementGroup`, `setKYCRegistry`, `setKYCRequirementGroup` | A holder must satisfy the configured registry and requirement group. Only `KYC_CONFIGURER_ROLE` can change either reference. |
| Transfer gate | inherited `transfer`, `transferFrom`, plus the implementation's `_beforeTokenTransfer` | The verified implementation requires the transaction sender to be KYC'd. For a transfer, both `from` and `to` must also be KYC'd; for mint and burn it omits the zero address as appropriate. Thus, an unapproved delegated-transfer operator cannot use an otherwise valid allowance. |
| Supply control | `mint(to,amount)`, `burn(amount)`, `burnFrom(account,amount)`, `burn(from,amount)` | `MINTER_ROLE` controls minting through the inherited preset. Holders can use normal burn methods subject to allowance rules; the additional two-argument `burn` permits a `BURNER_ROLE` account to destroy another holder's balance. |
| Administration and emergency control | `grantRole`, `revokeRole`, `renounceRole`, role-member enumeration, `pause`, `unpause`, `paused` | Standard OpenZeppelin role administration. `PAUSER_ROLE` can stop token operations; privileged control must be included in any operational-risk assessment. |
| Proxy administration | `admin`, `implementation`, `changeAdmin`, `upgradeTo`, `upgradeToAndCall` | `TokenProxy` administration surface, not ordinary token methods. Resolve the implementation at integration time because it can change. |

OUSG accrues value through its on-chain NAV price rather than rebasing token
balances. Ondo describes the daily price update and price-per-token calculation
in its [OUSG overview](https://docs.ondo.finance/qualified-access-products/ousg/overview).
Therefore, `totalSupply` is not a sufficient valuation input.

## Known protocol and fund conclusion

**Conclusion: Ondo's proprietary qualified-access fund-token system — high
confidence.**

The proxy's Etherscan Ondo label, the exact verified source's Ondo-specific
KYC client, and Ondo's official address registry identify this as the OUSG
token. GitHub/web research finds the same KYC-token and RWA subscription
architecture in Ondo's public code and documentation, rather than a deployment
of an external fund standard such as ERC-4626, Centrifuge, Securitize, or
Superstate.

The public [Ondo repository](https://github.com/ondoprotocol/usdy) describes
the historical shared `RWAHub` subscription/redemption system for OUSG, OMMF,
and USDY, including KYC checks and price-oracle-mediated mint/redemption
requests. The current official address page has a direct GitHub link for the
historic `CashKYCSenderReceiver` source path, but that target
(`ondoprotocol/tokenized-funds`) was unavailable during this check. The
deployment-specific exact-match source on Etherscan and Sourcify is therefore
the reproducible reference for `CashKYCSenderReceiver`; the live address and
functionality are not inferred from the missing GitHub path.

## Related live contracts

These contracts clarify the current integration path; they are not substitutes
for the OUSG ERC-20 address.

| Contract | Ethereum address | Role |
| --- | --- | --- |
| `OUSG_InstantManager` | [`0x93358db73B6cd4b98D89c8F5f230E81a95c2643a`](https://etherscan.io/address/0x93358db73B6cd4b98D89c8F5f230E81a95c2643a#code) | Current official instant subscription/redemption entry point. Its verified ABI exposes `subscribe` and `redeem`, accepted-token configuration, pricing, fee, rate-limit, compliance, and Ondo-ID-registry references. |
| `OndoIDRegistry` | [`0xcf6958D69d535FD03BD6Df3F4fe6CDcd127D97df`](https://etherscan.io/address/0xcf6958D69d535FD03BD6Df3F4fe6CDcd127D97df#code) | The official registry page says this stores addresses that can hold OUSG. |
| `OndoOracle` | [`0x9Cad45a8BF0Ed41Ff33074449B357C7a1fAb4094`](https://etherscan.io/address/0x9cad45a8bf0ed41ff33074449b357c7a1fab4094#code) | The official registry identifies this as the unified price-data interface required by Ondo contracts. |

Ondo's documentation says instant minting and redemption is available only on
Ethereum mainnet, even though OUSG has representations on Polygon, Solana, and
XRP Ledger. Eligibility/onboarding remains a precondition for holding,
transferring, investing, or redeeming the qualified-access token.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned
**1,580,534.087618339562150913 OUSG** (18 decimals). The token ABI has no
direct share-price method; use the separately deployed issuer `OndoOracle` and
manager route documented below for the daily NAV price.

## Integration implications

- Track the proxy address as the OUSG ERC-20, but resolve and monitor the
  active EIP-1967 implementation at runtime.
- Do not model OUSG as an ERC-4626 vault. Read the official on-chain price
  oracle/manager route for NAV and use the manager for issuer-supported
  subscription/redemption flows.
- Treat every transfer as permissioned: the sender, holder, and recipient KYC
  requirements apply at the token level, while the manager adds separate
  onboarding, compliance, fee, accepted-asset, and rate-limit conditions.
- The ordinary ERC-20 ABI does not establish that a wallet can receive or
  redeem OUSG. Perform the issuer's applicable onboarding and simulate the
  intended call path before integrating a flow.

## Primary sources

- [Etherscan OUSG proxy and proxy-history view](https://etherscan.io/address/0x1b19c19393e2d034d8ff31ff34c81252fcbbee92#code)
- [Etherscan `CashKYCSenderReceiver` implementation](https://etherscan.io/address/0x1ceb44b6e515abf009e0ccb6ddafd723886cf3ff#code)
- [Sourcify exact-match proxy record](https://sourcify.dev/server/v2/contract/1/0x1b19c19393e2d034d8ff31ff34c81252fcbbee92?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution)
- [Sourcify exact-match implementation record](https://sourcify.dev/server/v2/contract/1/0x1CEB44b6E515aBf009E0CCb6ddaFD723886cf3Ff?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution)
- [Ondo official address registry](https://docs.ondo.finance/addresses)
- [Ondo OUSG overview](https://docs.ondo.finance/qualified-access-products/ousg/overview)
- [Ondo OUSG technical documentation](https://docs.ondo.finance/qualified-access-products/ousg/technical)
- [Ondo public RWA contract repository](https://github.com/ondoprotocol/usdy)

## State Street Galaxy Onchain Liquidity Sweep Fund (SWEEP) contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| State Street Galaxy Onchain Liquidity Sweep Fund (`SWEEP`) | Solana at launch; **no public mint address or Solana program address discovered** | Not publicly disclosed | A tokenised private-liquidity-fund product operated using Galaxy Digital Infrastructure. Official material identifies the commercial lifecycle (PYUSD/USDC subscriptions, PYUSD/USD redemptions and a non-rebasing token) but does not disclose the token mint, token-program variant, transfer-control programme, or issuance/redemption programme. | No public Galaxy/State Street SWEEP smart-contract repository was found in GitHub/web searches. Galaxy describes its tokenisation technology as proprietary. | [Galaxy SWEEP fund page](https://am.galaxy.com/galaxy-state-street-sweep-fund), [State Street launch release](https://investors.statestreet.com/investor-news-events/press-releases/news-details/2026/State-Street-Investment-Management-and-Galaxy-Digital-Bring-Cash-Management-Onchain/default.aspx), [Galaxy tokenisation overview](https://www.galaxy.com/tokenization) |

## What is publicly established

State Street and Galaxy officially launched SWEEP on 5 May 2026 as a tokenised
private liquidity fund. The State Street release says that Galaxy's Digital
Infrastructure provides the tokenisation technology for issuing and managing
SWEEP tokens; it also names Anchorage as digital custodian, NAV Consulting as
transfer agent, Chainlink NAVLink for daily on-chain NAV publication, and
Chainlink CCIP for planned cross-chain interoperability.

Galaxy's fund page says the fund launched on Solana, is non-rebasing, accepts
PYUSD and USDC subscriptions at any time (and USD on business days), and permits
daily USD redemption or 24/7 PYUSD redemption subject to portfolio availability.
The stated next-chain plans are Stellar and Ethereum. These sources establish
that a Solana on-chain token exists, but they do not publish the Solana mint or
a programme address.

## Mint and programme discovery result

**No attributable SWEEP mint or contract/programme address is publicly
discoverable as of the check date.**

The official fund page, State Street release, Galaxy tokenisation page, Galaxy
Digital Assets Portal link, and the public Form D records were checked. None
states a Solana base58 mint address, transaction signature, token account,
programme ID, explorer link, or an SPL Token versus Token-2022 choice. The
issuer instead directs prospective users to a Qualified Purchaser-gated Digital
Assets Portal.

Focused public web searches combining the complete fund name, `SWEEP`,
`Solana`, `mint`, `token address`, `Solscan`, `program`, and
`contract address` found launch reporting but no attributable mint. The same
search terms on GitHub found no public Galaxy/State Street SWEEP Solidity, Rust,
Anchor, or Solana-program repository. Searches for the distinctive issuer
phrases `Galaxy Digital Infrastructure` and `SWEEP tokens` likewise led
only to issuer marketing/press material.

This negative result is material: without the mint address, it is not possible
to query a Solana explorer for the token's owner programme, mint authority,
freeze authority, Token-2022 extensions, transfer-hook configuration, metadata,
supply, or transactions. It would be unsafe to infer a mint from an unrelated
ticker-matched Solana token.

## Smart-contract/framework conclusion

**Conclusion: proprietary Galaxy tokenisation infrastructure, with the
deployed SWEEP token/programme intentionally not publicly identified — high
confidence for the negative discovery result.**

Galaxy describes its in-house tokenisation platform as proprietary technology
for compliant digital tokens. The SWEEP announcement attributes issuance and
management to Galaxy Digital Infrastructure, rather than identifying a public
framework such as SPL Token, Token-2022, Metaplex, Superstate FundOS,
ERC-3643/T-REX, or a named open-source Solana programme. No evidence supports
assigning SWEEP to any of those frameworks.

This is unlike Galaxy's separate tokenised `GLXY` equity product: Galaxy
publishes that product's exact Solana token address and identifies Superstate
as transfer agent. The absence of equivalent SWEEP disclosure should be treated
as deliberate lack of public contract metadata, not evidence that the GLXY
mint, Superstate contracts, or any other Galaxy-associated Solana token belong
to SWEEP.

The publicly documented product-level controls are:

| Publicly stated component | Publicly stated role | On-chain address available? |
| --- | --- | --- |
| Galaxy Digital Infrastructure | Tokenisation technology; issuance and management of SWEEP tokens | No |
| SWEEP Solana token | Non-rebasing representation of fund units at launch | No mint or token account disclosed |
| Chainlink NAVLink | Publication of daily NAV on-chain | No SWEEP-linked feed/address disclosed |
| Chainlink CCIP | Intended secure cross-chain interoperability | No SWEEP-linked sender/receiver disclosed |
| Anchorage / NAV Consulting | Digital custody / transfer agency | Service providers, not disclosed Solana programmes |

## On-chain supply and ABI price availability

Not available. The issuer has not disclosed a SWEEP Solana mint or programme,
so neither the mint supply nor an ABI/programme share-price interface can be
queried safely.

## Integration implications

- Do not add a guessed SPL mint, generic `SWEEP` ticker result, or Galaxy's
  separate GLXY token address to production metadata.
- Classify SWEEP as **tracked fund, contract address unknown/not publicly
  disclosed** until the issuer provides a mint address through a trustworthy
  source or an authorised investor supplies a verifiable on-chain transaction.
- Once a mint is obtained, independently verify it against an issuer-controlled
  source, inspect it in [Solana Explorer](https://explorer.solana.com/) or
  [Solscan](https://solscan.io/), and record its owner programme, authorities,
  Token-2022 extensions and transfer restrictions before treating it as
  transferable collateral.
- The press release makes clear that the fund is a private placement for
  eligible Qualified Purchasers. A publicly visible mint, if subsequently
  supplied, would not itself establish transfer or redemption eligibility.

## Primary sources

- [State Street launch release](https://investors.statestreet.com/investor-news-events/press-releases/news-details/2026/State-Street-Investment-Management-and-Galaxy-Digital-Bring-Cash-Management-Onchain/default.aspx)
- [Galaxy SWEEP fund page](https://am.galaxy.com/galaxy-state-street-sweep-fund)
- [Galaxy tokenisation overview](https://www.galaxy.com/tokenization)
- [SEC Form D: SWEEP onshore fund](https://www.sec.gov/Archives/edgar/data/2130185/000090266426002249/xslFormDX01/primary_doc.xml)
- [Solana Explorer](https://explorer.solana.com/) and [Solscan](https://solscan.io/) (no SWEEP mint was supplied by the issuer to query)

## thBILL contract research

Checked on 2026-07-17. This note identifies the public Ethereum fund-token
deployment and technical interface; it is not an assessment of investor
eligibility, legal ownership, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Theo Short Duration US Treasury Fund (thBILL) | Ethereum ERC-20 proxy, [`0x5FA487BCa6158c64046B2813623E20755091DA0b`](https://etherscan.io/address/0x5fa487bca6158c64046b2813623e20755091da0b#code) | `ERC1967Proxy`; current verified implementation [`IToken`](https://etherscan.io/address/0x325478a069b0dbbdfbee909fa3741f84259ba519#code) at `0x325478A069b0DBBdFbeE909FA3741f84259Ba519` | Theo's proprietary **iToken**: an upgradeable multi-asset index vault. It extends ERC-4626-style share accounting to deposits and withdrawals expressed as arrays of approved basket assets, with configurable target ratios. It is not a conventional single-asset ERC-4626 vault. | No public issuer repository containing the deployed `IToken` source was found. [Theo's GitHub organisation](https://github.com/theo-network) publishes audit material but not the core iToken code. | [thBILL product documentation](https://docs.theo.xyz/thbill), [tToken and iToken technical reference](https://docs.theo.xyz/technical-reference/ttokens-and-itokens), [official deployments](https://docs.theo.xyz/technical-reference/deployments), [Zenith audit report](https://github.com/zenith-security/reports/blob/main/reports/Theo%20-%20Zenith%20Audit%20Report.pdf) |

## On-chain verification

Theo's official deployment registry identifies
[`0x5FA487BCa6158c64046B2813623E20755091DA0b`](https://etherscan.io/address/0x5fa487bca6158c64046b2813623e20755091da0b#code)
as the Ethereum thBILL token. Etherscan identifies it as an EIP-1967 proxy and
resolves its current implementation to
[`0x325478A069b0DBBdFbeE909FA3741f84259Ba519`](https://etherscan.io/address/0x325478a069b0dbbdfbee909fa3741f84259ba519#code).

Etherscan source-verifies the implementation as an **Exact Match** named
`IToken`, compiled with Solidity `0.8.28` and 200 optimiser runs. The token
proxy itself is a **Similar Match** for OpenZeppelin's `ERC1967Proxy` source.
The verified implementation source tree includes Theo's `IToken`, an
`ERC4626UpgradeableMultiAsset` vault base, multi-asset ERC-4626 interfaces,
and OpenZeppelin access control, pausing, ERC-20, and UUPS upgrade components.

No Sourcify full- or partial-match metadata package was located by public
address search for either the proxy or the current implementation. The
deployment-specific source reference is therefore Etherscan's exact verified
implementation, rather than a public Git commit.

## Contract surface and behaviour

| Area | Functions | Effect |
| --- | --- | --- |
| ERC-20 shares | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom` | thBILL is an ERC-20 share token at the proxy address. |
| Multi-asset vault accounting | `asset`, `totalUnderlyingAssets`, `totalDepositAssets`, `convertToAssets`, `convertToShares`, `convertToDepositAssets`, `previewDeposit`, `previewMint`, `previewRedeem`, `previewWithdraw` | Calculates shares and basket-asset amounts. Unlike canonical ERC-4626, the conversion methods can return or accept arrays of assets and amounts. |
| Deposits and withdrawals | `deposit(address[],uint256[],address)`, `mint`, `withdraw`, `redeem` | Mints/burns index shares against the approved basket. The `Deposit` and `Withdraw` events also include arrays of deposit or withdrawal assets and amounts. |
| Basket composition | `depositAssetsList`, `isSupportedDepositAsset`, `getAssetRatio`, `getConfig`, `setConfig`, `updateDepositAssets` | Maintains the eligible assets and their target ratios, including enforced-ratio, maximum-deviation, and minimum-share settings. |
| Safety and administration | `pause`, `unpause`, `rescueAsset`, `setEmergencyRole`, `DEFAULT_ADMIN_ROLE`, `EMERGENCY_ROLE` | Privileged operators can halt the vault, rescue non-basket assets, and alter emergency-role configuration. |
| Upgrade control | `upgradeToAndCall`, `proxiableUUID` | The implementation contains UUPS upgrade machinery; the externally held token is also an EIP-1967 proxy. Upgrades must be treated as mutable operational state. |

Theo documents iTokens as indexes that can hold multiple Theo tTokens and/or
other iTokens. thBILL is documented as using this iToken standard; at launch,
its basket contained tULTRA, a wrapped representation of the Libeara/Wellington
short-duration Treasury-bill product. Theo also documents KYC for direct
minting and redemption, and says fund redemptions are settled in USDC. Those
product rules are distinct from the raw ERC-20 interface and must be confirmed
with the issuer for an intended transaction.

## Protocol-family conclusion

**Conclusion: Theo's own iToken standard (high confidence), a custom
multi-asset ERC-4626 extension, rather than a generic single-asset
ERC-4626 implementation.**

The match is direct: Theo's technical reference defines iTokens as index
tokens, the thBILL page says that the fund uses the iToken standard, and the
active Etherscan-verified `IToken` source derives its vault model from
`ERC4626UpgradeableMultiAsset`. The distinctive ABI—array-valued deposit and
withdrawal asset methods, configurable basket ratios, and asset-list
management—matches an index/basket vault rather than an OpenZeppelin-style
single-asset ERC-4626 share contract.

An exact public source repository for the deployed `IToken` was not found.
GitHub searches for the implementation name and its distinctive
`ERC4626UpgradeableMultiAsset`/`ITheoWhitelist` source families did not locate
a public Theo core-contract repository. The public audit report and Etherscan
verification substantiate the family identification; they do not establish a
specific public source commit for this deployment.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **97,323,026.986138
thBILL** (6 decimals). The multi-asset iToken ABI has conversion methods such
as `convertToAssets` and `totalUnderlyingAssets`, but no single scalar
share-price/NAV accessor; valuation requires the current basket assets, ratios
and their prices.

## Integration implications

- Classify thBILL as a **Theo iToken multi-asset vault**. Do not assume that a
  generic integration built only around canonical `IERC4626.asset()` and
  scalar `deposit`/`withdraw` return values handles it correctly.
- For basket valuation and composition, use the multi-asset conversion,
  `depositAssetsList`, ratio, and `totalUnderlyingAssets` views. A single
  ERC-20 balance or `totalSupply` does not show the asset mix or NAV.
- Direct issue/redemption is documented as KYC-gated and operationally settled
  through Theo. Public ABI visibility does not imply that an arbitrary wallet
  can complete a compliant mint or redemption.
- Resolve the EIP-1967 implementation at integration time and monitor proxy
  and UUPS upgrades, pausing, emergency role changes, and basket configuration
  changes.
- Theo documents thBILL as an OFT on Arbitrum, Base, and HyperEVM at
  `0xfdd22ce6d1f66bc0ec89b20bf16ccb6670f55a5a`. Treat those as cross-chain
  representations of the fund token, not independent fund products when
  aggregating supply or AUM.

## Primary sources

- [Etherscan thBILL token proxy](https://etherscan.io/address/0x5fa487bca6158c64046b2813623e20755091da0b#code)
- [Etherscan verified `IToken` implementation](https://etherscan.io/address/0x325478a069b0dbbdfbee909fa3741f84259ba519#code)
- [Theo thBILL product documentation](https://docs.theo.xyz/thbill)
- [Theo tToken and iToken technical reference](https://docs.theo.xyz/technical-reference/ttokens-and-itokens)
- [Theo deployment registry](https://docs.theo.xyz/technical-reference/deployments)
- [Zenith's Theo iToken/tToken audit report](https://github.com/zenith-security/reports/blob/main/reports/Theo%20-%20Zenith%20Audit%20Report.pdf)

## ULTRA Arbitrum contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, or smart-contract security.

## Identification

| Field | Finding |
| --- | --- |
| Fund | Delta Wellington Ultra Short Treasury On-Chain Fund |
| Token | `ULTRA` — tokenised fund units |
| Researched deployment | Arbitrum One (the primary chain in the supplied fund table) |
| Token / proxy | [`0xc26af85ede9cc25d449bcebef866bb85afd5d346`](https://arbiscan.io/address/0xc26af85ede9cc25d449bcebef866bb85afd5d346#code) |
| Proxy contract | `ERC1967Proxy` (OpenZeppelin) |
| Active implementation | [`0x0bd2267bAe2729150b29eD374A7CC73197d1fFE2`](https://arbiscan.io/address/0x0bd2267bAe2729150b29eD374A7CC73197d1fFE2#code) — `Ultra` |
| Token decimals | 6, set by `Ultra.decimals()` |
| Verification | Sourcify reports an **exact** creation- and runtime-bytecode match for the proxy and active implementation. The proxy resolves to `Ultra`; its source tree is Solidity `0.8.19`, includes `contracts/Ultra/Ultra.sol`, and imports OpenZeppelin upgradeable modules. |

The [issuer announcement](https://libeara.com/libeara-partners-with-wellington-and-fundbridge-capital-to-launch-a-u-s-treasuries-fund-tokenised-on-public-blockchain/)
identifies ULTRA as the Delta Wellington fund, with Libeara's Delta platform
handling subscription, issuance, transfer, and redemption. It initially
launched on Ethereum and was intended to expand to Arbitrum, Avalanche, and
Solana; this note examines the supplied Arbitrum deployment. The
[RWA catalogue](https://app.rwa.xyz/assets/ULTRA) lists both the Arbitrum and
Ethereum (`0x50293dd8889b931eb3441d2664dce8396640b419`) representations.

## Contract surface

`Ultra` is a bespoke, upgradeable, six-decimal ERC-20 fund-unit contract. It
combines OpenZeppelin `ERC20BurnableUpgradeable`, enumerable access control,
and UUPS upgrades with issuer-defined KYC, clawback, and transfer-settlement
logic. It is not an ERC-4626 vault.

| Function group | Functions | Description |
| --- | --- | --- |
| ERC-20 | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `increaseAllowance`, `decreaseAllowance`, `transfer`, `transferFrom`, `burn`, `burnFrom` | Standard ERC-20 / burnable surface. `transfer` and `transferFrom` are overridden by the fund-specific controls below. |
| KYC gate | `KYCContract`, `setKYC` | Before a transfer, the token calls the configured `KYCUltra.isKYC` for sender and recipient. The clawback-wallet recipient is expressly exempted from the recipient check. |
| Issuance and administration | `mint` (two overloads), `completeTransfer`, `MINTER_ADMIN`, `setUltraManager` | The `MINTER_ADMIN` role mints units and can complete a transfer. An operator configures the external `UltraManager` used in settlement calculations. |
| Large-transfer workflow | `transfer`, `transferFrom`, `transferFromManager`, `transferLimit`, `transferLimitPeriod`, `setTransferLimit`, `setTransferLimitPeriod` | Transfers are converted using the manager's latest exchange rate and tracked over a configurable period. Above the configured limit, units are sent to the manager and it creates a transfer promise instead of immediately delivering them to the recipient. A `MINTER_ADMIN` can later transfer through `transferFromManager`. |
| Clawback | `clawback`, `setClawbackWallet` | The `SUPER_ADMIN` role can transfer units from an account to the configured clawback wallet and emits `TokensClawedBack`. |
| Roles and upgrade | `DEFAULT_ADMIN_ROLE`, `grantRole`, `revokeRole`, `getRoleMember`, `upgradeTo`, `upgradeToAndCall`, `proxiableUUID` | Enumerable role management plus UUPS upgrade authority. `_authorizeUpgrade` requires `DEFAULT_ADMIN_ROLE`. |

The exact verified source tree also contains `UltraManager`, `KYCUltra`, and
their interfaces. `UltraManager` is a separate, UUPS-upgradeable,
role-controlled request processor: its public source defines epoch-based
`requestMint`, `resolveDeposit`, `requestRedemption`, `completeRedemptions`,
NAV/exchange-rate setting, pause controls, and transfer-promise fulfilment or
refund. The ULTRA token calls that manager for exchange-rate and promise
handling, but the manager address is configurable token state; resolve and
inspect the live manager separately before integrating those flows.

The ULTRA ABI does not contain ERC-4626 `asset`, `deposit`, `mint` with asset
input, `withdraw`, `redeem`, `convertToShares`, `convertToAssets`, or
`totalAssets` methods. **ULTRA is therefore a permissioned tokenised-fund
unit with a separate subscription/redemption manager, not an ERC-4626 vault.**

## Protocol-family conclusion

**Conclusion: bespoke Libeara Delta / ULTRA token-and-manager system built on
OpenZeppelin upgradeable primitives — high confidence.**

The active implementation's exact verified name is `Ultra`, and its source
contains purpose-built `Ultra`, `UltraManager`, and `KYCUltra` contracts. The
contract names, KYC gate, issuer roles, clawback, exchange-rate-based transfer
limit, and transfer-promise workflow do not match a public tokenised-fund
standard such as ERC-4626 or CMTAT. They do match the issuer's public
description of the Delta platform operating subscription, issuance, transfer,
and redemption.

GitHub and web searches for the exact contract names and distinctive functions
(`UltraManager`, `lastSetMintExchangeRate`, and `createTransferPromise`) found
explorer/source records but no public Libeara or fund-specific source
repository. The identifiable public dependency is OpenZeppelin's standard
upgradeable-contract library; it is a code dependency, not the ULTRA protocol
or issuer.

## Public source, GitHub, and documentation

| Resource | Link | Finding |
| --- | --- | --- |
| Token-proxy explorer | [Arbiscan: `0xc26…D346`](https://arbiscan.io/address/0xc26af85ede9cc25d449bcebef866bb85afd5d346#code) | OpenZeppelin ERC-1967 proxy for the Arbitrum token. |
| Implementation explorer | [Arbiscan: `0x0bd2…fFE2`](https://arbiscan.io/address/0x0bd2267bAe2729150b29eD374A7CC73197d1fFE2#code) | Active `Ultra` implementation and ABI. |
| Exact source verification | [Sourcify proxy lookup](https://sourcify.dev/server/v2/contract/42161/0xc26af85ede9cc25d449bcebef866bb85afd5d346?fields=compilation,proxyResolution), [implementation source and ABI](https://sourcify.dev/server/v2/contract/42161/0x0bd2267bAe2729150b29eD374A7CC73197d1fFE2?fields=compilation,abi,sources) | Exact creation and runtime source verification. The proxy resolves to the `Ultra` implementation. |
| Known-library GitHub source | [OpenZeppelin Contracts Upgradeable](https://github.com/OpenZeppelin/openzeppelin-contracts-upgradeable) | Public upstream library family imported by `Ultra`, including ERC-20, AccessControl, and UUPS modules. |
| Product documentation | [Libeara ULTRA announcement](https://libeara.com/libeara-partners-with-wellington-and-fundbridge-capital-to-launch-a-u-s-treasuries-fund-tokenised-on-public-blockchain/) | Issuer/platform description, parties, and the Delta subscription/issuance/transfer/redemption role. |
| Operational / control context | [S&P Global Ratings: Libeara Delta](https://www.spglobal.com/ratings/en/regulatory/article/-/view/type/HTML/id/3523101) | Describes the compliance-led platform, separation of smart-contract administration roles, and whitelisted-wallet access. |
| Product-specific GitHub | — | No public Libeara or ULTRA contract repository was located by GitHub and web search on 2026-07-17. The exact deployed source is nevertheless published through Sourcify. |

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Arbitrum returned **36,192,127.917021
ULTRA** (6 decimals). The token ABI has no direct scalar share-price accessor.
Its configurable external `UltraManager` performs NAV/exchange-rate handling,
so resolve and validate the live manager before reading any price.

## Integration implications

- Classify ULTRA as a **permissioned, upgradeable ERC-20 fund unit** with an
  external issuance/redemption manager, rather than an ERC-4626 vault or a
  freely transferable ERC-20.
- Do not assume a successful allowance is sufficient for delivery: both
  parties must pass the configured KYC contract, and a transfer over the
  configured threshold enters a manager-mediated promise workflow.
- Account for privileged mint, role administration, KYC-contract replacement,
  clawback, transfer-limit configuration, and UUPS upgrade paths.
- Resolve the configured `UltraManager` and `KYCContract` from the live proxy
  before supporting deposits, redemptions, or transfers; the manager's
  request, NAV, and payment flows are separate from the token ABI.
- Re-resolve the proxy implementation and operational roles immediately before
  production use, because all material components are upgradeable or
  administrator-configurable.

## Ondo U.S. Dollar Yield (USDY) contract research

## Scope

This note covers the Ethereum USDY token in the tokenised-funds research list:
[`0x96f6ef951840721adbf46ac996b59e0235cb985c`](https://etherscan.io/address/0x96f6ef951840721adbf46ac996b59e0235cb985c).
Checked on 2026-07-17. It documents the contract interface and architecture,
not an assessment of eligibility, custody, redemption rights, or investment
suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Ondo U.S. Dollar Yield (USDY) | Ethereum ERC-20, [`0x96F6eF951840721AdBF46Ac996b59E0235CB985C`](https://etherscan.io/address/0x96f6ef951840721adbf46ac996b59e0235cb985c) | `USDY` behind an EIP-1967 `TransparentUpgradeableProxy`; current verified implementation [`0xea0F7EEbDc2Ae40edFE33bf03D332F8A7f617528`](https://etherscan.io/address/0xea0f7eebdc2ae40edfe33bf03d332f8a7f617528) | An accumulating, non-rebasing, permissioned ERC-20 representation of USDY. Transfers require allow-list membership and reject blocklisted or sanctioned participants; privileged roles can mint, burn, pause, and alter list-contract references. | [ondoprotocol/usdy](https://github.com/ondoprotocol/usdy), [`USDY.sol`](https://github.com/ondoprotocol/usdy/blob/main/contracts/usdy/USDY.sol) | [USDY basics](https://docs.ondo.finance/general-access-products/usdy/basics), [addresses](https://docs.ondo.finance/addresses) |

## On-chain verification

Etherscan labels the token address as a proxy and points to the implementation
above. The implementation is source-verified as an **Exact Match**, reports
contract name `USDY`, and compiles with Solidity `0.8.16` (optimiser enabled,
100 runs). Its verified source tree contains:

- `contracts/usdy/USDY.sol`;
- `BlocklistClientUpgradeable`, `AllowlistClientUpgradeable`, and
  `SanctionsListClientUpgradeable`;
- OpenZeppelin upgradeable ERC-20, pausable, access-control, and proxy
  dependencies; and
- the Chainalysis `ISanctionsList` interface.

The public Ondo repository contains a `USDY.sol` contract with the same
contract name, inheritance pattern, roles, initialiser parameters, and
transfer-restriction model. This is strong source-family evidence, but this
research did not build a particular Git commit to make a separate bytecode
comparison; Etherscan's verified implementation is the deployment-specific
source of record. A Sourcify source package was not obtained from the hosted
repository during this check, so no Sourcify assertion is made.

## Contract surface and behaviour

`USDY` inherits OpenZeppelin's
`ERC20PresetMinterPauserUpgradeable`, then adds issuer-controlled compliance
checks. The material externally callable functions are:

| Area | Functions | Effect |
| --- | --- | --- |
| ERC-20 | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `increaseAllowance`, `decreaseAllowance` | Standard 18-decimal ERC-20 interface, subject to the checks below. |
| Supply control | `mint(address,uint256)`, `burn(uint256)`, `burn(address,uint256)`, `burnFrom(address,uint256)` | Mint/burn capabilities are role-controlled; the two-argument `burn` is an explicit administrator burn protected by `BURNER_ROLE`. |
| Compliance configuration | `allowlist`, `blocklist`, `sanctionsList`, `setAllowlist`, `setBlocklist`, `setSanctionsList` | Reads or changes the three external compliance-list contract references. The setters require `LIST_CONFIGURER_ROLE`. |
| Administration | `grantRole`, `revokeRole`, `renounceRole`, `hasRole`, role-member enumeration | OpenZeppelin `AccessControlEnumerable` role management. Important exposed roles include `DEFAULT_ADMIN_ROLE`, `MINTER_ROLE`, `PAUSER_ROLE`, `BURNER_ROLE`, and `LIST_CONFIGURER_ROLE`. |
| Emergency control | `pause`, `unpause`, `paused` | Pauses/unpauses token operations for an account with `PAUSER_ROLE`. |
| Initialisation | overloaded `initialize(...)` | Used when deploying/configuring the proxy. The implementation constructor disables its own initialisers. |

Before mint, burn, transfer, or delegated transfer, `_beforeTokenTransfer`
checks the relevant sender and receiver: they must not be blocklisted or
sanctioned and must be allowlisted. For a delegated `transferFrom`, the
operator is also checked if it is neither the `from` nor the `to` address.
Consequently, USDY is not a freely transferable generic ERC-20 despite being
discoverable through the standard ERC-20 interface.

## Protocol-family conclusion

**Conclusion: Ondo's own RWA/USDY protocol (high confidence), using standard
OpenZeppelin upgradeable components rather than an external vault standard.**

The contract name and verified dependency tree match Ondo's public
[`ondoprotocol/usdy`](https://github.com/ondoprotocol/usdy) repository. That
repository describes USDY as a non-rebasing token whose yield accrues through
price appreciation, and describes its allowlist/blocklist/sanctions transfer
gates. Ondo's product documentation independently describes the accumulating
USDY form and its associated rebasing wrapper, rUSDY. The contract is therefore
best classified as an Ondo permissioned RWA token, not as ERC-4626, a
Centrifuge pool token, or a generic Securitize token.

## Related contracts that clarify the architecture

These are not substitutes for the USDY token address.

| Contract | Ethereum address | Purpose |
| --- | --- | --- |
| `USDY_InstantManager` | [`0xa42613C243b67BF6194Ac327795b926B4b491f15`](https://etherscan.io/address/0xa42613C243b67BF6194Ac327795b926B4b491f15) | Ondo's current on-chain subscription/redemption entry point. Official integration documentation specifies `subscribe(depositToken, depositAmount, minimumRwaReceived)` for USDC-to-USDY and `redeem(rwaAmount, receivingToken, minimumTokenReceived)` for USDY-to-USDC. Calling addresses must be registered in OndoIDRegistry. |
| `RWADynamicOracle` (USDY redemption-price oracle) | [`0xA0219AA5B31e65Bc920B5b6DFb8EdF0988121De0`](https://etherscan.io/address/0xA0219AA5B31e65Bc920B5b6DFb8EdF0988121De0) | Publishes the 18-decimal USDY price used by the manager. The docs describe time-ranged, compounding daily-interest-rate inputs and expose `getPrice`, `getPriceData`, and `getPriceHistorical`. |
| `rUSDY` | [`0xaf37c1167910ebC994e266949387d2c7C326b879`](https://etherscan.io/address/0xaf37c1167910ebC994e266949387d2c7C326b879) | Rebasing wrapper representation. It locks ordinary USDY and issues a token whose balance rebases; it should not be added as an independent fund without avoiding double counting. |
| Deprecated `USDYManager` | [`0x25A103A1D6AeC5967c1A4fe2039cdc514886b97e`](https://etherscan.io/address/0x25A103A1D6AeC5967c1A4fe2039cdc514886b97e) | The official address list marks this manager as deprecated. It illustrates the older RWAHub request/claim architecture but should not be selected for a new integration. |

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned
**971,667,626.830877926894936901 USDY** (18 decimals). The USDY token ABI has
no share-price accessor; the separate `RWADynamicOracle` exposes `getPrice`,
`getPriceData`, and `getPriceHistorical` for the redemption price.

## Integration implications

- A vault metadata adapter can safely identify the asset using the proxy
  address and standard ERC-20 reads, but must expect transfers and approvals
  involving unapproved addresses to fail because of compliance checks.
- NAV/yield cannot be derived from `totalSupply` alone: ordinary USDY accrues
  yield through its redemption price. Use the issuer's oracle/official pricing
  route, and account for rUSDY's wrapper relationship to avoid double counting.
- Resolve the active implementation at integration time because the token is
  upgradeable. Do not hard-code the implementation address as immutable
  protocol state.
- The manager adds KYC/identity registration, supported-token, rate-limit, and
  pause conditions beyond token-level allow-list checks.

## Primary sources

- [Etherscan proxy/token page](https://etherscan.io/address/0x96f6ef951840721adbf46ac996b59e0235cb985c)
- [Etherscan verified `USDY` implementation](https://etherscan.io/address/0xea0f7eebdc2ae40edfe33bf03d332f8a7f617528#code)
- [Ondo official smart-contract address list](https://docs.ondo.finance/addresses)
- [Ondo's public USDY repository](https://github.com/ondoprotocol/usdy)
- [Ondo USDY product documentation](https://docs.ondo.finance/general-access-products/usdy/basics)
- [Ondo InstantManager integration documentation](https://docs.ondo.finance/developer-guides/usdy-instant-manager-integration)

## USTB contract research

Checked on 2026-07-17. This note concerns the public Ethereum fund-token
deployment and technical interface, not investor eligibility, legal rights,
custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Invesco Short Duration US Government Securities Fund (USTB; Superstate-branded in the current developer documentation) | Ethereum ERC-20, [`0x43415eB6ff9DB7E26A15b704e7A3eDCe97d31C4e`](https://etherscan.io/address/0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e#code) | EIP-1967 proxy; current verified implementation [`SuperstateTokenV5_1`](https://etherscan.io/address/0x1f50a1ee0ec8275d0c83b7bb08896b4b47d6e8c4#code) at `0x1f50a1EE0eC8275d0c83B7BB08896B4b47D6E8C4` | An upgradeable, allowlisted 6-decimal ERC-20 fund share. It supports issuer mint/burn, pause/accounting pause, EIP-2612-style permit, USDC atomic subscription, off-chain redemption initiation, book-entry and cross-chain burn/bridge flows, and issuer-controlled NAV-oracle/stablecoin configuration. | [`superstateinc/ustb`](https://github.com/superstateinc/ustb) — public token and allowlist code; [`superstateinc/onchain-redemptions`](https://github.com/superstateinc/onchain-redemptions) — redemption/oracle code. | [Superstate smart-contract registry and integration guide](https://docs.superstate.com/welcome-to-superstate/smart-contracts), [USTB product documentation](https://docs.superstate.com/superstate-funds/ustb), [redemption documentation](https://docs.superstate.com/ustb/redeeming-ustb) |

## On-chain verification

Superstate's official smart-contract registry identifies the Ethereum USTB
token proxy as
[`0x43415eB6ff9DB7E26A15b704e7A3eDCe97d31C4e`](https://etherscan.io/address/0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e#code).
Etherscan labels it *Superstate: USTB Token*, recognises its EIP-1967 proxy
pattern, and resolves its active implementation to
[`0x1f50a1EE0eC8275d0c83B7BB08896B4b47D6E8C4`](https://etherscan.io/address/0x1f50a1ee0ec8275d0c83b7bb08896b4b47d6e8c4#code).

The implementation is Etherscan **Exact Match** source verified as
`SuperstateTokenV5_1`, compiled with Solidity `0.8.28` and one million
optimiser runs. Sourcify did not return hosted full- or partial-match metadata
for the proxy during this check, so the verified Etherscan implementation is
the deployment-specific source reference.

The public `superstateinc/ustb` repository is clearly the same protocol and
source family: its `SuperstateToken` is a pausable OpenZeppelin upgradeable
ERC-20 with the same subscription, allowlist, burn, bridge, accounting, and
oracle model. Its presently published `main` source is version 4 and does not
contain the exact `SuperstateTokenV5_1` contract name, so this research does
not claim an exact current-bytecode-to-Git-commit correspondence. The verified
Etherscan source remains authoritative for V5.1.

## Contract surface and behaviour

| Area | Functions | Effect |
| --- | --- | --- |
| ERC-20 and permit | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `permit`, `nonces`, `DOMAIN_SEPARATOR` | Standard token interface plus signed approval; transfer paths are gated by the private-instrument allowlist. |
| Investor eligibility | `allowlistV2`, `isAllowed` | Exposes the external Superstate allowlist used to decide whether an address may hold/transact in the fund token. |
| Issuance and redemption | `mint`, `bulkMint`, `adminBurn`, `offchainRedeem` | Owner-controlled creation/burning of shares, and a holder path to initiate the standard off-chain redemption process. |
| Atomic USDC subscription | overloaded `subscribe`, `calculateSuperstateTokenOut`, `supportedStablecoins` | On Ethereum, an allowlisted investor/protocol can transfer a configured stablecoin (currently documented as USDC) and receive USTB atomically at the oracle price, optionally to another allowlisted address. |
| Book-entry and cross-chain movement | `bridge`, `bridgeToBookEntry`, `supportedChainIds` | Burns source-chain USTB to receive the specified amount on a supported destination chain, or converts tokens into Superstate book-entry shares. |
| Price and redemption liquidity | `superstateOracle`, `getChainlinkPrice`, `redemptionContract`, `setOracle`, `setRedemptionContract` | References the issuer's continuous-price oracle and the separate redemption-liquidity contract. |
| Emergency and administration | `pause`, `unpause`, `accountingPause`, `accountingUnpause`, `owner`, `transferOwnership`, configuration setters | Owner can halt transfers or issuance/redemption accounting and alter oracle, fee, stablecoin, redemption, and bridge-chain configuration. |

The documented transfer control is not merely advisory: both sender and
recipient must be allowlisted and authorised for the specific private
instrument. The docs explain that identities are represented by Superstate
entity IDs and permissions, maintained after KYC and investment-agreement
processes; an unauthorised ERC-20 wallet cannot freely receive USTB.

## Protocol-family conclusion

**Conclusion: Superstate's own permissioned tokenised-fund protocol (high
confidence), based on OpenZeppelin upgradeable ERC-20 components, not
ERC-4626.**

The match is direct rather than inferred: the issuer's contract registry
names the exact proxy, links its public repositories, and describes the EVM
implementation as an upgradeable OpenZeppelin ERC-20 with Superstate's
allowlist, mint, redemption, bridge, and oracle extensions. The public source
and the deployment ABI share these distinctive function families. The token
does not expose the ERC-4626 `asset`, `deposit`, `withdraw`, or `redeem`
interface; its `subscribe` and redemption mechanisms are Superstate-specific
flows coupled to allowlisting and issuer pricing/liquidity infrastructure.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **59,105,391.356421 USTB**
(6 decimals). The token ABI provides pricing routes through `superstateOracle`
and `getChainlinkPrice()`; the documented continuous-price and daily NAV feeds
are the supported share-price sources rather than `totalSupply()`.

## Related contracts and integration implications

The official registry lists the following Ethereum components. They are
architecture dependencies, not alternative USTB token addresses:

| Contract | Address | Purpose |
| --- | --- | --- |
| `AllowlistV3` proxy | [`0x02f1fa8b196d21c7b733eb2700b825611d8a38e5`](https://etherscan.io/address/0x02f1fa8b196d21c7b733eb2700b825611d8a38e5#code) | Superstate's current permission/identity registry for EVM token holders and protocols. |
| `RedemptionIdle` proxy | [`0x4c21b7577c8fe8b0b0669165ee7c8f67fa1454cf`](https://etherscan.io/address/0x4c21b7577c8fe8b0b0669165ee7c8f67fa1454cf#code) | Holds replenished USDC liquidity for one-transaction USTB redemptions; `redeem` can fail if the contract lacks USDC. |
| USTB continuous-price oracle | [`0xe4fa682f94610ccd170680cc3b045d77d9e528a8`](https://etherscan.io/address/0xe4fa682f94610ccd170680cc3b045d77d9e528a8#code) | Superstate's NAV-per-share oracle, which linearly extrapolates from its two newest NAV checkpoints and presents a Chainlink-compatible interface. |
| Chainlink USTB oracle | [`0x289B5036cd942e619E1Ee48670F98d214E745AAC`](https://etherscan.io/address/0x289b5036cd942e619e1ee48670f98d214e745aac#code) | Issuer-listed daily NAV/share feed. |

- Track the proxy address, resolve its implementation dynamically, and observe
  upgrades/configuration changes. Do not consider its current implementation
  address immutable protocol state.
- Treat USTB as a compliance-gated tokenised fund, not a permissionless
  money-market vault. Standard ERC-20 reads work, but transfer, subscription,
  redemption, and bridge integrations must account for eligibility and pause
  conditions.
- For NAV and atomic settlement, use the documented oracle/redemption paths;
  `totalSupply` is not a standalone measure of current value or redeemable
  USDC liquidity.
- Avoid double counting shares burned by `bridge`, `bridgeToBookEntry`, or
  redemption flows when aggregating multi-chain or book-entry holdings.

## Primary sources

- [Etherscan USTB token proxy](https://etherscan.io/address/0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e#code)
- [Etherscan verified `SuperstateTokenV5_1` implementation](https://etherscan.io/address/0x1f50a1ee0ec8275d0c83b7bb08896b4b47d6e8c4#code)
- [Superstate's contract registry and technical integration documentation](https://docs.superstate.com/welcome-to-superstate/smart-contracts)
- [USTB fund documentation](https://docs.superstate.com/superstate-funds/ustb)
- [Superstate USTB token/allowlist source](https://github.com/superstateinc/ustb)
- [Superstate on-chain redemption/oracle source](https://github.com/superstateinc/onchain-redemptions)

## USTBL Ethereum contract research

Checked on 2026-07-17. This note is contract-discovery research, not an
assessment of investor eligibility, legal rights, or smart-contract security.

## Identification

| Field | Finding |
| --- | --- |
| Fund | Spiko US T-Bills Money Market Fund |
| Token | `USTBL` — a tokenised share in Spiko's open-ended UCITS money-market fund |
| Primary chain in this note | Ethereum mainnet |
| Token / proxy | [`0xe4880249745eac5f1ed9d8f7df844792d560e750`](https://etherscan.io/address/0xe4880249745eac5f1ed9d8f7df844792d560e750#code) |
| Proxy contract | `ERC1967Proxy` (OpenZeppelin) |
| Current implementation | [`0x15EA0EC460a0E6847EC0AA8D50A84B3A51B95f74`](https://etherscan.io/address/0x15ea0ec460a0e6847ec0aa8d50a84b3a51b95f74#code) — `Token` |
| Verification | Proxy and current implementation are both exact creation and runtime source matches in [Sourcify](https://sourcify.dev/server/v2/contract/1/0x15ea0ec460a0e6847ec0aa8d50a84b3a51b95f74), and the implementation is exact source-verified in Etherscan. |

The token is a multi-chain Spiko deployment: the issuer states that its
infrastructure operates across Ethereum, Polygon, Base, Arbitrum, Starknet,
and Stellar. This document identifies only the above Ethereum proxy; do not
substitute a same-symbol token deployed on another chain.

## Contract surface

The active `Token` implementation is a UUPS-upgradeable, permissioned ERC-20
fund-share token. Its verified source inherits OpenZeppelin
`ERC20Upgradeable`, `ERC20PausableUpgradeable`, `ERC20PermitUpgradeable`,
`ERC2771ContextUpgradeable`, `MulticallUpgradeable`, and UUPS components. It
also includes Spiko's `PermissionManaged` access layer and an ERC-1363
extension.

| Function group | Functions / mechanism | Description |
| --- | --- | --- |
| Standard token and permit | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `permit`, `nonces`, `DOMAIN_SEPARATOR` | ERC-20 balance/allowance interface with EIP-2612-style permit support. |
| ERC-1363 callbacks | `transferAndCall`, `transferFromAndCall`, `approveAndCall` | ERC-1363 extension for token actions that notify a receiving/spender contract. Integration must account for the token's permission checks. |
| Issuance and redemption servicing | `mint(address,uint256)`, `burn(address,uint256)` | Restricted issuance and destruction. The issuer describes minting as primary issuance when an investor subscribes. |
| Transfer restrictions | Internal `_update` authority check | For ordinary transfers, both sender and receiver must be authorised by the external authority for the `IERC20.transfer` selector. Minting is exempt for the zero-address sender; burning is exempt for the zero-address receiver. |
| Emergency / ownership | `pause`, `unpause`, `setOwnership`, `owner` | Restricted pause control and a restricted ownership reset mechanism. Pausing is enforced by `ERC20PausableUpgradeable`. |
| Meta-transactions and batching | `trustedForwarder`, `isTrustedForwarder`, `multicall` | ERC-2771 trusted-forwarder support and batched calls. The source cautions that restricted administrative calls cannot be made through the forwarder. |
| Upgrade | `upgradeToAndCall`, `proxiableUUID` | UUPS upgrade path. The implementation authorises upgrades through the same restricted authority check. |

The implementation has no ERC-4626 `asset`, `deposit`, `withdraw`, `redeem`,
`convertToShares`, or `convertToAssets` functions. USTBL is therefore a
**permissioned tokenised mutual-fund share**, rather than an ERC-4626 vault.

## Issuance architecture

Spiko documents a dedicated `Minter` contract as the sole intended path for
new-token issuance. Its relayer cannot call `Token.mint()` directly: the
Minter enforces per-token daily limits, records mint state, and requires
separate approval for a mint that would exceed the limit. The Minter's public
functions include `initiateMint`, `approveMint`, `cancelMint`, `dailyLimit`,
and `getMintedToday`.

This is material context for the token's restricted `mint` ABI: an address
with direct `mint` permission is expected to be the Spiko servicing system,
not an arbitrary investor wallet. Redemptions are operated as a separate
issuer-side contract/workflow, as described in the infrastructure
documentation.

## Protocol-family conclusion

**Conclusion: Spiko's open-source proprietary permissioned fund-token and
servicing system — high confidence.**

Spiko calls its `Token` contract the core share representation for its
open-ended European mutual funds (UCITS), including USTBL and EUTBL. The
verified implementation's `PermissionManaged` imports, sender/receiver
whitelist enforcement, pausing, restricted mint/burn, and UUPS upgrades match
the issuer's public architecture. The issuer also maintains the public
[`spiko-tech/contracts`](https://github.com/spiko-tech/contracts) repository,
which contains the `Token`, `PermissionManaged`, and rate-limited `Minter`
source.

It is not an ERC-4626 vault or a deployment of Centrifuge, Securitize, Ondo,
or Superstate. It uses well-known OpenZeppelin components and adds a Spiko
permission manager, token extension, and issuance-service pattern.

## Public source, GitHub, and documentation

| Resource | Link | Finding |
| --- | --- | --- |
| Token-proxy explorer/source | [Etherscan: `0xe488…e750`](https://etherscan.io/address/0xe4880249745eac5f1ed9d8f7df844792d560e750#code) | OpenZeppelin ERC-1967 proxy, active implementation link, historic implementations, and token tracker. |
| Implementation explorer/source | [Etherscan: `0x15EA…5f74`](https://etherscan.io/address/0x15ea0ec460a0e6847ec0aa8d50a84b3a51b95f74#code) | Exact verified `Token` source, including the external-authority whitelist checks, restricted admin functions, and UUPS authorisation. |
| Independent verification | [Sourcify: proxy](https://sourcify.dev/server/v2/contract/1/0xe4880249745eac5f1ed9d8f7df844792d560e750), [implementation](https://sourcify.dev/server/v2/contract/1/0x15ea0ec460a0e6847ec0aa8d50a84b3a51b95f74) | Exact source matches for both deployed addresses. |
| Issuer GitHub repository | [spiko-tech/contracts](https://github.com/spiko-tech/contracts) | Public source for the contract family. Relevant files include [`Token.sol`](https://github.com/spiko-tech/contracts/blob/main/contracts/token/Token.sol), [`Minter.sol`](https://github.com/spiko-tech/contracts/blob/main/contracts/token/Minter.sol), and [`PermissionManaged.sol`](https://github.com/spiko-tech/contracts/blob/main/contracts/permissions/PermissionManaged.sol). |
| Contract architecture documentation | [Spiko's smart contracts](https://tech.spiko.io/posts/spiko-smart-contracts/) | Explains that the token represents UCITS shares, uses reusable permissioning, and is designed for multiple fund tokens. |
| Issuance documentation | [Rate-limited token minting](https://tech.spiko.io/posts/minter/) | Describes the Minter, daily limits, blocked/approved mint flow, and primary-issuance role. |
| Multi-chain operations documentation | [Spiko blockchain infrastructure](https://tech.spiko.io/posts/spiko-blockchain-infrastructure/) | Identifies networks and event-indexed token/redemption operations. |
| Upstream component family | [OpenZeppelin Contracts](https://github.com/OpenZeppelin/openzeppelin-contracts), [OpenZeppelin Contracts Upgradeable](https://github.com/OpenZeppelin/openzeppelin-contracts-upgradeable) | The verified implementation imports these ERC-20, permit, pausable, ERC-2771, UUPS, and utility components. |

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **53,782,226.27927 USTBL**
(5 decimals). The ERC-20 token ABI has no share-price or NAV accessor; obtain
the fund NAV and redemption terms from Spiko's issuer/fund data.

## Integration implications

- Classify USTBL as a **Spiko permissioned tokenised-fund share**, not a
  general-purpose freely transferable ERC-20 or an ERC-4626 vault.
- A successful ERC-20 ABI decode does not establish transferability: both
  transfer endpoints need external-authority permission, and the token can be
  paused.
- Resolve the EIP-1967 implementation on refresh, because the proxy is UUPS
  upgradeable.
- Do not invoke `mint` or assume an approved allowance grants issuance;
  Spiko's documented Minter/permission system is the primary-issuance route.
- Obtain current NAV, eligibility, and redemption conditions from Spiko fund
  material rather than from ERC-20 supply or `balanceOf` alone.

## USYC contract research

Checked on 2026-07-17. This note is contract-discovery research, not an
assessment of investor eligibility, legal rights, or security.

## Identification

| Field | Finding |
| --- | --- |
| Fund | Circle USYC / Hashnote International Short Duration Yield Fund Ltd. |
| Token | US Yield Coin (`USYC`), 6 decimals |
| Primary chain | Ethereum mainnet |
| Token / proxy | [`0x136471a34f6ef19fe571effc1ca711fdb8e49f2b`](https://etherscan.io/address/0x136471a34f6ef19fe571effc1ca711fdb8e49f2b#code) |
| Proxy contract | `ShortDurationYieldCoinProxy` — an OpenZeppelin `ERC1967Proxy` wrapper |
| Current implementation | [`0xBF0f2F3aad6b99893D80c550fbAcEc915545eb92`](https://etherscan.io/address/0xBF0f2F3aad6b99893d80c550fbacec915545eb92#code) — `YieldCoin` |
| Verification | Both the proxy and the active implementation are exact source matches in [Sourcify](https://sourcify.dev/server/v2/contract/1/0xbf0f2f3aad6b99893d80c550fbacec915545eb92), and source-verified in [Etherscan](https://etherscan.io/address/0x136471a34f6ef19fe571effc1ca711fdb8e49f2b#code). |

The fund is the Hashnote International Short Duration Yield Fund Ltd.; Circle
International Bermuda Limited administers its token on the fund's behalf.
The issuer describes its investments as short-term US government securities
and reverse repos. See the [Circle product page](https://www.circle.com/usyc)
and the [Hashnote smart-contract address registry](https://usyc.docs.hashnote.com/overview/smart-contracts).

## Contract surface

`ShortDurationYieldCoinProxy` is only the ERC-1967 delegation wrapper. The
functional token is the upgradeable `YieldCoin` implementation. Its verified
source contains issuer-specific `Access`, `Ownable`, `RolesAuthority`, and
`ERC20` components alongside OpenZeppelin upgradeability code.

The relevant public ABI functions are:

| Function group | Functions | Description |
| --- | --- | --- |
| ERC-20 and permit | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `permit`, `nonces`, `DOMAIN_SEPARATOR` | Standard token read, allowance, transfer, and EIP-2612-style permit surface. |
| Issuance and destruction | `mint`, `burn(uint256)`, `burn(address,uint256)`, `burnFor` | Creates or destroys USYC; access-controlled in the custom implementation. |
| Minter limits | `minterAllowance`, `setMinterAllowance`, `incrementMinterAllowance`, `decrementMinterAllowance` | Per-minter issuance allowance administration. |
| Governance and upgrade | `owner`, `pendingOwner`, `transferOwnership`, `acceptOwnership`, `renounceOwnership`, `authority`, `upgradeTo`, `upgradeToAndCall` | Ownership/role control and UUPS implementation upgrade functions. |
| Asset recovery | `sweep` | Privileged recovery of tokens sent to the contract. |

The current ABI exposes neither `asset`, `deposit`, `withdraw`, `redeem`, nor
`convertToShares`. Therefore the token itself is **not an ERC-4626 vault**.
Subscriptions and redemptions are a separate, permissioned **Teller** flow:
the [official integration guide](https://usyc.docs.hashnote.com/integration-guides/teller-smart-contract)
documents `deposit(uint256,address)` and
`redeem(uint256,address,address)`, while the [address registry](https://usyc.docs.hashnote.com/overview/smart-contracts)
publishes the current Ethereum Teller, cross-chain Teller, oracle, and
entitlement contracts.

## Protocol-family conclusion

**Conclusion: Hashnote/Circle proprietary permissioned yield-coin system — high confidence.**

This is not a deployment of a public generic fund protocol such as ERC-4626,
Ondo, Securitize, Centrifuge, or Superstate. The implementation is named
`YieldCoin`, is deployed by the Etherscan-labelled Hashnote deployer, and
includes Hashnote-specific source paths (`src/core/coins`,
`src/core/entitlements`, and `src/core/tellers`). Its `RolesAuthority` source
states that it is modified from Solmate's `RolesAuthority`; the only identified
public protocol-code relationship is therefore an authorisation-component
lineage, not an adoption of a full external fund platform.

The public issuer documentation also makes the permissioning explicit: a
wallet must be entitled to interact with USYC, and the address registry includes
a distinct Entitlements contract. Do not treat a successful `transfer` ABI
decode as evidence that arbitrary wallets can receive or redeem the token.

## Public source, GitHub, and documentation

| Resource | Link | Finding |
| --- | --- | --- |
| Token proxy explorer/source | [Etherscan: `0x1364…9f2b`](https://etherscan.io/address/0x136471a34f6ef19fe571effc1ca711fdb8e49f2b#code) | Verified `ShortDurationYieldCoinProxy`, active implementation link, historical implementations, and proxy ABI. |
| Implementation explorer/source | [Etherscan: `0xBF0f…eb92`](https://etherscan.io/address/0xbf0f2f3aad6b99893d80c550fbacec915545eb92#code) | Verified `YieldCoin` source and ABI. |
| Independent source verification | [Sourcify: proxy](https://sourcify.dev/server/v2/contract/1/0x136471a34f6ef19fe571effc1ca711fdb8e49f2b), [implementation](https://sourcify.dev/server/v2/contract/1/0xbf0f2f3aad6b99893d80c550fbacec915545eb92) | Exact runtime and creation matches. |
| Issuer technical docs | [USYC smart contracts](https://usyc.docs.hashnote.com/overview/smart-contracts), [Teller integration](https://usyc.docs.hashnote.com/integration-guides/teller-smart-contract), [token-price oracle](https://usyc.docs.hashnote.com/overview/token-price) | Canonical addresses, subscription/redemption interface, and NAV-oracle explanation. |
| Issuer product documentation | [Circle USYC](https://www.circle.com/usyc) | Fund structure, administrator, networks, eligibility, and product description. |
| Public Hashnote/Circle GitHub repository | None found | Searches for `ShortDurationYieldCoinProxy`, `YieldCoin`, and the verified source path did not locate an issuer-maintained public repository. The verified explorer/Sourcify source is the reproducible source reference. |
| Upstream authorisation component | [Solmate `RolesAuthority`](https://github.com/transmissions11/solmate/blob/main/src/auth/authorities/RolesAuthority.sol) | The verified Hashnote source explicitly attributes its `RolesAuthority` implementation as modified from this component. |

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **81,015,541.068726 USYC**
(6 decimals). The YieldCoin ABI has no share-price or NAV accessor. Pricing is
published through Hashnote's separate oracle/API, not the token ABI.

## Integration implications

- Track this as a **tokenised fund / permissioned ERC-20**, not an ERC-4626
  vault. Its price accrues through NAV appreciation rather than a rebasing
  balance, according to the issuer's [token-price documentation](https://usyc.docs.hashnote.com/overview/token-price).
- Resolve the active EIP-1967 implementation at runtime and observe upgrades;
  the proxy is upgradeable and Etherscan shows historic implementations.
- Where pricing is required, use the issuer's published oracle/API rather than
  inferring NAV from the ERC-20 supply alone.
- Gate any trading or redemption workflow on the issuer's entitlement/KYC
  controls and the Teller contract, not just token ownership.

## WTGXX contract research

Checked on 2026-07-17. This note identifies the public Ethereum token
contract and its observable interface; it is not an assessment of investor
eligibility, legal ownership, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| WisdomTree Treasury Money Market Digital Fund (WTGXX) | Ethereum ERC-20, [`0x1feCF3d9d4Fee7f2c02917A66028a48C6706c179`](https://etherscan.io/address/0x1fecf3d9d4fee7f2c02917a66028a48c6706c179#code) | Token proxy; current verified implementation [`ERC20RevocableComplianceStandard`](https://etherscan.io/address/0xc2a8ca84bc363605c36757f9409b214b6ee710c9#code) at `0xC2A8ca84BC363605c36757f9409b214b6ee710c9` | A permissioned, revocable ERC-20 record of WTGXX fund shares. It supports issuer mint/burn, frozen accounts, clawback, pausing, role control, batch operations, and a pluggable compliance context that decides whether a transfer is permitted. | No public issuer source repository found. The OpenZeppelin audit identifies the reviewed `wisdomtreeam/whitelist-contexts` repository, but its public GitHub URL is currently unavailable. [OpenZeppelin Contracts](https://github.com/OpenZeppelin/openzeppelin-contracts) is an upstream dependency family, not this deployment's source. | [WTGXX fund page and official addresses](https://www.wisdomtreeconnect.com/digital-funds/money-market/wtgxx), [WisdomTree Connect developer docs](https://docs.wisdomtreeconnect.com/), [OpenZeppelin Whitelist Contexts audit](https://www.openzeppelin.com/news/wisdomtree-digital-whitelist-contexts-audit) |

## On-chain verification

The fund's official [WTGXX page](https://www.wisdomtreeconnect.com/digital-funds/money-market/wtgxx)
lists `0x1feCF3d9d4Fee7f2c02917A66028a48C6706c179` as its Ethereum token
address. Etherscan identifies that address as the WTGXX ERC-20 and labels it
as a proxy, resolving the active implementation to
[`0xC2A8ca84BC363605c36757f9409b214b6ee710c9`](https://etherscan.io/address/0xc2a8ca84bc363605c36757f9409b214b6ee710c9#code).

The implementation is source-verified by Etherscan as an **Exact Match**,
with contract name `ERC20RevocableComplianceStandard`, Solidity `0.8.19`, and
optimisation enabled (200 runs). Sourcify did not return a hosted full or
partial-match metadata package for either address during this research; the
verified Etherscan source and ABI are the deployment-specific reference.

The verified implementation exposes `upgradeBeaconToAndCall`, while
WisdomTree's independently published OpenZeppelin audit describes its
whitelist system as using a Beacon Proxy upgrade pattern. Treat both the
token's proxy target and its compliance configuration as mutable operational
state, rather than hard-coding the present implementation as permanent.

## Contract surface and behaviour

The significant callable functions in the verified ABI are:

| Area | Functions | Effect |
| --- | --- | --- |
| ERC-20 | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom` | Standard ERC-20 reads and movement, subject to the compliance condition. |
| Issue and redemption operations | `mint`, `batchMint`, `burn`, `batchBurn` | Privileged supply increase and decrease operations. These are issuer-controlled token actions, not public ERC-4626 `deposit`/`redeem` functions. |
| Compliance and transfer control | `getCompliance`, `setCompliance`, `removeCompliance`, `isAddressWhitelisted`, `freeze`, `unfreeze`, `batchFreeze`, `batchUnfreeze`, `isFrozen` | Associates an external compliance context with the token and can deny transfers involving frozen or non-compliant addresses. |
| Exceptional movement | `clawback`, `batchClawback`, `batchTransfer` | A privileged actor can move tokens without the ordinary holder-initiated ERC-20 flow; `clawback` emits its own event. |
| Emergency and upgrade controls | `pause`, `unpause`, `isPaused`, `upgradeBeaconToAndCall` | Stops token activity and allows the privileged beacon-upgrade path. |
| Administration | `grantRole`, `revokeRole`, `grantDefaultAdminRole`, `revokeDefaultAdminRole`, delegated-admin functions, `hasRole` | Role-based control. The ABI declares `DEFAULT_ADMIN_ROLE`, `DELEGATED_ADMIN_ROLE`, `ISSUER_ROLE`, and `REGISTRAR_ROLE`. |
| Initialisation | `initializeWithRoles` | Establishes the token metadata, initial supply/recipient, and owner/issuer/registrar roles. |

This is a permissioned security-token interface. In particular, `mint`,
`burn`, `clawback`, `freeze`, and `setCompliance` make its behaviour
materially different from a permissionless ERC-20 or ERC-4626 vault share.

## Protocol-family conclusion

**Conclusion: WisdomTree Digital's proprietary whitelist-compliance token
standard (high confidence); not an external general-purpose tokenisation
protocol.**

The deployment-specific contract name and functions match the role and
compliance model described by WisdomTree and in OpenZeppelin's independent
audit of WisdomTree Digital's Whitelist Contexts. The audit states that this
system authenticates transfers by whitelist membership, supports compliant
token standards, and uses non-transferable ERC-721 tokens for whitelisted
wallet identity. WisdomTree Connect likewise says that permission is granted
to registered wallets through revocable, soulbound ERC-721 attestations and
that WTGXX can be peer-to-peer transferred only between verified,
permissioned wallets.

The audit specifically names a reviewed `wisdomtreeam/whitelist-contexts`
repository and commit `4476078`, but a current public GitHub search for the
repository, the exact implementation name, its source path
`src/tokens/standards/ERC20RevocableComplianceStandard.sol`, and
`IERC20RevocableCompliance` did not locate a publicly accessible issuer
repository. Therefore the precise deployed standard should be treated as
proprietary published-on-explorer source, rather than equated with an
unaffiliated ERC-3643, CMTAT, or T-REX implementation merely because all are
compliance-oriented token families.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned
**752,397,305.498747707437504894 WTGXX** (18 decimals). The token ABI has no
share-price or NAV accessor; valuation must come from WisdomTree's issuer-side
fund/NAV sources.

## Integration implications

- Classify WTGXX as a **tokenised fund / permissioned ERC-20**, not as an
  ERC-4626 vault. The token ABI has no `asset`, `deposit`, `mint` (share-vault
  semantics), `withdraw`, or `redeem` ERC-4626 surface; its `mint` is an
  issuer supply-control operation.
- A standard ERC-20 transfer may fail for an unregistered, frozen, or otherwise
  non-compliant wallet. WisdomTree says the transfer agent remains the
  official record of fund-share ownership, so token movement alone should not
  be treated as a complete ownership or settlement model.
- Resolve the token implementation at integration time and monitor the
  upgrade path and compliance-context address. Privileged roles can also
  pause, freeze, mint, burn, or claw back positions.
- Use the official fund page/API for fund value and eligibility workflows;
  neither `totalSupply` nor ERC-20 balances establish an independently
  redeemable, permissionless NAV claim.

## Primary sources

- [WTGXX official fund page and Ethereum token address](https://www.wisdomtreeconnect.com/digital-funds/money-market/wtgxx)
- [Etherscan WTGXX token/proxy](https://etherscan.io/address/0x1fecf3d9d4fee7f2c02917a66028a48c6706c179#code)
- [Etherscan verified `ERC20RevocableComplianceStandard` implementation](https://etherscan.io/address/0xc2a8ca84bc363605c36757f9409b214b6ee710c9#code)
- [WisdomTree Connect: wallet permissioning and WTGXX settlement](https://www.wisdomtreeconnect.com/)
- [WisdomTree Digital Funds architecture](https://www.wisdomtree.com/us/about-digital-funds)
- [OpenZeppelin's WisdomTree Digital Whitelist Contexts audit](https://www.openzeppelin.com/news/wisdomtree-digital-whitelist-contexts-audit)
