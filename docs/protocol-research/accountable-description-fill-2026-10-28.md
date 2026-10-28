# Accountable manual description research

Research date: 2026-10-28. The published export contained 33 Accountable vaults,
12 without full or short descriptions, and one with a description from another
chain. This change adds the Avalanche Travessia fallback alongside the other
reviewed deployments.

## Reviewed deployments

| Vault | Chain | Share-token address | Result |
|---|---|---|---|
| Travessia Tauri AUSD | Ethereum | `0x4c18e2bb9942b12b28e780acf2d9ec2dda126df9` | Add strategy description and official link |
| Travessia Tauri USDC | Ethereum | `0xf84a37545e313e8177d80039158ed478c7dc9079` | Add strategy description and official link |
| Travessia Tauri USDT | Ethereum | `0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35` | Override incorrect Pelican pGAIN description and link |
| Travessia Tauri AUSD | Monad | `0x8d24b48cb736addb5b7069565f192a052f42ba61` | Add strategy description and official link |
| Travessia Tauri USDT | Avalanche | `0x9c3da70cd7fedeaef528d060ea99ace133e08fd2` | Add strategy description, official link and note |
| Noon Symphony Yield Vault | Arbitrum | `0x9fe29a3fec55414f70454e2dbe6522f48ced3e85` | Add business context and protocol-level yield model |
| sUSN Delta Neutral Yield Vault | Ethereum | `0xa73424f1ac94b3ef0d0c9af4f2967c87d4af25d9` | Add limited Noon product context |
| Noon Sophon Yield Vault | Ethereum | `0x0963c0f17d77e336ff75ed36c27d3a1522847615` | Add historical context; distinguish Ethereum deployment from Sophon |
| Asia Credit Yield Vault | Monad | `0x4c0d041889281531ff060290d71091401caa786d` | Add SharpByte/Mu Digital strategy |
| Neutral Trade Autopilot | Monad | `0xaabab7598be3c4fe58c593e73c2f5934b73b573e` | Add Autopilot strategy and confirmed company backers |

## Evidence and copy boundaries

### Travessia

