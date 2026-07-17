# USYC contract research

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
