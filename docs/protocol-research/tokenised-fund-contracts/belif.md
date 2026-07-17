# BELIF Ethereum contract research

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
