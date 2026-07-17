# thBILL contract research

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
