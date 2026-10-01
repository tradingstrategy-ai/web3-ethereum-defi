# Excluded vaults, 2026-09-30

Vaults the investability check left out of *The best-performing stablecoin vaults, September 2026*, with data dated 2026-09-30. Written 2026-09-30 19:47 UTC by `eth_defi.vault_report.vault_checks`, see `eth_defi/vault_report/README-vault-report.md`.

They would have ranked or been charted, but are not investable in practice: their collateral cannot be valued or sold, their depositors cannot exit, or they show signs of a scam. The check is AI-assisted and not deterministic; review each decision.

## Excluded vaults

| Vault | Chain | Protocol | Suspicious item | Reason | Confidence | Blacklisted in `flag.py` |
|---|---|---|---|---|---|---|
| [Pharaoh USDC](https://tradingstrategy.ai/vaults/pharaoh-usdc) | Avalanche | 40acres | 100% utilised, no free liquidity | Pharaoh USDC is a 40acres pool that has had $0 free liquidity and 100% utilisation for 30 days, so lenders must wait for borrowers to repay veNFT-backed loans before they can withdraw. | medium | no |
| [Aerodrome USDC](https://tradingstrategy.ai/vaults/aerodrome-usdc) | Base | 40acres | 100% utilised, no free liquidity | Aerodrome USDC is a 40acres pool that has had $0 free liquidity and 100% utilisation for 30 days, so lenders must wait for borrowers to repay veNFT-backed loans before they can withdraw. | high | no |
| [Velodrome USDC](https://tradingstrategy.ai/vaults/velodrome-usdc) | Optimism | 40acres | 100% utilised, no free liquidity | Velodrome USDC is a 40acres pool that has had $0 free liquidity and 100% utilisation for 30 days, so lenders must wait for borrowers to repay veNFT-backed loans before they can withdraw. | medium | no |

## Vaults under review

The check could not decide on these vaults, so they stay in the report. They are recorded in `flag.py` with `VaultFlag.review_needed`.

| Vault | Chain | Protocol | Reason |
|---|---|---|---|
| [Liquity Hub](https://tradingstrategy.ai/vaults/liquity-hub-3) | Ethereum | Euler | Only 0.014% of assets is redeemable and the pool has been about 100% utilised for 30 days, but no served withdrawals could be verified. |
| [Alpha USDC Forex V2](https://tradingstrategy.ai/vaults/alpha-usdc-forex-v2) | Ethereum | Morpho | Only $81 of $1.06M is withdrawable and 100% is lent against Morini carry-trade and basis-trade vault tokens with no DEX pairs, but they belong to a real documented product and are not clearly a scam. |
| [JPEG Trading x Tenbin RWAs](https://tradingstrategy.ai/vaults/jpeg-trading-x-tenbin-rwas-2) | Ethereum | Euler | The pool lends only against Tenbin tGLD with about $2.7k of DEX liquidity and has had 0% redeemable liquidity at 100% utilisation for 30 days, but the exit mechanism was not confirmed. |
| [JPEG Trading x Tenbin RWAs](https://tradingstrategy.ai/vaults/jpeg-trading-x-tenbin-rwas-3) | Ethereum | Euler | 97% of assets is in the tGLD Euler pool with 2.6% redeemable and $2.7k of DEX liquidity for the collateral; whether the pool is permanently stuck is unresolved. |
| [Steakhouse PaoTech JPYC](https://tradingstrategy.ai/vaults/steakhouse-paotech-jpyc-3) | Polygon | Morpho | The Morpho API reports total assets of about $10k against $1.6M in our export, so the TVL appears overstated, though the vault is liquid. |
| [RockawayX PT Yield](https://tradingstrategy.ai/vaults/rockawayx-pt-yield) | Binance | Euler | The Euler Earn probe failed so redeemable liquidity is unknown and the protocol classification is in doubt. |
| [Clearstar Yield](https://tradingstrategy.ai/vaults/clearstar-yield-6) | Hyperliquid | Euler | Redeemable liquidity is 0.003% of assets while idle cash reached 29% within 14 days; whether the illiquidity is temporary is unresolved. |
| [HypurrFi Earn USDC](https://tradingstrategy.ai/vaults/hypurrfi-earn-usdc) | Hyperliquid | Euler | Redeemable liquidity is 0.003% of assets at about 100% utilisation and the vault depends on the Clearstar Yield pool, whose exit liquidity is undecided. |
| [Hyperithm USDC Degen](https://tradingstrategy.ai/vaults/hyperithm-usdc-degen) | Ethereum | Morpho | Lends all assets against curator-issued Midas NAV tokens with no DEX market; acceptability is unresolved. |
| [YieldNest Max Vaults](https://tradingstrategy.ai/vaults/yieldnest-max-vaults) | Ethereum | Euler | The pool has been fully borrowed for 30 days with 0.00% redeemable, and no served withdrawals could be confirmed. |

## Check run

- Rounds: 3; decisions: 3 exclude, 47 keep, 203 not_in_scope, 10 uncertain
- In-scope vaults not checked before the round limit: 0
- Decision files: `vault-check-decisions-1.json`, `vault-check-decisions-2.json`, `vault-check-decisions-3.json`
