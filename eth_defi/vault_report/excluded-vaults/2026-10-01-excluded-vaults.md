# Excluded vaults, 2026-10-01

Vaults the investability check left out of *The best-performing stablecoin vaults, October 2026*, with data dated 2026-10-01. Written 2026-10-01 09:47 UTC by `eth_defi.vault_report.vault_checks`, see `eth_defi/vault_report/README-vault-report.md`.

They would have ranked or been charted, but are not investable in practice: their collateral cannot be valued or sold, their depositors cannot exit, or they show signs of a scam. The check is AI-assisted and not deterministic; review each decision.

## Excluded vaults

| Vault | Chain | Protocol | Suspicious item | Reason | Confidence | Blacklisted in `flag.py` |
|---|---|---|---|---|---|---|
| [HypurrFi Earn USDC](https://tradingstrategy.ai/vaults/hypurrfi-earn-usdc) | Hyperliquid | Euler | 100% utilised, no free liquidity | Redeemable liquidity is 0.001% with idle share 0 and 100% utilisation for 30 days across all strategies. | medium | no |
| [Pharaoh USDC](https://tradingstrategy.ai/vaults/pharaoh-usdc) | Avalanche | 40acres | 100% utilised, no free liquidity | 40acres lender liquidity stays near zero (1.8% redeemable now, free liquidity below 1% for at least 29 days, utilisation median 100%), and lenders must wait for borrowers to repay veNFT-backed loans, so a new depositor cannot expect to exit. | medium | no |
| [Aerodrome USDC](https://tradingstrategy.ai/vaults/aerodrome-usdc) | Base | 40acres | 100% utilised, no free liquidity | 40acres lender liquidity stays near zero (0% redeemable, free liquidity below 1% for at least 29 days, utilisation median 100%), and lenders must wait for borrowers to repay veNFT-backed loans, so a new depositor cannot expect to exit. | high | no |
| [Velodrome USDC](https://tradingstrategy.ai/vaults/velodrome-usdc) | Optimism | 40acres | 100% utilised, no free liquidity | 40acres lender liquidity stays near zero (0% redeemable, free liquidity below 1% for at least 29 days, utilisation median 100%), and lenders must wait for borrowers to repay veNFT-backed loans, so a new depositor cannot expect to exit. | high | no |
| [Liquity Hub](https://tradingstrategy.ai/vaults/liquity-hub-3) | Ethereum | Euler | 99.99% utilised, no free liquidity | Redeemable liquidity is 0.01% of assets with median utilisation 99.997% over 29 days, and the pool lends against expired or illiquid PT-sBOLD collateral. | medium | no |
| [NetNet Credit](https://tradingstrategy.ai/vaults/netnet-credit) | Robinhood | Morpho | fully borrowed unlisted markets | Morpho API shows $55 of liquidity on $1.49M; all markets (NVDA, SPCX, wsNET, AAPL, GOOGL collateral) are unlisted and 99.99% utilised, so a new depositor cannot exit. | medium | no |
| [JPEG Trading x Tenbin RWAs](https://tradingstrategy.ai/vaults/jpeg-trading-x-tenbin-rwas-2) | Ethereum | Euler | 100% utilised, no free liquidity | Redeemable liquidity is 0.00% with idle share 0 and 100% utilisation for 29 days, in a pool lending against tGLD with $2.7k DEX liquidity. | medium | no |
| [Steakhouse PaoTech JPYC](https://tradingstrategy.ai/vaults/steakhouse-paotech-jpyc-3) | Polygon | Morpho | TVL overstated about 160 times | Morpho reports $10k of assets in USD while our data shows $1.6M because JPYC (yen stablecoin) was valued 1:1 with USD, so the TVL and ranking are wrong. | medium | no |

## Vaults under review

The check could not decide on these vaults, so they stay in the report. They are recorded in `flag.py` with `VaultFlag.review_needed`.

| Vault | Chain | Protocol | Reason |
|---|---|---|---|
| [AlphaGrowth Base RWA](https://tradingstrategy.ai/vaults/alphagrowth-base-rwa) | Base | Euler | Wrapped tokenised stock collateral has no DEX liquidity, but 30% is redeemable and no scam evidence was found. |
| [Alpha USDC Forex V2](https://tradingstrategy.ai/vaults/alpha-usdc-forex-v2) | Ethereum | Morpho | Only $105 of vault liquidity on $959k and issuer-linked collateral without DEX pairs, but the markets are listed and exits via forced deallocation are possible. |
| [InfiniFi Markets](https://tradingstrategy.ai/vaults/infinifi-markets) | Ethereum | Euler | 90% of assets lends against infiniFi's locked liUSD-4w with no DEX market and 9% is redeemable; no exploit evidence. |
| [JPEG Trading x Tenbin RWAs](https://tradingstrategy.ai/vaults/jpeg-trading-x-tenbin-rwas-3) | Ethereum | Euler | 97% in a pool lending against tGLD with $2.7k DEX liquidity, 2.6% redeemable, idle share 0 for 29 days; evidence conflicting. |
| [Morini USDC Emerging Yield](https://tradingstrategy.ai/vaults/morini-usdc-emerging-yield) | Ethereum | Morpho | Unlisted vault lending against issuer carry-trade tokens without DEX pairs, but 42% of assets are withdrawable. |
| [9Summits Piku Ecosystem USDC](https://tradingstrategy.ai/vaults/9summits-piku-ecosystem-usdc-2) | Ethereum | Morpho | Listed vault with 83% withdrawable, but earlier runs flagged Morini collateral and adapter positions were not re-read. |
| [RockawayX PT Yield](https://tradingstrategy.ai/vaults/rockawayx-pt-yield) | Binance | Euler | Probe failed and total assets read as 0 against a $1.78M candidate TVL; exit liquidity and TVL cannot be confirmed. |
| [Clearstar Yield](https://tradingstrategy.ai/vaults/clearstar-yield-6) | Hyperliquid | Euler | Redeemable liquidity is 0.001% now but idle share reached 29% within 14 days, so the shortage is not proven persistent. |
| [Hyperithm USDC Degen](https://tradingstrategy.ai/vaults/hyperithm-usdc-degen) | Ethereum | Morpho | All assets lend against NAV-priced Midas tokens issued for the curator with $0 DEX liquidity, so an editor must decide whether that collateral is acceptable although 21.6% is redeemable and Morpho lists the vault. |
| [YieldNest Max Vaults](https://tradingstrategy.ai/vaults/yieldnest-max-vaults) | Ethereum | Euler | The pool has been about 100% utilised for 29 days with 0.00% redeemable liquidity but served withdrawals could not be confirmed, so an editor must decide. |

## Check run

- Rounds: 3; decisions: 8 exclude, 45 keep, 201 not_in_scope, 10 uncertain
- Decision files: `vault-check-decisions-1.json`, `vault-check-decisions-2.json`, `vault-check-decisions-3.json`
