# BlackRock ICS US Dollar Liquidity Fund (CASHx) contract research

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
