# FDIT Ethereum contract research

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
