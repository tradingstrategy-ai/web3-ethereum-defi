"""Address-scoped strategies reviewed from current official Buoy descriptions.

Reviewed 2026-11-14 for all 60 registered vaults. Entries without sufficiently
specific published strategy information are deliberately unmapped (None),
including two registrations absent from the public API. No protocol-wide
perpetual-futures default: several vaults explicitly buy spot assets only.
"""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Delta Neutral; reviewed 2026-11-14.
    #: Decision material: A Delta Neutral vault which aims to earn steady returns by staying neutral to price swings.
    #: Source: https://buoy.finance/vaults/0x87F76a7e6C346904192c884d48C02D3AC640DE1E
    "0x87f76a7e6c346904192c884d48c02d3ac640de1e": {StrategyTag.delta_neutral},
    #: The Cat's Meow; reviewed 2026-11-14.
    #: Decision material: The cat's meow is a long-only HYPE vault built on one thesis: HYPE goes higher. No shorts, No hedging, just conviction.
    #: Source: https://buoy.finance/vaults/0xD2D28B042a99eD1e8Be3e3F5197Cc3327Dd03248
    "0xd2d28b042a99ed1e8be3e3f5197cc3327dd03248": {StrategyTag.directional_trading},
    #: HyperNeutral; reviewed 2026-11-14.
    #: Decision material: Market neutral strategy. Maximise returns, Minimize pain.
    #: Source: https://buoy.finance/vaults/0x3C8535595a962B8578647BDF7Ce4C7320af91b14
    "0x3c8535595a962b8578647bdf7ce4c7320af91b14": {StrategyTag.delta_neutral},
    #: Degen quant AI; reviewed 2026-11-14.
    #: Decision material: Systematic vault run by an automated quant engine. Event-driven setups on Hyperliquid perps, each in a small, fixed size with its own stop and loss limit, hedged where the setup allows. No discretionary trades: every rul
    #: Source: https://buoy.finance/vaults/0x1af42a9937C2E0D0446e3Ea9659725Eff909A14E
    "0x1af42a9937c2e0d0446e3ea9659725eff909a14e": {StrategyTag.algorithmic_trading, StrategyTag.perpetual_futures},
    #: DCA HYPE; reviewed 2026-11-14.
    #: Decision material: Buys HYPE on spot every day and never sells. Spends 1% of free USDC per day, scaled 0.5x to 3x by distance from the 200-day average: more when HYPE is cheap vs trend, less when stretched. No leverage. Volatile asset.
    #: Source: https://buoy.finance/vaults/0x6e8075CF5f840067a3d82453677Db2C0D88dB51e
    "0x6e8075cf5f840067a3d82453677db2c0d88db51e": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading},
    #: DCA ETH; reviewed 2026-11-14.
    #: Decision material: Buys ETH on spot every day and never sells. Spends 1% of free USDC per day, scaled 0.5x to 3x by distance from the 200-day average: more when ETH is cheap vs trend, less when stretched. No leverage, no funding.
    #: Source: https://buoy.finance/vaults/0xBB36f71abfe80eD10c44e37C69ec8D3359DDD920
    "0xbb36f71abfe80ed10c44e37c69ec8d3359ddd920": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading},
    #: DCA BTC; reviewed 2026-11-14.
    #: Decision material: Buys BTC on spot every day and never sells. Spends 1% of free USDC per day, scaled 0.5x to 3x by distance from the 200-day average: more when BTC is cheap vs trend, less when stretched. No leverage, no funding.
    #: Source: https://buoy.finance/vaults/0x754eaF74Dee8DAF61E75FE778007202f8Fd8F9F6
    "0x754eaf74dee8daf61e75fe778007202f8fd8f9f6": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading},
    #: LIT $1 Club; reviewed 2026-11-14.
    #: Decision material: A $0.05 grid on LIT perps. Buys every 5c down, sells every 5c up, forever. No signals, no discretion. Same engine as my HYPE vault, Long the dip by design, so leverage stays near 1.2x and the ladder survives below LIT's
    #: Source: https://buoy.finance/vaults/0x3Bc0dc0F79421388aA10A9fDE393F73cE92B1dDE
    "0x3bc0dc0f79421388aa10a9fde393f73ce92b1dde": {StrategyTag.grid_trading, StrategyTag.algorithmic_trading, StrategyTag.perpetual_futures, StrategyTag.directional_trading},
    #: MashVault; reviewed 2026-11-14.
    #: Decision material: Mash's vault for the automated trading strategy developed throughout 2026. Deposit it at your own risk!
    #: Source: https://buoy.finance/vaults/0x11a000f5cbbB9E97f18a03da189E88AA4c849aD9
    "0x11a000f5cbbb9e97f18a03da189e88aa4c849ad9": {StrategyTag.algorithmic_trading},
    #: AI Industrial Complex; reviewed 2026-11-14.
    #: Decision material: Long AI. Short garbage.
    #: Source: https://buoy.finance/vaults/0x8d76B763Ce5D6815a8C6F26BF1A595E39eba62Dd
    "0x8d76b763ce5d6815a8c6f26bf1a595e39eba62dd": {StrategyTag.directional_trading},
    #: Stock Market Making; reviewed 2026-11-14.
    #: Decision material: Stock / FX Market Making vault - no crypto exposure. High frequency automatic trading capturing volatility with limited long/short exposure.
    #: Source: https://buoy.finance/vaults/0x5616C9f81163AbE21eE986248AB95F37840CB02a
    "0x5616c9f81163abe21ee986248ab95f37840cb02a": {StrategyTag.market_making, StrategyTag.market_making_clob, StrategyTag.algorithmic_trading, StrategyTag.fx},
    #: ML Yield Hunter; reviewed 2026-11-14.
    #: Decision material: Buoy Vault ($HYA) deploys a systematic strategy on Hyperliquid perpetuals, combining machine learning signal generation with adaptive risk management to generate regular yield. HYA stands for Active Hyperliquid Yield. En
    #: Source: https://buoy.finance/vaults/0x7c9bbcfd592dcf4b76F75219DDfd2C9F2188bAc5
    "0x7c9bbcfd592dcf4b76f75219ddfd2c9f2188bac5": {StrategyTag.algorithmic_trading, StrategyTag.perpetual_futures},
    #: Momentum; reviewed 2026-11-14.
    #: Decision material: Market-neutral momentum strategy: long strong crypto assets, short weak ones, with periodic rebalancing.
    #: Source: https://buoy.finance/vaults/0x9C635C7486Cdf3d27E9960502E308203d0726f2E
    "0x9c635c7486cdf3d27e9960502e308203d0726f2e": {StrategyTag.algorithmic_trading, StrategyTag.delta_neutral, StrategyTag.trend_following},
    #: Long HYPE & BTC / Short Others; reviewed 2026-11-14.
    #: Decision material: Recreating the classic Long HYPE Short high FDV vault
    #: Source: https://buoy.finance/vaults/0x5B3096EB52F2BbE9C3B4aFc1a91fE4A5A6E332b0
    "0x5b3096eb52f2bbe9c3b4afc1a91fe4a5a6e332b0": {StrategyTag.directional_trading},
    #: Mad Scientists; reviewed 2026-11-14.
    #: Decision material: Everything is an experiment — and Mad Scientists follows the one that keeps working. This vault mirrors Mad Scientists, one of Hyperliquid's boldest native vaults: a conviction-driven book built around a core long positi
    #: Source: https://buoy.finance/vaults/0x7E1E744B728Ea69BD1D418de0Ff684BF285bc20B
    "0x7e1e744b728ea69bd1d418de0ff684bf285bc20b": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.perpetual_futures},
    #: Inverse Cramer; reviewed 2026-11-14.
    #: Decision material: Jim says buy. We sell. Jim says sell. We buy. It’s not personal—it’s quantitative disrespect.
    #: Source: https://buoy.finance/vaults/0x3FD6ba2c19a2C3fb1856A169B81e9E377c29742f
    "0x3fd6ba2c19a2c3fb1856a169b81e9e377c29742f": {StrategyTag.social_trading},
    #: GAFAM; reviewed 2026-11-14.
    #: Decision material: Passive, rules-based 2x long on Big Tech, equal 20% allocation to each of the five GAFAM names, no discretionary calls, rebalanced periodically
    #: Source: https://buoy.finance/vaults/0xc231917313c95d707605e038f72Da984b6555D3F
    "0xc231917313c95d707605e038f72da984b6555d3f": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.index},
    #: GRANUM One; reviewed 2026-11-14.
    #: Decision material: Quantitative multi-asset trading across liquid markets, targeting structural capital flows with regime-adaptive risk, volatility sizing and correlation controls.
    #: Source: https://buoy.finance/vaults/0x620566D5B952aff3332D6Cd6b65b4821316Dd37C
    "0x620566d5b952aff3332d6cd6b65b4821316dd37c": {StrategyTag.algorithmic_trading},
    #: ESL4; reviewed 2026-11-14.
    #: Decision material: Long trend rider.
    #: Source: https://buoy.finance/vaults/0x030be10A98a4f7E9BADe01AB7a0AE8016Fa1d4cD
    "0x030be10a98a4f7e9bade01ab7a0ae8016fa1d4cd": {StrategyTag.trend_following, StrategyTag.directional_trading},
    #: Hypemaxxing; reviewed 2026-11-14.
    #: Decision material: Buy every HYPE dip
    #: Source: https://buoy.finance/vaults/0x26dd792Bec1B3d5FAA7E6AbA314513E3aD1b5A1f
    "0x26dd792bec1b3d5faa7e6aba314513e3ad1b5a1f": {StrategyTag.directional_trading},
    #: Reversal WIzard; reviewed 2026-11-14.
    #: Decision material: Reversal Wizard is an automated strategy that combines technical analysis with order flow data, focused on trading Bitcoin reversals. It uses predefined risk parameters for position sizing and stop placement. This is not
    #: Source: https://buoy.finance/vaults/0xd8533F609F1645244e070e52dcbAB6372C032A38
    "0xd8533f609f1645244e070e52dcbab6372c032a38": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading},
    #: Surf; reviewed 2026-11-14.
    #: Decision material: Like a surfer waiting for the right wave, Surf stays patient until an uptrend forms, then rides it. Long-only trend following, no shorts.
    #: Source: https://buoy.finance/vaults/0xE58ffF5a895e3e4397E2D49157Af1bBD4e279721
    "0xe58fff5a895e3e4397e2d49157af1bbd4e279721": {StrategyTag.trend_following, StrategyTag.directional_trading},
    #: Edgescanner4; reviewed 2026-11-14.
    #: Decision material: Trend rider Long-only.
    #: Source: https://buoy.finance/vaults/0xf5a7C62B12a0B2F825FC1B0cd1F278DF88C570Ec
    "0xf5a7c62b12a0b2f825fc1b0cd1f278df88c570ec": {StrategyTag.trend_following, StrategyTag.directional_trading},
    #: Vector Systematic ($VEC); reviewed 2026-11-14.
    #: Decision material: A cross-asset systematic momentum pod capturing directional dispersions with tight risk control.
    #: Source: https://buoy.finance/vaults/0x3061651d6A6a1c2B34d1B0f65cb21e19dbf53938
    "0x3061651d6a6a1c2b34d1b0f65cb21e19dbf53938": {StrategyTag.algorithmic_trading, StrategyTag.trend_following, StrategyTag.perpetual_futures},
    #: Chronos; reviewed 2026-11-14.
    #: Decision material: Trading vault on Hyperliquid specialized in perpetual futures.
    #: Source: https://buoy.finance/vaults/0xE0018e16ea1FA0cfE1Efc04b89579D0Ac8F8348D
    "0xe0018e16ea1fa0cfe1efc04b89579d0ac8f8348d": {StrategyTag.perpetual_futures},
    #: Long HYPE Short Aster; reviewed 2026-11-14.
    #: Decision material: This vault has a view and sticks to it: HYPE is the token to accumulate, ASTER is the token to fade. It never shorts HYPE and never longs ASTER.
    #: Source: https://buoy.finance/vaults/0x2f64a08360c3168a9B0bE00829b3CEa32d11C243
    "0x2f64a08360c3168a9b0be00829b3cea32d11c243": {StrategyTag.algorithmic_trading, StrategyTag.perpetual_futures, StrategyTag.directional_trading},
    #: Growi HF; reviewed 2026-11-14.
    #: Decision material: Employs a quantitative, mean-reversion strategy, trades all available cryptos. Fully automated with advanced risk control, targeting over 50% annual returns. Results from 5 months of live trading align with 7 years of ba
    #: Source: https://buoy.finance/vaults/0x9CCe8D3C90A74C96Ea5B1e0f16d4F1b9532e774D
    "0x9cce8d3c90a74c96ea5b1e0f16d4f1b9532e774d": {StrategyTag.algorithmic_trading, StrategyTag.statistical_arbitrage},
    #: PairVault; reviewed 2026-11-14.
    #: Decision material: Pair trading is a market-neutral strategy. Instead of betting that the whole market will go up or down, you bet on the relationship between two or more related assets
    #: Source: https://buoy.finance/vaults/0xcAa4c83f456b9Ec399Bbe2d0013AeDE8B9769883
    "0xcaa4c83f456b9ec399bbe2d0013aede8b9769883": {StrategyTag.pair_trading, StrategyTag.delta_neutral},
    #: Wake Rider; reviewed 2026-11-14.
    #: Decision material: Wake Rider rides in the wake of one of Hyperliquid's most consistent traders. The vault algorithmically mirrors the positions of Realist Capital (0x77fe...1a16) — a swing-trading vault with a 16-month track record, ~20%
    #: Source: https://buoy.finance/vaults/0x937f746B53C87dEB4C780123b8A056fa740b4e8f
    "0x937f746b53c87deb4c780123b8a056fa740b4e8f": {StrategyTag.algorithmic_trading},
    #: Top Ten Traders; reviewed 2026-11-14.
    #: Decision material: Copy-trades the ten best recent traders on Hyperliquid as a cohort, not a guru. Every 12 hours the vault re-selects from the public leaderboard, ranking by 30-day ROI — but only after screening out the accounts that make
    #: Source: https://buoy.finance/vaults/0x8e6D89c4e5C8d32532eFB10d133A1f5Edc9010B4
    "0x8e6d89c4e5c8d32532efb10d133a1f5edc9010b4": {StrategyTag.algorithmic_trading},
}
