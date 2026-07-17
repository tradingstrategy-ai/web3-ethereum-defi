# ULTRA Arbitrum contract research

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
