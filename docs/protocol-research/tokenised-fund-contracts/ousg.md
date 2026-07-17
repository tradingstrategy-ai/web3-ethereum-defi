# Ondo Short-Term US Government Treasuries (OUSG) contract research

Checked on 2026-07-17. This is contract-discovery research, not an assessment
of investor eligibility, legal rights, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Ondo Short-Term US Government Treasuries (`OUSG`) | Ethereum ERC-20 proxy, [`0x1B19C19393e2d034D8Ff31ff34c81252FcBbee92`](https://etherscan.io/address/0x1b19c19393e2d034d8ff31ff34c81252fcbbee92#code) | `TokenProxy` (EIP-1967 `TransparentUpgradeableProxy`); active implementation [`CashKYCSenderReceiver`](https://etherscan.io/address/0x1ceb44b6e515abf009e0ccb6ddafd723886cf3ff#code) at `0x1CEB44b6E515aBf009E0CCb6ddaFD723886cf3Ff` | A non-rebasing, NAV-appreciating, permissioned ERC-20 fund-share token. The implementation uses OpenZeppelin's upgradeable ERC-20/access-control/pause components and requires the transaction sender, source holder, and destination holder to satisfy Ondo's configured KYC registry. Separate Ondo contracts provide the price oracle and subscriptions/redemptions. | [Ondo public contract repository](https://github.com/ondoprotocol/usdy) (documents the shared Ondo RWAHub architecture); source-specific historic repository link in Ondo’s address registry now returns 404, so use the verified explorer/Sourcify source for this deployed implementation. | [OUSG overview](https://docs.ondo.finance/qualified-access-products/ousg/overview), [official address registry](https://docs.ondo.finance/addresses), [technical page](https://docs.ondo.finance/qualified-access-products/ousg/technical) |

## On-chain identification and verification

Etherscan labels the proxy as **Ondo Finance: OUSG Token**, identifies its
active implementation as `0x1CEB…f3Ff`, and records two proxy upgrades. The
proxy source is verified; it is `TokenProxy`, a thin wrapper around
OpenZeppelin's `TransparentUpgradeableProxy`. The current implementation is
`CashKYCSenderReceiver` at
[`0x1CEB44b6E515aBf009E0CCb6ddaFD723886cf3Ff`](https://etherscan.io/address/0x1ceb44b6e515abf009e0ccb6ddafd723886cf3ff#code).

Both contracts are exact creation- and runtime-bytecode matches in Sourcify:

| Contract | Sourcify finding |
| --- | --- |
| `TokenProxy` | [Exact match](https://sourcify.dev/server/v2/contract/1/0x1b19c19393e2d034d8ff31ff34c81252fcbbee92?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution), compiler `0.8.16`, optimiser 100 runs; proxy resolution identifies an EIP-1967 proxy and the active `CashKYCSenderReceiver` implementation. |
| `CashKYCSenderReceiver` | [Exact match](https://sourcify.dev/server/v2/contract/1/0x1CEB44b6E515aBf009E0CCb6ddaFD723886cf3Ff?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution), compiler `0.8.16`, optimiser 100 runs; fully qualified name `contracts/cash/token/CashKYCSenderReceiver.sol:CashKYCSenderReceiver`. |

The implementation source includes `KYCRegistryClientInitializable` and
OpenZeppelin `ERC20PresetMinterPauserUpgradeable`,
`AccessControlEnumerableUpgradeable`, and `PausableUpgradeable`. This is a
token proxy/implementation design, not an ERC-4626 vault contract. Its ABI has
no `asset`, `totalAssets`, `deposit`, `withdraw`, `redeem`, or
`convertToShares` function.

## Contract surface and behaviour

| Area | Material functions | Behaviour |
| --- | --- | --- |
| ERC-20 | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `increaseAllowance`, `decreaseAllowance` | Standard ERC-20 interface, but token movement is subject to the KYC check below. |
| KYC configuration | `kycRegistry`, `kycRequirementGroup`, `setKYCRegistry`, `setKYCRequirementGroup` | A holder must satisfy the configured registry and requirement group. Only `KYC_CONFIGURER_ROLE` can change either reference. |
| Transfer gate | inherited `transfer`, `transferFrom`, plus the implementation's `_beforeTokenTransfer` | The verified implementation requires the transaction sender to be KYC'd. For a transfer, both `from` and `to` must also be KYC'd; for mint and burn it omits the zero address as appropriate. Thus, an unapproved delegated-transfer operator cannot use an otherwise valid allowance. |
| Supply control | `mint(to,amount)`, `burn(amount)`, `burnFrom(account,amount)`, `burn(from,amount)` | `MINTER_ROLE` controls minting through the inherited preset. Holders can use normal burn methods subject to allowance rules; the additional two-argument `burn` permits a `BURNER_ROLE` account to destroy another holder's balance. |
| Administration and emergency control | `grantRole`, `revokeRole`, `renounceRole`, role-member enumeration, `pause`, `unpause`, `paused` | Standard OpenZeppelin role administration. `PAUSER_ROLE` can stop token operations; privileged control must be included in any operational-risk assessment. |
| Proxy administration | `admin`, `implementation`, `changeAdmin`, `upgradeTo`, `upgradeToAndCall` | `TokenProxy` administration surface, not ordinary token methods. Resolve the implementation at integration time because it can change. |

OUSG accrues value through its on-chain NAV price rather than rebasing token
balances. Ondo describes the daily price update and price-per-token calculation
in its [OUSG overview](https://docs.ondo.finance/qualified-access-products/ousg/overview).
Therefore, `totalSupply` is not a sufficient valuation input.

## Known protocol and fund conclusion

**Conclusion: Ondo's proprietary qualified-access fund-token system — high
confidence.**

The proxy's Etherscan Ondo label, the exact verified source's Ondo-specific
KYC client, and Ondo's official address registry identify this as the OUSG
token. GitHub/web research finds the same KYC-token and RWA subscription
architecture in Ondo's public code and documentation, rather than a deployment
of an external fund standard such as ERC-4626, Centrifuge, Securitize, or
Superstate.

The public [Ondo repository](https://github.com/ondoprotocol/usdy) describes
the historical shared `RWAHub` subscription/redemption system for OUSG, OMMF,
and USDY, including KYC checks and price-oracle-mediated mint/redemption
requests. The current official address page has a direct GitHub link for the
historic `CashKYCSenderReceiver` source path, but that target
(`ondoprotocol/tokenized-funds`) was unavailable during this check. The
deployment-specific exact-match source on Etherscan and Sourcify is therefore
the reproducible reference for `CashKYCSenderReceiver`; the live address and
functionality are not inferred from the missing GitHub path.

## Related live contracts

These contracts clarify the current integration path; they are not substitutes
for the OUSG ERC-20 address.

| Contract | Ethereum address | Role |
| --- | --- | --- |
| `OUSG_InstantManager` | [`0x93358db73B6cd4b98D89c8F5f230E81a95c2643a`](https://etherscan.io/address/0x93358db73B6cd4b98D89c8F5f230E81a95c2643a#code) | Current official instant subscription/redemption entry point. Its verified ABI exposes `subscribe` and `redeem`, accepted-token configuration, pricing, fee, rate-limit, compliance, and Ondo-ID-registry references. |
| `OndoIDRegistry` | [`0xcf6958D69d535FD03BD6Df3F4fe6CDcd127D97df`](https://etherscan.io/address/0xcf6958D69d535FD03BD6Df3F4fe6CDcd127D97df#code) | The official registry page says this stores addresses that can hold OUSG. |
| `OndoOracle` | [`0x9Cad45a8BF0Ed41Ff33074449B357C7a1fAb4094`](https://etherscan.io/address/0x9cad45a8bf0ed41ff33074449b357c7a1fab4094#code) | The official registry identifies this as the unified price-data interface required by Ondo contracts. |

Ondo's documentation says instant minting and redemption is available only on
Ethereum mainnet, even though OUSG has representations on Polygon, Solana, and
XRP Ledger. Eligibility/onboarding remains a precondition for holding,
transferring, investing, or redeeming the qualified-access token.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned
**1,580,534.087618339562150913 OUSG** (18 decimals). The token ABI has no
direct share-price method; use the separately deployed issuer `OndoOracle` and
manager route documented below for the daily NAV price.

## Integration implications

- Track the proxy address as the OUSG ERC-20, but resolve and monitor the
  active EIP-1967 implementation at runtime.
- Do not model OUSG as an ERC-4626 vault. Read the official on-chain price
  oracle/manager route for NAV and use the manager for issuer-supported
  subscription/redemption flows.
- Treat every transfer as permissioned: the sender, holder, and recipient KYC
  requirements apply at the token level, while the manager adds separate
  onboarding, compliance, fee, accepted-asset, and rate-limit conditions.
- The ordinary ERC-20 ABI does not establish that a wallet can receive or
  redeem OUSG. Perform the issuer's applicable onboarding and simulate the
  intended call path before integrating a flow.

## Primary sources

- [Etherscan OUSG proxy and proxy-history view](https://etherscan.io/address/0x1b19c19393e2d034d8ff31ff34c81252fcbbee92#code)
- [Etherscan `CashKYCSenderReceiver` implementation](https://etherscan.io/address/0x1ceb44b6e515abf009e0ccb6ddafd723886cf3ff#code)
- [Sourcify exact-match proxy record](https://sourcify.dev/server/v2/contract/1/0x1b19c19393e2d034d8ff31ff34c81252fcbbee92?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution)
- [Sourcify exact-match implementation record](https://sourcify.dev/server/v2/contract/1/0x1CEB44b6E515aBf009E0CCb6ddaFD723886cf3Ff?fields=compilation%2Cmetadata%2Csources%2Cdeployment%2CproxyResolution)
- [Ondo official address registry](https://docs.ondo.finance/addresses)
- [Ondo OUSG overview](https://docs.ondo.finance/qualified-access-products/ousg/overview)
- [Ondo OUSG technical documentation](https://docs.ondo.finance/qualified-access-products/ousg/technical)
- [Ondo public RWA contract repository](https://github.com/ondoprotocol/usdy)
