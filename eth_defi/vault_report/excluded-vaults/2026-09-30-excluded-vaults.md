# Excluded vaults, 2026-09-30

Vaults the investability check left out of *The best-performing stablecoin vaults, September 2026*, with data dated 2026-09-30. Written 2026-09-30 19:01 UTC by `eth_defi.vault_report.vault_checks`, see `eth_defi/vault_report/README-vault-report.md`.

They would have ranked or been charted, but are not investable in practice: their collateral cannot be valued or sold, their depositors cannot exit, or they show signs of a scam. The check is AI-assisted and not deterministic; review each decision.

## Excluded vaults

| Vault | Chain | Protocol | Suspicious item | Reason | Confidence | Blacklisted in `flag.py` |
|---|---|---|---|---|---|---|
| [Trevee plUSD](https://tradingstrategy.ai/vaults/trevee-plusd) | Plasma | Euler | Stream xUSD collateral, no redeemable liquidity | Lends 100% of its assets into an Euler pool accepting collapsed Stream xUSD as collateral, with $0 redeemable, so the yield cannot be realised and depositors cannot exit. | high | yes, illiquid |
| [HypurrFi Earn USDC](https://tradingstrategy.ai/vaults/hypurrfi-earn-usdc) | Hyperliquid | Euler | Redeemable liquidity 0.003% | Redeemable liquidity is 0.003% of assets with about 100% utilisation for 30 days and no idle cash, so a new depositor cannot expect to exit. | medium | no |
| [YieldNest Max Vaults](https://tradingstrategy.ai/vaults/yieldnest-max-vaults) | Ethereum | Euler | 0% redeemable, 100% utilised for 30 days | Redeemable liquidity has been effectively zero at 100% utilisation for the whole 30-day history, and the collateral ynRWAx is a fixed-maturity real-estate credit token, so a new depositor cannot expect to exit soon. | medium | no |
| [Pharaoh USDC](https://tradingstrategy.ai/vaults/pharaoh-usdc) | Avalanche | 40acres | 100% utilised, no free liquidity | Single-pool 40acres vault has been 100% utilised with no free liquidity for 14 days and lenders must wait for borrowers to repay veNFT-backed loans, so a new depositor cannot exit. | high | no |
| [Aerodrome USDC](https://tradingstrategy.ai/vaults/aerodrome-usdc) | Base | 40acres | 100% utilised, no free liquidity | Single-pool 40acres vault has been 100% utilised with no free liquidity for 14 days and lenders must wait for borrowers to repay veNFT-backed loans, so a new depositor cannot exit. | high | no |
| [Velodrome USDC](https://tradingstrategy.ai/vaults/velodrome-usdc) | Optimism | 40acres | 100% utilised, no free liquidity | Single-pool 40acres vault has been 100% utilised with no free liquidity for 14 days and lenders must wait for borrowers to repay veNFT-backed loans, so a new depositor cannot exit. | high | no |
| [Liquity Hub](https://tradingstrategy.ai/vaults/liquity-hub-3) | Ethereum | Euler | Redeemable liquidity 0.01%, pool ~100% utilised | Redeemable liquidity is 0.01% of assets, utilisation has been about 100% for 30 days and the pool accepts expired PT-sBOLD collateral with no DEX liquidity. | medium | no |
| [Alpha USDC Forex V2](https://tradingstrategy.ai/vaults/alpha-usdc-forex-v2) | Ethereum | Morpho | Only $81 withdrawable of $1.06M | Unlisted vault with only $81 withdrawable out of $1.06M, lending against leveraged carry-trade strategy tokens with no DEX pairs, so a new depositor cannot expect to exit. | medium | no |
| [JPEG Trading x Tenbin RWAs](https://tradingstrategy.ai/vaults/jpeg-trading-x-tenbin-rwas-2) | Ethereum | Euler | 0% redeemable, 100% in tGLD pool | Redeemable liquidity is 0.00% of assets, the pool has been about 100% utilised for 30 days and its tGLD collateral has only $2,699 DEX liquidity. | medium | no |

## Vaults under review

The check could not decide on these vaults, so they stay in the report. They are recorded in `flag.py` with `VaultFlag.review_needed`.

| Vault | Chain | Protocol | Reason |
|---|---|---|---|
| [AlphaGrowth Base RWA](https://tradingstrategy.ai/vaults/alphagrowth-base-rwa) | Base | Euler | Pool accepts reUSD and wrapped tokenised stocks with no DEX liquidity; 25% is redeemable but collateral pricing could not be verified. |
| [NetNet Credit](https://tradingstrategy.ai/vaults/netnet-credit) | Robinhood | Morpho | Unlisted vault lending against tokenised stocks with unknown oracles in markets 100% borrowed and the curator-related wsNET token; only 7% is withdrawable. |
| [JPEG Trading x Tenbin RWAs](https://tradingstrategy.ai/vaults/jpeg-trading-x-tenbin-rwas-3) | Ethereum | Euler | 97% of assets sit in a pool with tGLD collateral that has only $2,699 DEX liquidity, but 2.6% is redeemable and utilisation is about 100%. |
| [Edge UltraYield USDC](https://tradingstrategy.ai/vaults/edge-ultrayield-usdc-3) | Base | Morpho | 83% of assets lent against uniBTC with only $334 DEX liquidity on Base; 25% redeemable and the vault is listed on Morpho. |
| [RockawayX PT Yield](https://tradingstrategy.ai/vaults/rockawayx-pt-yield) | Binance | Euler | The Euler Earn probe failed so redeemable liquidity is unknown. |
| [Clearstar Yield](https://tradingstrategy.ai/vaults/clearstar-yield-6) | Hyperliquid | Euler | Redeemable liquidity is 0.003% now, but idle share reached 29% within the last 14 days and utilisation median is 87%. |
| [Hyperithm USDC Degen](https://tradingstrategy.ai/vaults/hyperithm-usdc-degen) | Ethereum | Morpho | All assets are lent against Midas tokens issued for the curator Hyperithm with $0 DEX liquidity and 87-90% utilisation, though they are Midas primary-market NAV tokens, 21% is redeemable and Morpho lists the vault, so the editor should decide. |
| [K3 Isolated syzUSD-USDT0](https://tradingstrategy.ai/vaults/k3-isolated-syzusd-usdt0) | Monad | Euler | Evidence missing: 100% lent against syzUSD with $0 DEX liquidity on Monad; price source and redemption path not verified. |
| [Clearstar Reactor](https://tradingstrategy.ai/vaults/clearstar-reactor) | Monad | Euler | Evidence missing: 100% lent against FXRP with $0 Monad DEX liquidity; price source not verified. |
| [Clearstar Earn USDC](https://tradingstrategy.ai/vaults/clearstar-earn-usdc) | Monad | Euler | Evidence missing: 100% routed to the FXRP pool with $0 Monad DEX liquidity; price source not verified. |
| [Clearstar OpenEden Hybond](https://tradingstrategy.ai/vaults/clearstar-openeden-hybond) | Ethereum | Euler | Evidence missing: 100% lent against OpenEden HYBOND with $0 DEX liquidity; primary-market NAV assumed but not verified, 6.8% redeemable. |

## Check run

- Rounds: 3; decisions: 9 exclude, 43 keep, 215 not_in_scope, 11 uncertain
- In-scope vaults not checked before the round limit: 0
- Decision files: `vault-check-decisions-1.json`, `vault-check-decisions-2.json`, `vault-check-decisions-3.json`
