# Midas product audit (2026-08-04)

## Decision

Only **mTBILL** is approved as a `tokenised_fund` at this time.  It has
reviewed short and long product descriptions in `MIDAS_PRODUCT_METADATA` and
the migration applies them to every existing mTBILL record.

This is deliberately a product-level decision, not a protocol-level one.
Midas describes mTokens as financial instruments and says that they are not
DeFi vaults.  That does not make every strategy token a tokenised fund for our
catalogue.  A product gets the flag only after a review of its issuer,
underlying portfolio, investor rights and suitable primary documentation.

The current mTBILL decision is supported by the product documentation, its
prospectus/secured-loan structure, the short-duration U.S. Treasury-bill
portfolio, and independently published NAV attestations:

- <https://midas.app/mtbill>
- <https://docs.midas.app/tokens/mtbill>
- <https://docs.midas.app/tokens/mtbill/independent-reporting>
- <https://docs.midas.app/tokens/mtbill/bankruptcy-remoteness>

## Registry scope and status

The static registry snapshot contains **80 distinct symbols and 149 chain
deployments**.  This table is deliberately by symbol: a classification and its
copy must be identical across each deployment of the same product.  Chain IDs
are the registry's raw IDs, including testnets, bridges, incomplete records and
third-party/white-label products.

`Approved fund` means set `tokenised_fund` and keep product copy current.
`Not a fund` means do not set it.  `Needs product review` means do not set it
or invent a description until there is an issuer page, legal terms and a clear
portfolio/investor-rights review.  This is intentionally more conservative
than treating the `m` prefix or a Midas integration as proof of fund status.

