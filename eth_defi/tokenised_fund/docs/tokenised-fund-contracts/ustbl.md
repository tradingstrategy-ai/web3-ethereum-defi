# USTBL Ethereum contract research

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
