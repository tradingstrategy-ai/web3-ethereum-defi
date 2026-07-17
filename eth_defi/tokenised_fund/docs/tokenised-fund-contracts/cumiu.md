# CUMIU contract research

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
