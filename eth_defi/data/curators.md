# DeFi vault curators

A curator is a professional asset manager or risk management firm that operates vaults on permissionless DeFi lending and vault infrastructure protocols (Morpho, Euler, IPOR Fusion, Lagoon Finance, etc.).

Curators are responsible for setting risk parameters, allocating assets, and managing vault strategies on behalf of passive depositors. They do not have custody of user funds but control key parameters like collateral selection and allocation weights.

Sources: On-disk vault metadata database (~11,000 stablecoin vaults across Morpho, Euler, IPOR Fusion, Lagoon Finance, and other protocols), Lagoon Finance API cache, Hyperliquid/GRVT/Lighter native DuckDB databases (vault descriptions parsed for manager identity), web search verification.

## Cross-platform curators

Active across multiple vault protocols (Morpho, Euler, Lagoon, IPOR Fusion, etc.).

| Curator | Website | Twitter |
|---------|---------|---------|
| Gauntlet | https://www.gauntlet.xyz | [@gauntlet_xyz](https://x.com/gauntlet_xyz) |
| Steakhouse Financial | https://steakhouse.financial | [@SteakhouseFi](https://x.com/SteakhouseFi) |
| RE7 Labs | https://re7.capital | [@Re7Capital](https://x.com/Re7Capital) |
| MEV Capital | https://www.mevcapital.com | [@MEVCapital](https://x.com/MEVCapital) |
| Block Analitica | https://blockanalitica.com | [@BlockAnalitica](https://x.com/BlockAnalitica) |
| Sentora (formerly IntoTheBlock) | https://sentora.com | [@SentoraHQ](https://x.com/SentoraHQ) |
| Hyperithm | https://www.hyperithm.com | [@hyperithm](https://x.com/hyperithm) |
| K3 Capital | https://k3.capital | [@k3_capital](https://x.com/k3_capital) |
| Avantgarde Finance | https://avantgarde.finance | [@avantgardefi](https://x.com/avantgardefi) |
| Apostro | https://apostro.xyz | [@apostroxyz](https://x.com/apostroxyz) |
| RockawayX | https://www.rockawayx.com | [@Rockaway_X](https://x.com/Rockaway_X) |
| Clearstar Labs | https://www.clearstar.xyz | [@ClearstarLabs](https://x.com/ClearstarLabs) |
| LlamaRisk | https://llamarisk.com | [@LlamaRisk](https://x.com/LlamaRisk) |
| kpk | https://kpk.io | [@kpk_io](https://x.com/kpk_io) |
| Alterscope | https://www.alterscope.org | [@Alterscope](https://x.com/Alterscope) |
| Varlamore Capital | https://varlamore.capital | [@VarlamoreCap](https://x.com/VarlamoreCap) |
| August Digital | https://www.augustdigital.io | [@august_digital](https://x.com/august_digital) |
| Tulipa Capital | https://tulipa.capital | [@tulipacapital](https://x.com/tulipacapital) |
| Turtle | https://www.turtle.xyz | [@turtledotxyz](https://x.com/turtledotxyz) |

## IPOR Fusion curators

