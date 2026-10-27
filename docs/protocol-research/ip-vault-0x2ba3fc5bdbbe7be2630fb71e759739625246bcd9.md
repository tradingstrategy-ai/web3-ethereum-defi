# IP Vault source and GitHub investigation

Research date: 2026-10-27. RoyaltyVault has verified Solidity source on
Soneium Blockscout, but no matching public GitHub development repository was
found. Live governance settings differ materially from the published documentation.

## Identity and source

- Protocol: IP Vault, the royalty investment product associated with [ipUSD](https://ipusd.xyz/).
- Chain: Soneium, chain ID 1868.
- Vault proxy: [0x2Ba3FC5BDbBE7BE2630fB71e759739625246bCd9](https://soneium.blockscout.com/address/0x2Ba3FC5BDbBE7BE2630fB71e759739625246bCd9?tab=contract).
- Verified RoyaltyVault implementation: [0x8CfDe91D3a4c55b426732f17dF1745E6d0603e73](https://soneium.blockscout.com/address/0x8CfDe91D3a4c55b426732f17dF1745E6d0603e73?tab=contract).
- Machine-readable source and ABI: [Blockscout contract API](https://soneium.blockscout.com/api/v2/smart-contracts/0x8CfDe91D3a4c55b426732f17dF1745E6d0603e73).
- Source path: `project/contracts/RoyaltyVault.sol`; Solidity pragma `^0.8.28`, compiled with `0.8.36+commit.8a079791`, OpenZeppelin dependencies 5.6.1.
- Verification timestamp: 2026-10-24 18:48:14 UTC. The source carries an MIT SPDX header, although the explorer API's separate licence field says `none`.
- Proxy creator: `0xf70d9942CA1865C4e11ADb1DB140Ba05d4F9531E`; no project name was exposed in the explorer response.
- Documentation: [ipUSD documentation](https://ipusd.gitbook.io/ipusd-docs/ipusd).
- Announcement: [Soneium on X](https://x.com/soneium/status/2114999606400692706).

## GitHub search result

GitHub code searches for the vault proxy, implementation, creator,
`DirectVaultDisabled`, `receiveCashAndMint`, `IPUSDWrapper` and `ipusd.xyz`
returned no matches. Searching `RoyaltyVault` produced unrelated contracts,
including [Joyn's royalty splitter](https://github.com/code-423n4/2022-03-joyn/blob/1b716a1358ea8bf825c79864e793bec67c0a36fb/royalty-vault/contracts/RoyaltyVault.sol)
and a [Story Protocol and Aave example](https://github.com/thedatafoundation/story-examples/blob/094dfeaf088f0b22a2ea1cf778b6aa68cec360e9/royalty-yield-vault/contracts/RoyaltyVault.sol).
Their inheritance, asset model and callable methods differ from the deployed ipUSD vault.

Asseto's website links to [AssetoFinance on GitHub](https://github.com/AssetoFinance).
Its public repository listing contained only `DefiLlama-Adapters` and
`defillama-server`. No development repository or audit report for this specific
RoyaltyVault deployment was identified in the project website, reviewed docs or
search results. This does not establish that no private or unindexed repository
or audit exists. A final repository-scoped DeFiLlama search hit GitHub's search
rate limit; the successful global exact-address search had already returned no matches.

## Contract behaviour and integration implications

RoyaltyVault is an upgradeable ERC-20 share token named `Staked IPUSD`, with
symbol `sIPUSD`. It inherits `ERC20Upgradeable`, not `ERC4626`.
Although it exposes `asset()`, `totalAssets()`, `convertToAssets()` and
`convertToShares()`, its standard `deposit`, `mint`, `withdraw` and `redeem`
entrypoints always revert with `DirectVaultDisabled`.

The authorised PreDeposit contract mints shares through `receiveCashAndMint()`
and handles instant exits. The authorised RedeemQueue calls `redeemFromQueue()`.
Custom events include `CashMinted`, `InstantExit` and `RedeemedFromQueue`;
the ABI has no standard ERC-4626 Deposit or Withdraw events. This requires
explicit discovery and protocol-specific flow handling in our integration.

`totalAssets()` includes the actual ipUSD balance, outstanding purchase and
redemption amounts, and accounted RWA holdings valued through
[RwaManager](https://soneium.blockscout.com/address/0x741a926c6FaE0f357aB0194DcE770767B21D8e31?tab=contract).
Its `quoteLenient()` can reuse the last accepted valuation when the current
oracle result is stale. Outstanding amounts remain valued at their booked
amount until settlement, refund or an applicable write-off. Reported assets
therefore need separate freshness and liquidity indicators.

`cashReserve` is a separate accounting balance used by withdrawal paths.
Direct ipUSD transfers increase `totalAssets()` without increasing
`cashReserve`. The admin can extract cash for investment, settle positions,
record purchase losses and upgrade the implementation.

The [published fee schedule](https://ipusd.gitbook.io/ipusd-docs/vault-and-protocol/fees-limits-and-risks)
states a 5% instant withdrawal fee and no protocol subscription or standard
withdrawal fee. These are documented product terms, not live fee reads in this investigation.

## Governance discrepancies

The [contracts and roles documentation](https://ipusd.gitbook.io/ipusd-docs/technical-reference/contracts-roles-and-disclosures)
says the Soneium timelock is fixed at three days and anyone may execute an
operation after its delay. The verified
[AdminTimelock source](https://soneium.blockscout.com/address/0x921530D47da54b3461704A804b4F9E686989B77F?tab=contract)
and live reads show different behaviour:

| Item | Source and live evidence |
| --- | --- |
| Operation delay | `DELAY()` returned 180 seconds: three minutes, not three days. |
| Delay changes | `setDelay()` is callable directly by the admin. It accepts a positive delay up to 30 days, including one second. Existing scheduled operations retain their original execution time. |
| Execution | `execute()` requires the admin or operator; arbitrary callers revert with `NotExecutor`. |
| Role holders | Admin, operator and guardian all returned `0xc7efCF39c0722a0d27f101cc79Fd00420bf077c4`. Logical roles currently share the same address. |
| Admin handoff | `HANDOFF_DELAY()` returned 600 seconds. The admin can change the delay for future handoffs. |
| Vault ownership | RoyaltyVault's `admin()` returned the documented AdminTimelock address. |

Reads used the configured Soneium RPC at block **29979401** on 2026-10-27.
At that block, vault `totalAssets()`, `totalSupply()` and `cashReserve()` were
all zero. These observations precede the announced opening and may change.
The three-day protection must not be represented as an enforced live guarantee
without rechecking configuration and the admin's ability to change it.
