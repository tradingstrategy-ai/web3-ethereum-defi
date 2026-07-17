# DCP contract research

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