Curators operating vaults on the [IPOR Fusion](https://app.ipor.io/) platform.

| Curator | Website | Twitter |
|---------|---------|---------|
| Tesseract | https://tesseract.fi | [@tesseractcrypto](https://x.com/tesseractcrypto) |

## Lagoon Finance curators

Curators operating vaults on the [Lagoon Finance](https://app.lagoon.finance/) platform.
Sourced from Lagoon API (`app.lagoon.finance/api/vault`) chain 1 (Ethereum).

| Curator | Website | Twitter |
|---------|---------|---------|
| 9Summits | https://vaults.9summits.io | [@nine_summits](https://x.com/nine_summits) |
| DAMM Capital | https://dammcap.finance | [@DAMM_Capital](https://x.com/DAMM_Capital) |
| Gami | https://gamilabs.io | [@GamiLabs](https://x.com/GamiLabs) |
| Hub Capital | https://hub.capital | |
| Odyssey Digital AM | | |
| SmarDex | https://smardex.io | [@SmarDex](https://x.com/SmarDex) |
| Syntropia | https://syntropia.ai | [@syntropia_ai](https://x.com/syntropia_ai) |

## Hyperliquid vault managers

Active traders and asset managers running strategy vaults on [Hyperliquid](https://app.hyperliquid.xyz/vaults).
Identified from vault descriptions in the Hyperliquid native DuckDB database (`hyperliquid-vaults.duckdb`).
Sorted by vault TVL. Excludes protocol-owned vaults (HLP, HLP Strategy A/B/X, etc.).

| Manager | Website | Twitter | Notes |
|---------|---------|---------|-------|
| Growi Finance | https://growi.fi | [@GrowiFinance](https://x.com/GrowiFinance) | Quantitative mean-reversion; runs Growi HF (~$9M TVL) and Growi HF-2. Also operates HyperTwin (2x leveraged copy vault) |
| Systemic Strategies | | [@SystemicStratHL](https://x.com/SystemicStratHL) | Grid longing/shorting strategies; HyperGrowth (~$11M TVL) and L/S Grids (~$3M TVL). Also on Lighter |
| Blackalgo | https://blackalgo.com | [@blackalgo](https://x.com/blackalgo) | Institutional-grade momentum strategy; Dubai-based algo trading firm, VARA-regulated |
| Edge & Hedge | | [@blothecap](https://x.com/blothecap) | Quantitative delta neutral L/S, ~3x leverage. Also on Lighter |
| Silentist | https://www.silentist.xyz | [@silentist_inc](https://x.com/silentist_inc) | Korean VC-backed quant firm; 1,000+ days live trading. Also on GRVT and Lighter |

## GRVT vault managers

Active traders and asset managers running strategy vaults on [GRVT](https://grvt.io/exchange/strategies).
Identified from `manager_name` field and vault descriptions in the GRVT native DuckDB database (`grvt-vaults.duckdb`).
Sorted by vault TVL.

| Manager | Website | Twitter | Notes |
|---------|---------|---------|-------|
| KangCFA | | | Leading Korean crypto/quant YouTuber; CFA & CAIA charterholder; delta neutral arbitrage (~$1.8M TVL) |
| Bitcoin Strategy (Gerhard) | | | YouTube creator with 150k+ subscribers; data-driven BTC/ETH analysis; 8 years outperforming BTC (~$519k TVL) |
| SK System (Kim Seon-kyung) | | | CEO of YH Holdings; 14-year BTC futures veteran; "Jibaldonjom" YouTube (40k+), Instagram (100k+) (~$455k TVL) |
| Silentist | https://www.silentist.xyz | [@silentist_inc](https://x.com/silentist_inc) | Korean VC-backed quant firm; 46% APR in 2022, 64% in 2023, 71% in 2024 (~$119k TVL). Also on Hyperliquid and Lighter |
| Fisher8 Capital | https://fisher8.capital | [@fisher8cap](https://x.com/fisher8cap) | Family office; 7+ years in crypto; Top 3 Bybit leaderboard in 2024 (~$97k TVL) |
| B-CUBE.AI | https://b-cube.ai | | VASP-regulated AI quant platform; ML-driven signal execution (~$25k TVL) |
| Ignight Capital | https://ignight.capital | [@IgnightCapital](https://x.com/IgnightCapital) | Multi-strategy crypto fund; backed by Korean VCs; invested in Grass, Fragmetric, GRVT, Gnosis Safe |
| Rogue Traders | | | BTC options fund & academy; 25-year market veterans across derivatives, FX, software |
| Pareto Technologies | | | Systematic asset management firm; active in digital asset markets since 2018 |

## Lighter pool managers

Active traders and asset managers running strategy pools on [Lighter](https://app.lighter.xyz/public-pools).
Identified from pool descriptions in the Lighter native DuckDB database (`lighter-pools.duckdb`).
Sorted by pool TVL.

| Manager | Website | Twitter | Notes |
|---------|---------|---------|-------|
| Edge & Hedge | | [@blothecap](https://x.com/blothecap) | Quantitative delta neutral L/S, ~3x leverage, ~110% CAGR backtest (~$2.4M TVL). Also on Hyperliquid |
| Systemic Strategies | | [@SystemicStratHL](https://x.com/SystemicStratHL) | Grid long/short strategies ("Peter Schiff's vault" and others) (~$768k TVL). Also on Hyperliquid |
| Alleged Alpha | | [@allegedalpha](https://x.com/allegedalpha) | Systematic multi-strategy L/S; targets Sharpe ~3, APR >100% (~$514k TVL) |
| Silentist | https://www.silentist.xyz | [@silentist_inc](https://x.com/silentist_inc) | VC-backed quant team; BTC/bluechip ALTs, 37% trend, 37% reversion, 25% neutral (~$53k TVL). Also on GRVT and Hyperliquid |
| Gamma Strategies | https://www.gamma.xyz | [@GammaStrategies](https://x.com/GammaStrategies) | Manages "Symphony" pool (multi-strategy market-neutral); known for concentrated liquidity management on Uniswap and other DEXes (~$51k TVL) |
| Insertive Capital | | [@onchainquant](https://x.com/onchainquant) | High-risk discretionary fund; asymmetric opportunities across volatile markets (~$297k TVL) |
