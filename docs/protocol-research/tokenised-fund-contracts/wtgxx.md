# WTGXX contract research

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