| Product | Deployments (chain IDs) | Product assessment | Fund decision | Description assessment and next action |
| --- | --- | --- | --- | --- |
| `mTBILL` | 1, 30, 8453, 23294, 42161, 42793, 98866, 421614, 11155111 | Midas U.S. Treasury-bill investment product | **Approved fund** | **Updated**: registry has reviewed short/long copy and official link. |
| `mBASIS` | 1, 8453, 42161, 42793, 98866, 11155111 | Crypto funding-rate/basis strategy | Not a fund | Official strategy documentation exists; do not label as a fund. |
| `mBTC` | 1, 30, 11155111 | Bitcoin yield strategy product | Not a fund | Official product documentation exists; do not label as a fund. |
| `mEDGE` | 1, 143, 239, 8453, 16661, 42161, 98866, 11155111 | Delta-neutral DeFi yield strategy | Not a fund | Official strategy documentation exists; do not label as a fund. |
| `mMEV` | 1, 2390, 8453, 16661, 42161, 42793, 98866, 11155111 | Delta-neutral DeFi yield strategy | Not a fund | Official strategy documentation exists; do not label as a fund. |
| `mRE7` | 1, 239, 8453, 16661, 42161, 42793, 11155111 | Managed mToken; mandate not reviewed here | Needs product review | Obtain current manager page, terms and portfolio mandate before adding copy or flag. |
| `mSL` | 1, 8453, 42793, 98866, 11155111 | Midas Staked Liquidity / liquidity architecture token | Not a fund | Infrastructure/liquidity product, not a pooled investment-fund classification. |
| `mFONE` | 1, 11155111 | Managed mToken; mandate not reviewed here | Needs product review | Obtain the product page and terms before adding copy or flag. |
| `mHYPER` | 1, 143, 9745, 747474 | Managed mToken; mandate not reviewed here | Needs product review | Obtain the product page and terms before adding copy or flag. |
| `mAPOLLO` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain the product page and terms before adding copy or flag. |
| `mLIQUIDITY` | 1, 8453, 42793, 98866, 11155111 | Liquidity-oriented mToken; mandate not reviewed here | Needs product review | Obtain the product page and terms before adding copy or flag. |
| `hypeETH` | 1, 11155111 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `hypeBTC` | 1, 11155111 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `hypeUSD` | 1, 11155111 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `tUSDe` | 1, 11155111 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `tETH` | 1, 11155111 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `tBTC` | 1, 11155111 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `mevBTC` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mFARM` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `msyrupUSD` | 1 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `msyrupUSDp` | 1 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `TACmBTC` | 1 | Bridge/wrapped representation of mBTC | Not a fund | Bridge representation; do not duplicate an underlying product classification. |
| `TACmEDGE` | 1 | Bridge/wrapped representation of mEDGE | Not a fund | Bridge representation; do not duplicate an underlying product classification. |
| `TACmMEV` | 1 | Bridge/wrapped representation of mMEV | Not a fund | Bridge/wrapped representation; mMEV itself is a strategy, not a fund. |
| `zeroGUSDV` | 1 | Bridge/wrapped representation | Not a fund | Bridge representation; no standalone product copy. |
| `zeroGETHV` | 1 | Bridge/wrapped representation | Not a fund | Bridge representation; no standalone product copy. |
| `zeroGBTCV` | 1 | Bridge/wrapped representation | Not a fund | Bridge representation; no standalone product copy. |
| `JIV` | 1 | Managed/third-party product | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mRE7BTC` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain current terms and portfolio mandate before adding copy or flag. |
| `acremBTC1` | 1 | Managed/third-party product | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mWildUSD` | 1 | Managed/third-party product | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mEVUSD` | 1, 8453 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mEVETH` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `obeatUSD` | 1, 999 | Managed/third-party product | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mHyperETH` | 1, 30, 143 | Managed mToken; mandate not reviewed here | Needs product review | Obtain current terms and portfolio mandate before adding copy or flag. |
| `mHyperBTC` | 1, 30, 143 | Managed mToken; mandate not reviewed here | Needs product review | Obtain current terms and portfolio mandate before adding copy or flag. |
| `mPortofino` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mKRalpha` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mROX` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mTU` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mM1USD` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mGLOBAL` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `bondUSD` | 1, 16661 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `bondETH` | 1, 16661 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `bondBTC` | 1, 16661 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `stockMarketTRBasisTrade` | 1 | Turkish equity/single-stock-futures basis strategy | Not a fund | Existing handwritten strategy description is appropriate; retain as a strategy vault. |
| `carryTradeUSDTRYLeverage` | 1 | Leveraged USD/TRY carry/futures basis strategy | Not a fund | Existing handwritten strategy description is appropriate; retain as a strategy vault. |
| `mWIN` | 1 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `qHVNUSD` | 1 | Managed/third-party product | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `sGold` | 1 | Third-party/white-label product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `turtlePST` | 1 | Managed/third-party product | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mTEST` | 8453 | Test product | Not a fund | Test entry; never publish fund copy or flag. |
| `mGLO` | 4663, 8453 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `hbUSDT` | 999, 11155111 | HyperEVM/bridge product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `hbXAUt` | 999, 11155111 | HyperEVM/bridge product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `lstHYPE` | 999 | HyperEVM liquidity/staking product | Not a fund | Product-style token; no basis for a fund classification. |
| `liquidHYPE` | 999, 534352 | HyperEVM liquidity product | Not a fund | Product-style token; no basis for a fund classification. |
| `hbUSDC` | 999 | HyperEVM/bridge product | Needs product review | No verified product copy in the Midas registry; do not infer from symbol. |
| `wVLP` | 999 | HyperEVM vault/liquidity wrapper | Not a fund | Wrapper/vault-style product; not a tokenised fund. |
| `dnHYPE` | 999 | HyperEVM directional product | Not a fund | Strategy/wrapper-style product; not a tokenised fund. |
| `dnPUMP` | 999 | HyperEVM directional product | Not a fund | Strategy/wrapper-style product; not a tokenised fund. |
| `dnFART` | 999 | HyperEVM directional product | Not a fund | Strategy/wrapper-style product; not a tokenised fund. |
| `kitUSD` | 999 | HyperEVM packaged product | Needs product review | Obtain issuer and portfolio documentation before adding copy or flag. |
| `kitHYPE` | 999 | HyperEVM packaged product | Needs product review | Obtain issuer and portfolio documentation before adding copy or flag. |
| `kitBTC` | 999 | HyperEVM packaged product | Needs product review | Obtain issuer and portfolio documentation before adding copy or flag. |
| `wNLP` | 999 | HyperEVM vault/liquidity wrapper | Not a fund | Wrapper/vault-style product; not a tokenised fund. |
| `dnETH` | 999 | HyperEVM directional product | Not a fund | Strategy/wrapper-style product; not a tokenised fund. |
| `dnTEST` | 56, 999, 1440000 | Test product | Not a fund | Test entry; never publish fund copy or flag. |
| `mRE7SOL` | 747474 | Managed mToken; mandate not reviewed here | Needs product review | Obtain current terms and portfolio mandate before adding copy or flag. |
| `kmiUSD` | 747474 | Managed/third-party product | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mXRP` | 56, 1440000 | Managed mToken; mandate not reviewed here | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `tacTON` | 239 | Bridge/wrapped representation | Not a fund | Bridge representation; no standalone product copy. |
| `plUSD` | 9745 | Managed/third-party product | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `splUSD` | 9745 | Managed/third-party product | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `cUSDO` | 56 | Managed/third-party product | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `liquidRESERVE` | 10, 534352 | Liquidity/reserve product | Not a fund | Product-style liquidity token; no basis for a fund classification. |
| `weEUR` | 10, 534352 | Wrapped EUR product | Not a fund | Wrapper/settlement token, not a tokenised fund. |
| `sLINJ` | 1776 | Managed/third-party product | Needs product review | Obtain issuer/manager documentation before adding copy or flag. |
| `mRe7ETH` | 10 | Managed mToken; mandate not reviewed here | Needs product review | Obtain current terms and portfolio mandate before adding copy or flag. |
| `liquidRWA` | 10 | RWA/liquidity product; mandate not reviewed here | Needs product review | Obtain issuer and investor-rights documentation before adding copy or flag. |

## Implementation implications

1. `MIDAS_PRODUCT_METADATA` is the single source for reviewed decisions and
   display copy.  Product scanners must read it by symbol so one product is
   classified consistently across all chains.
2. The migration only touches pre-existing rows for approved products.  It
   merges the `tokenised_fund` flag and replaces only the product description,
   short description and Midas product link; it does not rescan, create rows or
   change price data.
3. Future products should enter this table as `Needs product review` first.
   Add a metadata entry only with a source-backed product description and an
   explicit decision.  Do not use token symbols, a NAV feed or a Midas contract
   alone as evidence of fund status.
