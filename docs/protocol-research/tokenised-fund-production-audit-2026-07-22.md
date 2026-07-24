# Tokenised fund production audit

This audit compares the production vault metadata snapshot dated 2026-07-20 with issuer product pages, fund documents, contract metadata and the current adapters. The snapshot contains 39,531 metadata rows, of which 32 rows are flagged as tokenised funds. Repeated deployments account for five BUIDL chains and two ULTRA chains.

## Findings

- mTBILL was incorrectly exported as USDC-denominated. Midas documents an mTBILL/USD oracle; USDC is an accepted issuance payment token. The adapter and metadata migration now export synthetic USD for mTBILL.
- OpenEden TBILL correctly retains USDC as its denomination token. OpenEden defines the token price in USDC, assumes USDC/USD at one and uses USDC for subscriptions and redemptions.
- FDIT and CASHx production rows still contain USD metadata from an older scan. Current supply-only adapters deliberately export no denomination until a machine-readable historical NAV source is integrated. Their legal or share-class currency is nevertheless USD.
- ULTRA is described by its issuer as USD-denominated, but the adapter deliberately leaves denomination and NAV unavailable until a verified public NAV source is configured.
- The remaining production rows consistently use USD as their accounting or published NAV currency. Synthetic USD metadata is expected where there is no transferable ERC-20 denomination token.
- Generic or stale product links were present for MONY, BELIF, CUMIU, mTBILL, BUIDL-I, MI4 and USTBL. Current adapters now resolve individual product sources where a reliable public page exists; a metadata refresh is still required to replace cached production rows.

## Product review

| Product | Production denomination | Reviewed result | Primary evidence |
|---|---:|---|---|
| Fidelity FDIT | USD | USD share class; current supply-only adapter intentionally exports no denomination or NAV | [Fidelity product page](https://institutional.fidelity.com/app/funds-and-products/9053/fidelity-treasury-digital-fund-onchain-class-fyoxx.html) |
| KAIO CASHx | USD | USD feeder/share class; current supply-only adapter intentionally exports no denomination or NAV | [BlackRock underlying fund](https://www.blackrock.com/cash/en-gb/products/229271/blackrock-ics-us-dollar-liquidity-select-acc-fund) |
| J.P. Morgan MONY | USD | Correct synthetic USD accounting denomination | [MONY launch announcement](https://www.prnewswire.com/news-releases/jp-morgan-asset-management-launches-its-first-tokenized-money-market-fund-302642262.html) |
| ChinaAMC CUMIU | USD | Correct synthetic USD; issuer page and onchain `currencyNAV()` both identify USD | [ChinaAMC product page](https://www.chinaamc.com.hk/product/chinaamc-usd-digital-money-market-fund-listedclass/) |
| Bosera BELIF | USD | Correct synthetic USD; onchain `currencyNAV()` identifies USD | [BELIF contract](https://etherscan.io/token/0x237c717df1b60501f8d029d3fe7385fd090df180) |
| Wellington ULTRA | None | Publicly described as USD-denominated; scanner remains conservative until NAV is configured | [Libeara launch announcement](https://libeara.com/libeara-partners-with-wellington-and-fundbridge-capital-to-launch-a-u-s-treasuries-fund-tokenised-on-public-blockchain/) |
| Midas mTBILL | USDC | Incorrect; corrected to synthetic USD because the oracle is mTBILL/USD | [Midas contract registry](https://docs.midas.app/protocol-mechanics/smart-contracts) |
| Ondo OUSG | USD | Correct synthetic USD | [OUSG overview](https://docs.ondo.finance/qualified-access-products/ousg/overview) |
| Ondo USDY | USD | Correct synthetic USD | [USDY overview](https://docs.ondo.finance/general-access-products/usdy/basics) |
| OpenEden TBILL | USDC | Correct USDC quote and dealing token | [Token-price methodology](https://docs.openeden.com/tbill/token-price) |
| BlackRock BUIDL and BUIDL-I | USD | Correct synthetic USD; stable USD 1 share-value model | [BlackRock BUIDL](https://www.blackrock.com/us/individual/products/buidl/) |
| Apollo ACRED | USD | Correct USD NAV currency | [ACRED fund page](https://securitize.io/primary-market/apollo-diversified-credit-securitize-fund) |
| VanEck VBILL | USD | Correct USD NAV currency | [VBILL fund page](https://securitize.io/primary-market/vaneck-vbill) |
| BNY/Securitize STAC | USD | Correct USD NAV currency | [STAC fund page](https://securitize.io/primary-market/Securitize-BNY-CLO-Fund) |
| Arca RCOIN | USD | Correct USD NAV currency | [Arca fund page](https://www.arcalabs.com/fund-overview) |
| SPiCE SPICE | USD | Correct USD reporting currency | [SPiCE NAV reports](https://spicevc.com/nav-reports.html) |
| Hamilton Lane HLSCOPE | USD | Correct USD NAV currency | [SCOPE fund page](https://www.hamiltonlane.com/en-us/strategies/evergreen/global/senior-credit-opportunities-fund) |
| Blockchain Capital BCAP | USD | Correct USD valuation currency | [Securitize BCAP oracle announcement](https://investors.securitize.io/news/news-details/2025/RedStone-and-Securitize-Catalyze-20Bn-RWA-Market-with-First-Ever-BCAP-Price-Feed-on-ZKsync-05-06-2025/default.aspx) |
| COSIMO X | USD | Correct USD NAV currency | [COSIMO X fund page](https://www.cosimodigital.com/asset-management/cosimo-x) |
| Science Blockchain SCI2 | USD | Correct USD reporting currency | [SCI2 investor dashboard](https://science.securitize.io/) |
| Protos PRTS | USD | Correct USD NAV reporting currency | [PRTS NAV report](https://protosmanagement.com/2024/05/09/protos-asset-management-releases-march-31-2024-prts-token-nav/) |
| Mantle Index Four MI4 | USD | Correct USD NAV currency; product link updated from the manager homepage | [MI4 fund page](https://securitize.io/primary-market/mantle-index-four-fund) |
| Spiko USTBL | USD | Correct synthetic USD | [Spiko USD fund page](https://www.spiko.io/spiko-treasury-bills-dollar) |
| Superstate USTB | USD | Correct synthetic USD | [USTB fund page](https://superstate.com/ustb) |
| Fidelity/Sygnum FILQ-A and FILQ-D | USD | Correct synthetic USD | [FILQ product page](https://www.sygnum.com/filq/) |

## Price-history coverage

The raw production price file contains history for 20 of the 32 flagged addresses. Twelve rows currently have no raw history: FDIT, CASHx, MONY, both ULTRA deployments, COSX, PRTS, RCOIN, SCI2, SPICE, FILQ-A and FILQ-D. This is expected for supply-only or newly integrated products and for products whose NAV source is intentionally unconfigured; it must not be interpreted as a zero NAV.

Most recorded price-read errors occur before a product's configured oracle or feed boundary, especially for RedStone-backed Securitize funds. They are historical boundary errors rather than evidence that the current feed is failing.

## Operational follow-up

Run the ordinary metadata refresh/backfill after deploying these changes. For an existing production metadata database, the Midas denomination migration can be previewed in dry-run mode before applying it. Production Parquet history does not store denomination metadata and does not need to be discarded or rebuilt.
