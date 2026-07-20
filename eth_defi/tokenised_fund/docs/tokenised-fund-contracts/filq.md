# FILQ contract research

Checked on 2026-07-17. This note identifies the public Ethereum token
deployments and their technical interfaces; it is not an assessment of
investor eligibility, legal ownership, custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Fidelity USD Digital Liquidity Fund, accumulating class (FILQ-A) | Ethereum ERC-20 UUPS proxy, [`0x54a4fc78431f9201824643e99bec891bb7462a1d`](https://etherscan.io/address/0x54a4fc78431f9201824643e99bec891bb7462a1d#code) | `UUPSProxy`; current verified implementation [`SygToken`](https://sourcify.dev/server/v2/contract/1/0x7030fe438be6ed196b8886616bbf5a245c267339?fields=all) at `0x7030fe438Be6Ed196B8886616BBF5a245c267339` | A 2-decimal, permissioned fund-share ERC-20 using Sygnum's current `SygToken` standard. It has SygFactory-backed wallet roles/blacklisting, issuer mint/burn and forced-transfer powers, pause control, EIP-2612 permits, an optional Chainlink data-feed interface, and timelocked UUPS upgrades. | No public repository for this exact 2026 `SygToken` source tree was found. [Sygnum's historical security-token contracts](https://github.com/sygnumbank/solidity-equity-token-contracts) are a documented predecessor/family reference, not the deployed source. | [FILQ product page](https://www.sygnum.com/filq/), [Sygnum FILQ launch architecture](https://www.sygnum.com/news/sygnum-powers-fidelity-internationals-first-tokenized-product-launch-with-moodys-aaa-mf-assessment/), [Fidelity prospectus](https://www.fidelityinternational.com/legal/documents/FISGF/en/pr.fisgf.en.xx.pdf) |
| Fidelity USD Digital Liquidity Fund, distributing class (FILQ-D) | Ethereum ERC-20 UUPS proxy, [`0xf0db6f529581e7f6ebac7a7f6882923c00fc3a66`](https://etherscan.io/address/0xf0db6f529581e7f6ebac7a7f6882923c00fc3a66#code) | `UUPSProxy`; same current `SygToken` implementation | The separate 2-decimal distributing share class. The implementation and permission manager are the same as FILQ-A; its configured data-feed address is distinct. | Same as FILQ-A. | Same as FILQ-A. |

## On-chain verification

The two supplied addresses are not duplicate deployments: direct calls identify
`0x54a4...2a1d` as **Fidelity USD Digital Liquidity Fund-Acc** / `FILQ-A` and
`0xf0db...3a66` as **Fidelity USD Digital Liquidity Fund-Dist** / `FILQ-D`.
Both report two decimals and store the same EIP-1967 implementation address,
`0x7030fe438Be6Ed196B8886616BBF5a245c267339`.

Sourcify v2 reports **exact creation and runtime matches** for both token
proxies as `UUPSProxy`, and an exact match for the shared implementation as
`SygToken`. The verified `SygToken` source is Solidity `0.8.30` and declares
Sygnum as the author/security contact. Its source tree includes `SygFactory`
permission-management interfaces, `ERC20SygToken`, roles, pausing, Chainlink
data-feed support, fund recovery, and Sygnum's timelocked UUPS components.
The public Etherscan pages linked above remain useful explorer references; the
Sourcify exact matches are the source-verification evidence used in this note.

Each proxy's `getPermissionManagerAddress()` returns
[`0x7427f3E0e32eb1ee19516aa5c6AbC99267a3eC89`](https://etherscan.io/address/0x7427f3e0e32eb1ee19516aa5c6abc99267a3ec89),
which is the external authority consulted for wallet roles. The class-specific
`getPriceFeedOracleAddress()` values are:

| Token class | Configured oracle address |
| --- | --- |
| FILQ-A | [`0x0c6c789A375cC4ee9CE6008715C915A91dA5AC5c`](https://etherscan.io/address/0x0c6c789a375cc4ee9ce6008715c915a91da5ac5c) |
| FILQ-D | [`0x7484379D1Af1B718DCCC6BB5e58AAdbcB6E4866A`](https://etherscan.io/address/0x7484379d1af1b718dccc6bb5e58aadbcb6e4866a) |

## Contract surface and behaviour

| Area | Functions | Effect |
| --- | --- | --- |
| ERC-20 and permit | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `permit`, `nonces`, `DOMAIN_SEPARATOR` | Standard ERC-20 mechanics plus EIP-2612 approval, all subject to SygToken's permission and pause checks. |
| Wallet permissioning | `getPermissionManagerAddress`, `isTokenUser`, `isTokenUserManager`, `isMinterBurner`, `isOperator`, `isPauser`, `isRoleManager`, `isSystem`, `updateTokenUser`, `blacklistTokenUser` | Roles are delegated to the external SygFactory permission manager. The verified source requires approved token users for token movement and makes blacklist status override whitelist status. |
| Issuance and exceptional movement | `mint`, `burn`, `forcedTransfer`, `updateMinterBurner` | Authorised roles can create/burn fund-share tokens and force a transfer to an approved recipient. These are issuer/administrator functions, not public ERC-4626 vault flows. |
| Price and metadata | `getPrice`, `getBundleData`, `getPriceFeedOracleAddress`, `getPriceFeedDescription`, `getPriceFeedDecimals`, `updatePricefeedOracleAddress`, `getTokenURI`, `updateTokenURI` | Provides an optional Chainlink AggregatorV3 or bundle-feed interface and token metadata URI. Sygnum states that Chainlink publishes FILQ's daily NAV and distribution metrics. |
| Emergency and recovery | `pause`, `unpause`, `isPaused`, `recoverFunds` | Authorised operators can halt token actions; the contract can recover accidentally held ETH/ERC-20 assets. |
| Upgrade control | `upgradeToAndCall`, `executeUpgrade`, `cancelUpgrade`, `getScheduledOperationDetails`, `getImplementation` | Proxy logic uses a Sygnum UUPS implementation with a scheduled/timelocked upgrade flow. The implementation's upgrade authorisation is bound to the permission manager. |

Sygnum describes FILQ as Ethereum ERC-20 tokens in a permissioned model where
only approved wallets transact and a transfer agent maintains access and
ownership records. Its product material says that the accumulating class
compounds yield into NAV whereas the distributing class pays monthly dividends
with a constant one-USD NAV structure. The token contract itself does not
expose an ERC-4626 `asset`, `deposit`, `withdraw`, or `redeem` interface.

## Protocol-family conclusion

**Conclusion: Sygnum's proprietary `SygToken`/Desygnate permissioned
tokenisation protocol (high confidence), not ERC-4626.**

This is a direct source-level identification, not merely a thematic match. The
active implementation is explicitly called `SygToken`, its exact verified
source names Sygnum and delegates permissions to `SygFactory`, and Sygnum's
official FILQ announcement says Desygnate provides the on-chain fund registry,
smart-contract settlement, and stablecoin subscription/redemption architecture.
The ABI's role, blacklist, forced-transfer, price-feed, and timelocked upgrade
families match a regulated security-token standard rather than a permissionless
money-market vault.

Sygnum's public GitHub repository documents an older `SygnumToken` security
token family with whitelist, role, mint/burn, pause, freeze/confiscation, and
proxy functionality. It is compelling historical/protocol-family evidence, but
it uses older source names and Solidity tooling and must **not** be treated as
the exact deployed `SygToken` version. Searches for the exact names
`SygToken`, `UUPSTimelockUpgradeable`, and the verified 2026 source paths did
not locate a public issuer repository containing this implementation.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` returned **448,264.28 FILQ-A** and
**300,377.53 FILQ-D** on Ethereum (both 2 decimals). The `SygToken` ABI exposes
configured Chainlink bundle-feed accessors. FILQ-A uses proxy
`0x0c6c...ac5c` and data id `02000001220700030000000000000000`; FILQ-D uses
proxy `0x7484...866a` and data id `02000001230700030000000000000000`.
Both proxies resolve to DataFeedsCache `0x16b5...1433`, which emits
`BundleReportUpdated(bytes16,uint256,bytes)` for every accepted daily report.

`getPrice()` is the single-value AggregatorV3 route and reverts for these
bundle feeds. In both reviewed schemas NAV/share is the second 32-byte bundle
word. `bundleDecimals()` scales it by four decimals for FILQ-A and two for
FILQ-D. Current and fixed-block reads use `latestBundle()`; historical report
discovery uses the cache event through Hypersync.

## Integration implications

- Track the two token proxies as distinct FILQ share classes, while treating
  their common implementation and permission manager as shared infrastructure.
  Do not merge their supplies merely because both represent the same fund.
- Classify FILQ as a **permissioned tokenised fund / ERC-20**, not an
  ERC-4626 vault. Standard token reads are available, but transfers, approvals,
  permits, subscriptions, and redemptions are subject to approved-wallet and
  operational processes.
- Do not assume a wallet can transfer based solely on its ERC-20 balance. The
  token checks user eligibility through SygFactory and has issuer-authorised
  blacklisting, pausing, mint/burn, and forced-transfer capabilities.
- Use the official product/transfer-agent workflow for issue and redemption.
  The issuer documents 24/7 eBanking access after onboarding, with market-hour
  settlement behaviour and potential out-of-hours queueing or fees.
- Resolve proxy implementations at integration time and monitor timelocked
  upgrades, the external permission-manager address, role membership, pause
  state, each class's configured oracle, data id, bundle decimals and cache
  aggregator. Chainlink NAV establishes valuation but does not establish
  independent redeemability.

## Primary sources

- [Sourcify exact-match FILQ-A `UUPSProxy`](https://sourcify.dev/server/v2/contract/1/0x54a4fc78431f9201824643e99bec891bb7462a1d?fields=all)
- [Sourcify exact-match FILQ-D `UUPSProxy`](https://sourcify.dev/server/v2/contract/1/0xf0db6f529581e7f6ebac7a7f6882923c00fc3a66?fields=all)
- [Sourcify exact-match `SygToken` implementation](https://sourcify.dev/server/v2/contract/1/0x7030fe438be6ed196b8886616bbf5a245c267339?fields=all)
- [Verified Chainlink DataFeedsCache](https://etherscan.io/address/0x16b53825c8ceaea593507274d4c1aaec9e261433#code)
- [Sygnum FILQ page](https://www.sygnum.com/filq/)
- [Sygnum's FILQ launch and Desygnate architecture](https://www.sygnum.com/news/sygnum-powers-fidelity-internationals-first-tokenized-product-launch-with-moodys-aaa-mf-assessment/)
- [Fidelity International Strategies Funds SPC prospectus](https://www.fidelityinternational.com/legal/documents/FISGF/en/pr.fisgf.en.xx.pdf)
- [Sygnum historical security-token GitHub repository](https://github.com/sygnumbank/solidity-equity-token-contracts)
