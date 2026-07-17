# Ondo U.S. Dollar Yield (USDY) contract research

## Scope

This note covers the Ethereum USDY token in the tokenised-funds research list:
[`0x96f6ef951840721adbf46ac996b59e0235cb985c`](https://etherscan.io/address/0x96f6ef951840721adbf46ac996b59e0235cb985c).
Checked on 2026-07-17. It documents the contract interface and architecture,
not an assessment of eligibility, custody, redemption rights, or investment
suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Ondo U.S. Dollar Yield (USDY) | Ethereum ERC-20, [`0x96F6eF951840721AdBF46Ac996b59E0235CB985C`](https://etherscan.io/address/0x96f6ef951840721adbf46ac996b59e0235cb985c) | `USDY` behind an EIP-1967 `TransparentUpgradeableProxy`; current verified implementation [`0xea0F7EEbDc2Ae40edFE33bf03D332F8A7f617528`](https://etherscan.io/address/0xea0f7eebdc2ae40edfe33bf03d332f8a7f617528) | An accumulating, non-rebasing, permissioned ERC-20 representation of USDY. Transfers require allow-list membership and reject blocklisted or sanctioned participants; privileged roles can mint, burn, pause, and alter list-contract references. | [ondoprotocol/usdy](https://github.com/ondoprotocol/usdy), [`USDY.sol`](https://github.com/ondoprotocol/usdy/blob/main/contracts/usdy/USDY.sol) | [USDY basics](https://docs.ondo.finance/general-access-products/usdy/basics), [addresses](https://docs.ondo.finance/addresses) |

## On-chain verification

Etherscan labels the token address as a proxy and points to the implementation
above. The implementation is source-verified as an **Exact Match**, reports
contract name `USDY`, and compiles with Solidity `0.8.16` (optimiser enabled,
100 runs). Its verified source tree contains:

- `contracts/usdy/USDY.sol`;
- `BlocklistClientUpgradeable`, `AllowlistClientUpgradeable`, and
  `SanctionsListClientUpgradeable`;
- OpenZeppelin upgradeable ERC-20, pausable, access-control, and proxy
  dependencies; and
- the Chainalysis `ISanctionsList` interface.

The public Ondo repository contains a `USDY.sol` contract with the same
contract name, inheritance pattern, roles, initialiser parameters, and
transfer-restriction model. This is strong source-family evidence, but this
research did not build a particular Git commit to make a separate bytecode
comparison; Etherscan's verified implementation is the deployment-specific
source of record. A Sourcify source package was not obtained from the hosted
repository during this check, so no Sourcify assertion is made.

## Contract surface and behaviour

`USDY` inherits OpenZeppelin's
`ERC20PresetMinterPauserUpgradeable`, then adds issuer-controlled compliance
checks. The material externally callable functions are:

| Area | Functions | Effect |
| --- | --- | --- |
| ERC-20 | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `increaseAllowance`, `decreaseAllowance` | Standard 18-decimal ERC-20 interface, subject to the checks below. |
| Supply control | `mint(address,uint256)`, `burn(uint256)`, `burn(address,uint256)`, `burnFrom(address,uint256)` | Mint/burn capabilities are role-controlled; the two-argument `burn` is an explicit administrator burn protected by `BURNER_ROLE`. |
| Compliance configuration | `allowlist`, `blocklist`, `sanctionsList`, `setAllowlist`, `setBlocklist`, `setSanctionsList` | Reads or changes the three external compliance-list contract references. The setters require `LIST_CONFIGURER_ROLE`. |
| Administration | `grantRole`, `revokeRole`, `renounceRole`, `hasRole`, role-member enumeration | OpenZeppelin `AccessControlEnumerable` role management. Important exposed roles include `DEFAULT_ADMIN_ROLE`, `MINTER_ROLE`, `PAUSER_ROLE`, `BURNER_ROLE`, and `LIST_CONFIGURER_ROLE`. |
| Emergency control | `pause`, `unpause`, `paused` | Pauses/unpauses token operations for an account with `PAUSER_ROLE`. |
| Initialisation | overloaded `initialize(...)` | Used when deploying/configuring the proxy. The implementation constructor disables its own initialisers. |

Before mint, burn, transfer, or delegated transfer, `_beforeTokenTransfer`
checks the relevant sender and receiver: they must not be blocklisted or
sanctioned and must be allowlisted. For a delegated `transferFrom`, the
operator is also checked if it is neither the `from` nor the `to` address.
Consequently, USDY is not a freely transferable generic ERC-20 despite being
discoverable through the standard ERC-20 interface.

## Protocol-family conclusion

**Conclusion: Ondo's own RWA/USDY protocol (high confidence), using standard
OpenZeppelin upgradeable components rather than an external vault standard.**

The contract name and verified dependency tree match Ondo's public
[`ondoprotocol/usdy`](https://github.com/ondoprotocol/usdy) repository. That
repository describes USDY as a non-rebasing token whose yield accrues through
price appreciation, and describes its allowlist/blocklist/sanctions transfer
gates. Ondo's product documentation independently describes the accumulating
USDY form and its associated rebasing wrapper, rUSDY. The contract is therefore
best classified as an Ondo permissioned RWA token, not as ERC-4626, a
Centrifuge pool token, or a generic Securitize token.

## Related contracts that clarify the architecture

These are not substitutes for the USDY token address.

| Contract | Ethereum address | Purpose |
| --- | --- | --- |
| `USDY_InstantManager` | [`0xa42613C243b67BF6194Ac327795b926B4b491f15`](https://etherscan.io/address/0xa42613C243b67BF6194Ac327795b926B4b491f15) | Ondo's current on-chain subscription/redemption entry point. Official integration documentation specifies `subscribe(depositToken, depositAmount, minimumRwaReceived)` for USDC-to-USDY and `redeem(rwaAmount, receivingToken, minimumTokenReceived)` for USDY-to-USDC. Calling addresses must be registered in OndoIDRegistry. |
| `RWADynamicOracle` (USDY redemption-price oracle) | [`0xA0219AA5B31e65Bc920B5b6DFb8EdF0988121De0`](https://etherscan.io/address/0xA0219AA5B31e65Bc920B5b6DFb8EdF0988121De0) | Publishes the 18-decimal USDY price used by the manager. The docs describe time-ranged, compounding daily-interest-rate inputs and expose `getPrice`, `getPriceData`, and `getPriceHistorical`. |
| `rUSDY` | [`0xaf37c1167910ebC994e266949387d2c7C326b879`](https://etherscan.io/address/0xaf37c1167910ebC994e266949387d2c7C326b879) | Rebasing wrapper representation. It locks ordinary USDY and issues a token whose balance rebases; it should not be added as an independent fund without avoiding double counting. |
| Deprecated `USDYManager` | [`0x25A103A1D6AeC5967c1A4fe2039cdc514886b97e`](https://etherscan.io/address/0x25A103A1D6AeC5967c1A4fe2039cdc514886b97e) | The official address list marks this manager as deprecated. It illustrates the older RWAHub request/claim architecture but should not be selected for a new integration. |

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned
**971,667,626.830877926894936901 USDY** (18 decimals). The USDY token ABI has
no share-price accessor; the separate `RWADynamicOracle` exposes `getPrice`,
`getPriceData`, and `getPriceHistorical` for the redemption price.

## Integration implications

- A vault metadata adapter can safely identify the asset using the proxy
  address and standard ERC-20 reads, but must expect transfers and approvals
  involving unapproved addresses to fail because of compliance checks.
- NAV/yield cannot be derived from `totalSupply` alone: ordinary USDY accrues
  yield through its redemption price. Use the issuer's oracle/official pricing
  route, and account for rUSDY's wrapper relationship to avoid double counting.
- Resolve the active implementation at integration time because the token is
  upgradeable. Do not hard-code the implementation address as immutable
  protocol state.
- The manager adds KYC/identity registration, supported-token, rate-limit, and
  pause conditions beyond token-level allow-list checks.

## Primary sources

- [Etherscan proxy/token page](https://etherscan.io/address/0x96f6ef951840721adbf46ac996b59e0235cb985c)
- [Etherscan verified `USDY` implementation](https://etherscan.io/address/0xea0f7eebdc2ae40edfe33bf03d332f8a7f617528#code)
- [Ondo official smart-contract address list](https://docs.ondo.finance/addresses)
- [Ondo's public USDY repository](https://github.com/ondoprotocol/usdy)
- [Ondo USDY product documentation](https://docs.ondo.finance/general-access-products/usdy/basics)
- [Ondo InstantManager integration documentation](https://docs.ondo.finance/developer-guides/usdy-instant-manager-integration)