The [official Tauri vault page](https://www.travessiacredit.com/vaults/43114/tauri/0x9c3da70cD7feDEAeF528d060ea99AcE133E08Fd2)
describes financing Tauri Agrícola's short-cycle grain purchases and logistics,
repayment from operating cash flow, borrower first loss and currency hedging.
The [operator case study](https://docs.travessiacredit.com/pages/tauri-case-study.html)
identifies soy, grain and rice, and the buyer industries.
The [Travessia homepage](https://www.travessiacredit.com/) names Rabobank,
Avalanche, RedStone, Accountable and Royco as organisations it works with;
these are not described as investors or guarantors.

The public deployment configuration loaded by the [vault application](https://www.travessiacredit.com/vaults)
maps all five reviewed chain/address pairs to Tauri and identifies their deposit
assets. The Ethereum USDC and USDT detail pages were fetched successfully and
displayed the matching vault addresses. Their paths follow
`/vaults/{chain_id}/tauri/{share_address}`.
The Ethereum AUSD and Monad AUSD links use the same route and addresses from
that deployment catalogue; their detail pages were not independently fetched.

### Noon

Noon's [yield model](https://docs.noon.capital/1.-noon-the-basics/how-it-works)
describes USN/sUSN and diversified Treasury, credit, lending and trading income.
Its [vault documentation](https://docs.noon.capital/1.-noon-the-basics/vaults)
explicitly distinguishes each vault's strategy from the blended sUSN return.
Consequently, the fallbacks describe protocol-level context without claiming
that a historical contract currently holds a particular asset, uses leverage,
or shares another vault's redemption schedule.

Symphony's [launch announcement](https://www.symphony.io/blog/symphony-is-live-get-rewarded-for-saving-not-spending)
identifies Noon as infrastructure behind its consumer savings and rewards
product. It supports the business relationship, not a contract-level holdings
attestation. No fixed yield is copied into the fallback.

The [verified Sophon-named vault contract](https://etherscan.io/address/0x0963c0f17d77e336ff75ed36c27d3a1522847615#code)
is deployed on Ethereum. Noon's [deployment catalogue](https://docs.noon.capital/6.-resources/contract-addresses-and-oracles)
lists the separate Sophon token deployments as deprecated. The contract name
supports the historical ecosystem association; it does not establish current
holdings or that the Ethereum vault itself has been closed.

### Asia credit

Accountable's [Monad launch announcement](https://www.linkedin.com/posts/accountablecapital_verifiable-yield-arrives-on-monad-foundation-activity-7398798395642982401-6WqP)
identifies SharpByte Capital and Mu Digital and describes a USDC strategy
combining staked AZND, muBOND and liquidity positions. The description retains
this launch-time attribution, without asserting current allocation weights.
[SharpByte's website](https://www.sharpbyte.xyz/) confirms its investment and
vault-curation business. No unverified financial backers are added.

### Neutral Autopilot

Neutral Trade's [strategy page](https://www.neutral.trade/strategies/neutral-autopilot)
describes allocation across market-neutral child strategies and rebalancing.
Its [strategy directory](https://www.neutral.trade/strategies?tier=marketplace)
lists Monad, Ethereum and Solana availability. The
[Ethereum launch discussion](https://www.neutral.trade/blog/september-october-2026-update)
confirms the Accountable relationship. The strategy description is applied to
the named Monad Autopilot deployment, without copying Solana custody guarantees
or Ethereum-specific withdrawal promises.

The [company FAQ](https://www.neutral.trade/faq) names Solana Labs, DRW Labs,
Skyland Ventures, BlackPine and Monke Ventures as company backers. These are
company investments, not guarantees of a vault's returns.

## X research

Searches covered `noon_capital`, `AccountableData`, SharpByte/Mu Digital,
Symphony, Travessia/Tauri, Neutral Autopilot and the three unresolved names and
addresses below. Indexed X mirrors corroborated Noon's Symphony relationship
and Sophon decommissioning, but do not establish holdings of the historical
vault contracts. Direct access to the linked Sophon announcement on X returned
HTTP 403; no connected Chrome tool was available. Official documentation and
first-party announcements are the cited sources for public copy.

## Unresolved deployments

| Vault | Chain | Address | Missing evidence |
|---|---|---|---|
| BR RWA15 Vault | Ethereum | `0x15ebd88e8898233128ffcc6e9c2dab1d2d7e1e86` | No attributable public strategy or manager disclosure found |
| Systematic Delta Neutral | Monad | `0x1eee675492a0f975229464b8dc8c795a0f1fe02a` | Generic name cannot identify a specific manager or trading strategy |
| Syntetika Testing Vault | Monad | `0x45c54b41bc9f7c7cfdfe9ba383ec6973ebf2cc98` | Syntetika's public BTC products are on other infrastructure; no evidence connects their strategy to this USDC testing contract |

Web and X searches for the exact names and addresses did not produce reliable
deployment-specific disclosures. Leave their descriptions empty pending a
manager factsheet or attributable announcement; do not copy a similarly named
product's strategy or substitute an unsupported generic investment description.

## Cross-chain metadata collision

The current Accountable API identifies Pelican pGAIN on chain 1672 at the same
share-token address as Travessia USDT on Ethereum. The address-only API cache
therefore supplied Pelican's strategy and link to the Ethereum row. The reviewed
Ethereum fallback takes precedence and repairs its description and homepage.
The API's manager name is still read through the address-only lookup and can
therefore still identify Pelican as the Ethereum vault's manager. This change
does not repair manager or curator attribution. The general API cache's
chain-scoping design remains a separate issue.

## Publishing

The public copy was edited for a general audience: it explains how deposits
are used and where returns come from, replaces trading jargon with plain
language, and distinguishes company backers from business partners. Noon's
descriptions explicitly state that its general investment approach does not
establish these historical vaults' current holdings. Network retirement details
remain in the source-linked Sophon note rather than the strategy description.

These are source-code fallbacks. Metadata scanning and JSON export are needed
after deployment to publish them. The description-only backfill script can
refresh descriptions, but it does not update links or notes. No production
metadata, price history or reader state was modified during this work.
