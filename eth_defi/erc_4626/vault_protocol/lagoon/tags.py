"""Maintained strategy classifications for Lagoon Finance vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Reviewed Lagoon strategy tags, keyed by lowercase vault address.
LAGOON_STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: RockSolid rETH Vault. Added: 2026-08-21.
    #: Decision material: rETH lending and positive-funding-rate looping.
    #: Sources: https://app.lagoon.finance/vault/1/0x936facdf10c8c36294e7b9d28345255539d81bc7
    "0x936facdf10c8c36294e7b9d28345255539d81bc7": {StrategyTag.lending, StrategyTag.lending_looping},
    #: Vaults: Flagship cbBTC and 722Capital-USDC. Added: 2026-08-21.
    #: Decision material: this reused address combines both products' DEX LP,
    #: lending, arbitrage, delta-neutral, leverage-loop and incentive strategies.
    #: Sources: https://app.lagoon.finance/vault/1/0xb09f761cb13baca8ec087ac476647361b6314f98
    #:          https://app.lagoon.finance/vault/8453/0xb09f761cb13baca8ec087ac476647361b6314f98
    "0xb09f761cb13baca8ec087ac476647361b6314f98": {StrategyTag.amm, StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.lending, StrategyTag.lending_looping, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.yield_farming},
    #: Vault: Gami USDC. Added: 2026-08-21.
    #: Decision material: money markets, AMM pools and ecosystem incentives.
    #: Sources: https://app.lagoon.finance/vault/1/0xdae854d0896ad2fee335689a3f7b4a95fd1a3e46
    "0xdae854d0896ad2fee335689a3f7b4a95fd1a3e46": {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.yield_farming},
    #: Vault: Tulipa USDC. Added: 2026-08-21.
    #: Decision material: RWA lending, structured LP and incentivised liquidity.
    #: Sources: https://app.lagoon.finance/vault/1/0xce0b790ae0d8cf91e01f3fb69025e14569b574f3
    "0xce0b790ae0d8cf91e01f3fb69025e14569b574f3": {StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.rwa_lending, StrategyTag.yield_farming},
    #: Vault: Coinshift USPC Prime. Added: 2026-08-21.
    #: Decision material: money markets, Curve LP, governance incentives and RWA credit.
    #: Sources: https://app.lagoon.finance/vault/1/0xfab0f56c28e3f874b15922b213e696f37b670916
    "0xfab0f56c28e3f874b15922b213e696f37b670916": {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.money_market_fund, StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.yield_farming},
    #: Vault: 9Summits Flagship ETH. Added: 2026-08-21.
    #: Decision material: DEX LP, lending, redemption arbitrage and incentive farming.
    #: Sources: https://app.lagoon.finance/vault/1/0x07ed467acd4ffd13023046968b0859781cb90d9b
    "0x07ed467acd4ffd13023046968b0859781cb90d9b": {StrategyTag.amm, StrategyTag.arbitrage, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.yield_farming},
    #: Vault: 9Summits Flagship USDC. Added: 2026-08-21.
    #: Decision material: DEX LP, lending, fixed-income and redemption arbitrage.
    #: Sources: https://app.lagoon.finance/vault/1/0x03d1ec0d01b659b89a87eabb56e4af5cb6e14bfc
    "0x03d1ec0d01b659b89a87eabb56e4af5cb6e14bfc": {StrategyTag.amm, StrategyTag.arbitrage, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Syntropia USDC. Added: 2026-08-21.
    #: Decision material: DEX liquidity, lending, arbitrage and incentive farming.
    #: Sources: https://app.lagoon.finance/vault/1/0xd17049ed25d8f99fe3bfd10cef2263da9995cfd8
    "0xd17049ed25d8f99fe3bfd10cef2263da9995cfd8": {StrategyTag.amm, StrategyTag.arbitrage, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.yield_farming},
    #: Vault: DAMM Ethereum Fund. Added: 2026-08-21.
    #: Decision material: algorithmic DEX liquidity, lending and LST redemption arbitrage.
    #: Sources: https://app.lagoon.finance/vault/1/0x3c63f3ce75dc83735745cf4e86b63414d95ee355
    "0x3c63f3ce75dc83735745cf4e86b63414d95ee355": {StrategyTag.algorithmic_trading, StrategyTag.amm, StrategyTag.arbitrage, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.market_making_amm, StrategyTag.multistrategy},
    #: Vault: RockSolid Looped ETH Vault. Added: 2026-08-21.
    #: Decision material: leveraged liquid-staking collateral loop through lending markets.
    #: Sources: https://app.lagoon.finance/vault/1/0x7a12d4b719f5aa479ecd60defed909fb2a37e428
    "0x7a12d4b719f5aa479ecd60defed909fb2a37e428": {StrategyTag.lending, StrategyTag.lending_looping},
    #: Vault: 1212.Stable. Added: 2026-08-21.
    #: Decision material: liquidity provision and stablecoin redemption arbitrage.
    #: Sources: https://app.lagoon.finance/vault/1/0xbb30c3b6046debcbe941281218d18dec8ecebeb5
    "0xbb30c3b6046debcbe941281218d18dec8ecebeb5": {StrategyTag.arbitrage, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: DAMM Stablecoin Fund. Added: 2026-08-21.
    #: Decision material: algorithmic Uniswap liquidity, lending and fixed-income allocation.
    #: Sources: https://app.lagoon.finance/vault/42161/0xe5d6eb448ac5a762c1ebe8cd1692b9cd08025176
    "0xe5d6eb448ac5a762c1ebe8cd1692b9cd08025176": {StrategyTag.algorithmic_trading, StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.market_making_amm, StrategyTag.multistrategy},
    #: Vault: Ammalgam WETH Vault. Added: 2026-08-21.
    #: Decision material: ETH-USDC liquidity provision with impermanent-loss hedge.
    #: Sources: https://app.lagoon.finance/vault/1/0xbb211be8664128e30c6adcd5998eca9592be272f
    "0xbb211be8664128e30c6adcd5998eca9592be272f": {StrategyTag.amm, StrategyTag.liquidity_provider, StrategyTag.market_making_amm},
    #: Vault: Syntropia USDC Core. Added: 2026-08-21.
    #: Decision material: DEX liquidity, lending, arbitrage and incentive farming.
    #: Sources: https://app.lagoon.finance/vault/1/0x1b2cb79a4564206f53ba80b4d780f251b4ae6765
    "0x1b2cb79a4564206f53ba80b4d780f251b4ae6765": {StrategyTag.amm, StrategyTag.arbitrage, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.yield_farming},
    #: Vault: DeltaUSD HyperLiquid USDN Funding Arb. Added: 2026-08-21.
    #: Decision material: delta-neutral perpetual funding arbitrage plus lending/liquidity yield.
    #: Sources: https://app.lagoon.finance/vault/1/0x01f461a0bbb218bc1943aa027c5bbc424391e541
    "0x01f461a0bbb218bc1943aa027c5bbc424391e541": {StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.funding_rate_arbitrage, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.perpetual_futures},
    #: Vault: USDC Avalanche Core. Added: 2026-08-21.
    #: Decision material: money markets, AMM pools and ecosystem incentives.
    #: Sources: https://app.lagoon.finance/vault/43114/0xb3a2bcb30c1460d88db18b42a29fae2399952874
    "0xb3a2bcb30c1460d88db18b42a29fae2399952874": {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.yield_farming},
    #: Vault: stETH Redemption Carry. Added: 2026-08-21.
    #: Decision material: discounted-stETH redemption carry with idle Morpho lending.
    #: Sources: https://app.lagoon.finance/vault/1/0x2746f31096f23670caf4043f8b30d8d02405a257
    "0x2746f31096f23670caf4043f8b30d8d02405a257": {StrategyTag.arbitrage, StrategyTag.carry_trade, StrategyTag.lending},
    #: Vault: Cross-chain USDC Lending. Added: 2026-08-21.
    #: Decision material: cost-aware allocation across Morpho lending markets.
    #: Sources: https://app.lagoon.finance/vault/1/0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89
    "0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89": {StrategyTag.lending, StrategyTag.lending_optimisation},
    #: Vault: DeltaETH HyperLiquid USDN EVERYTHING Funding Arb. Added: 2026-08-21.
    #: Decision material: delta-neutral perpetual funding arbitrage and LP yield.
    #: Sources: https://app.lagoon.finance/vault/1/0x56105f694e9549cebd0d509f1de71b22abe8f1d8
    "0x56105f694e9549cebd0d509f1de71b22abe8f1d8": {StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.funding_rate_arbitrage, StrategyTag.liquidity_provider, StrategyTag.perpetual_futures},
    #: Vault: 9Summits Flagship EURC. Added: 2026-08-21.
    #: Decision material: DEX LP, lending, fixed-income and redemption arbitrage.
    #: Sources: https://app.lagoon.finance/vault/1/0xd0c4c9386f7509c44987f43136be7d4349ccddc9
    "0xd0c4c9386f7509c44987f43136be7d4349ccddc9": {StrategyTag.amm, StrategyTag.arbitrage, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Rho X Liquidity Provider. Added: 2026-08-21.
    #: Decision material: provides trading liquidity and earns spreads and fees.
    #: Sources: https://app.lagoon.finance/vault/1/0x4cb280e63251b9ab24a54def74bf5995d82ff398
    "0x4cb280e63251b9ab24a54def74bf5995d82ff398": {StrategyTag.liquidity_provider, StrategyTag.market_maker, StrategyTag.market_making},
    #: Vault: DeTrade Core USDC. Added: 2026-08-21.
    #: Decision material: liquidity provision alongside yield-bearing stablecoins.
    #: Sources: https://app.lagoon.finance/vault/8453/0x8092ca384d44260ea4feaf7457b629b8dc6f88f0
    "0x8092ca384d44260ea4feaf7457b629b8dc6f88f0": {StrategyTag.liquidity_provider},
    #: Vault: Coinshift USPC High Yield. Added: 2026-08-21.
    #: Decision material: money markets, Curve LP, governance incentives and RWA credit.
    #: Sources: https://app.lagoon.finance/vault/1/0x09252d2c4afca9b1479efdd39faa53de9ff23114
    "0x09252d2c4afca9b1479efdd39faa53de9ff23114": {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.money_market_fund, StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.yield_farming},
    #: Vault: Flint USD. Added: 2026-08-21.
    #: Decision material: tokenised real-estate bonds and private-credit exposure.
    #: Sources: https://app.lagoon.finance/vault/1/0x7f35dea44a192764aa50d50e5f0ece1d5a8b0e45
    "0x7f35dea44a192764aa50d50e5f0ece1d5a8b0e45": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: Gami WBTC. Added: 2026-08-21.
    #: Decision material: money markets, AMM pools and ecosystem incentives.
    #: Sources: https://app.lagoon.finance/vault/1/0x414070fb9e64fd69160d75da57e75ba11f9f605a
    "0x414070fb9e64fd69160d75da57e75ba11f9f605a": {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.yield_farming},
    #: Vault: CEX Automated Funding Rate Arbitrage. Added: 2026-08-21.
    #: Decision material: automated, delta-neutral spot/perpetual funding arbitrage.
    #: Sources: https://app.lagoon.finance/vault/1/0x60a2612996ddfb1aa1c53e7bfbf179ccc1fcd734
    "0x60a2612996ddfb1aa1c53e7bfbf179ccc1fcd734": {StrategyTag.algorithmic_trading, StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.funding_rate_arbitrage, StrategyTag.market_maker, StrategyTag.market_making, StrategyTag.perpetual_futures},
    #: Vault: Autonomous Liquidity USD. Added: 2026-08-21.
    #: Decision material: code-based automated portfolio rebalancing.
    #: Sources: https://app.lagoon.finance/vault/1/0xdcd0f5ab30856f28385f641580bbd85f88349124
    "0xdcd0f5ab30856f28385f641580bbd85f88349124": {StrategyTag.algorithmic_trading},
    #: Vault: DACM LIT Strategy. Added: 2026-08-21.
    #: Decision material: Lighter perpetual-exchange LP, fees and market-neutral inventory.
    #: Sources: https://app.lagoon.finance/vault/42161/0x018282d5b510f00dcacb8f4a81c3901d2fc9da51
    "0x018282d5b510f00dcacb8f4a81c3901d2fc9da51": {StrategyTag.delta_neutral, StrategyTag.liquidity_provider, StrategyTag.market_maker, StrategyTag.market_making, StrategyTag.market_making_clob, StrategyTag.perpetual_futures},
    #: Vault: Usual Invested USD0++ in USCC & USTB. Added: 2026-08-21.
    #: Decision material: USTB money-market exposure and crypto carry allocation.
    #: Sources: https://app.lagoon.finance/vault/1/0x8245fd9ae99a482dfe76576dd4298f799c041d61
    "0x8245fd9ae99a482dfe76576dd4298f799c041d61": {StrategyTag.carry_trade, StrategyTag.money_market_fund, StrategyTag.multistrategy, StrategyTag.rwa},
    #: Vault: 1212.Alpha. Added: 2026-08-21.
    #: Decision material: systematic directional allocation using trend-following signals.
    #: Sources: https://app.lagoon.finance/vault/1/0xc35ce0c1acfc448d18ddacbf641b09f2a18e1958
    "0xc35ce0c1acfc448d18ddacbf641b09f2a18e1958": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    #: Vault: Zharta RWA Prime USDC. Added: 2026-08-21.
    #: Decision material: fixed-rate lending against tokenised RWA credit.
    #: Sources: https://app.lagoon.finance/vault/1/0xb4a4c9a736f91e2694c6b921445eef3e3585a591
    "0xb4a4c9a736f91e2694c6b921445eef3e3585a591": {StrategyTag.lending, StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: Syntropia Boosted. Added: 2026-08-21.
    #: Decision material: synUSD collateral is borrowed against and looped back.
    #: Sources: https://app.lagoon.finance/vault/1/0x8df3deba711ae4a9af16cbca5e4fbb1402f036d5
    "0x8df3deba711ae4a9af16cbca5e4fbb1402f036d5": {StrategyTag.lending, StrategyTag.lending_looping},
    #: Vault: Nova NLP Vault. Added: 2026-08-21.
    #: Decision material: institutional market making with idle capital lent on Morpho.
    #: Sources: https://app.lagoon.finance/vault/999/0xeeed7bb939d65938fe8f40dd898cd5942e32f09e
    "0xeeed7bb939d65938fe8f40dd898cd5942e32f09e": {StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.market_maker, StrategyTag.market_making},
    #: Vault: DAMM Bitcoin Fund. Added: 2026-08-21.
    #: Decision material: current mandate is explicitly market neutral.
    #: Sources: https://app.lagoon.finance/vault/1/0x7ededf832b5c9d8afa8f7365936100581a6db756
    "0x7ededf832b5c9d8afa8f7365936100581a6db756": {StrategyTag.delta_neutral},
    #: Vault: DAMM BTC Algo Fund. Added: 2026-08-21.
    #: Decision material: automated concentrated AMM liquidity with no directional BTC exposure.
    #: Sources: https://app.lagoon.finance/vault/1/0x9c414834a4a85aa15ec461edcd8cc9dd8ec979b9
    "0x9c414834a4a85aa15ec461edcd8cc9dd8ec979b9": {StrategyTag.algorithmic_trading, StrategyTag.amm, StrategyTag.delta_neutral, StrategyTag.liquidity_provider, StrategyTag.market_making_amm},
    #: Vault: Gami ETH. Added: 2026-08-21.
    #: Decision material: money markets, AMM pools and ecosystem incentives.
    #: Sources: https://app.lagoon.finance/vault/1/0x2031eceec018549a2c729cacd6c0bfc4be2524ed
    "0x2031eceec018549a2c729cacd6c0bfc4be2524ed": {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.yield_farming},
    #: Vault: TruMarket. Added: 2026-08-21.
    #: Decision material: agricultural trade-finance opportunities backed by real activity.
    #: Sources: https://app.lagoon.finance/vault/8453/0xbe7db44f4ce20dac83b578b94fd35087f66e9754
    "0xbe7db44f4ce20dac83b578b94fd35087f66e9754": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: DAMM ETH Algo Fund. Added: 2026-08-21.
    #: Decision material: automated concentrated AMM liquidity with no directional ETH exposure.
    #: Sources: https://app.lagoon.finance/vault/1/0xe6f4bc20f08125818baaac57e6f398ffadcb8d28
    "0xe6f4bc20f08125818baaac57e6f398ffadcb8d28": {StrategyTag.algorithmic_trading, StrategyTag.amm, StrategyTag.delta_neutral, StrategyTag.liquidity_provider, StrategyTag.market_making_amm},
    #: Vault: DeTrade Morpho X-Chain USDC. Added: 2026-08-21.
    #: Decision material: continuously rotates USDC between Morpho lending opportunities.
    #: Sources: https://app.lagoon.finance/vault/1/0x7d2c2f54792ad72cb834d298f542145b06b703cb
    "0x7d2c2f54792ad72cb834d298f542145b06b703cb": {StrategyTag.lending, StrategyTag.lending_optimisation},
    #: Vault: DeTrade Core EURC. Added: 2026-08-21.
    #: Decision material: lends collateral, borrows stablecoins and redeploys the proceeds.
    #: Sources: https://app.lagoon.finance/vault/8453/0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550
    "0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550": {StrategyTag.lending},
    #: Vault: DeTrade Core ETH. Added: 2026-08-21.
    #: Decision material: lends liquid-staking collateral and redeploys borrowed stablecoins.
    #: Sources: https://app.lagoon.finance/vault/8453/0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523
    "0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523": {StrategyTag.lending},
    #: Vault: Ammalgam USDC Vault. Added: 2026-08-21.
    #: Decision material: ETH-USDC liquidity provision with impermanent-loss hedge.
    #: Sources: https://app.lagoon.finance/vault/1/0x8417430a31851ae0a36a854394227c5d86be8fc9
    "0x8417430a31851ae0a36a854394227c5d86be8fc9": {StrategyTag.amm, StrategyTag.liquidity_provider, StrategyTag.market_making_amm},
    #: Vault: RockSolid MegaETH USDm Vault. Added: 2026-08-21.
    #: Decision material: deploys USDm for MegaETH DeFi yield and incentives.
    #: Sources: https://app.lagoon.finance/vault/1/0xba71097e426983d840569edfa1a01396b56d86ad
    "0xba71097e426983d840569edfa1a01396b56d86ad": {StrategyTag.yield_farming},
    #: Vault: Alchemix Ecosystem ETH vault. Added: 2026-08-21.
    #: Decision material: blends alAsset liquidity pools, Mix-Yield Tokens and Transmuter positions.
    #: Sources: https://app.lagoon.finance/vault/1/0xa6e8d7871153d030c918a0ca5d33f90779967484
    "0xa6e8d7871153d030c918a0ca5d33f90779967484": {StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Gami hemiBTC. Added: 2026-08-21.
    #: Decision material: Curve liquidity and leveraged money-market positions.
    #: Sources: https://app.lagoon.finance/vault/1/0x2a676c2744421b4fae65ce86b47adacb620047d4
    "0x2a676c2744421b4fae65ce86b47adacb620047d4": {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider},
    #: Vault: Excellion USDC Vault. Added: 2026-08-21.
    #: Decision material: liquidity provision, leveraged lending and explicit yield farming.
    #: Sources: https://app.lagoon.finance/vault/43114/0xb8a14b03900828f863aedd9dd905363863bc31f4
    "0xb8a14b03900828f863aedd9dd905363863bc31f4": {StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.yield_farming},
    #: Vault: SP500 Hyperliquid Synth. Added: 2026-08-21.
    #: Decision material: continuously rebalanced long HIP-3 perpetual position.
    #: Sources: https://app.lagoon.finance/vault/999/0x3b65e02b1ff8072bdc094a899a4e41d07ce034aa
    "0x3b65e02b1ff8072bdc094a899a4e41d07ce034aa": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.perpetual_futures},
    #: Vault: SpaceX Hyperliquid Synth. Added: 2026-08-21.
    #: Decision material: continuously rebalanced long HIP-3 perpetual position.
    #: Sources: https://app.lagoon.finance/vault/999/0x8982df557d81540678285450898f77c31d69c8b2
    "0x8982df557d81540678285450898f77c31d69c8b2": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.perpetual_futures},
    #: Vault: Gami Stake DAO USDC. Added: 2026-08-21.
    #: Decision material: AMM liquidity, lending and governance-reward boosts.
    #: Sources: https://app.lagoon.finance/vault/1/0x33e1339567c183fbadcb43f72d11c47229d468ab
    "0x33e1339567c183fbadcb43f72d11c47229d468ab": {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.yield_farming},
    #: Vault: Noon USN TAC. Added: 2026-08-21.
    #: Decision material: governance incentives and delta-neutral leveraged looping.
    #: Sources: https://app.lagoon.finance/vault/239/0x279385c180f5d01c4a4bdff040f17b8957304762
    "0x279385c180f5d01c4a4bdff040f17b8957304762": {StrategyTag.delta_neutral, StrategyTag.lending_looping, StrategyTag.yield_farming},
    #: Vault: Tulipa HemiBTC. Added: 2026-08-21.
    #: Decision material: HemiBTC liquidity and YieldBasis yield-bearing LP allocation.
    #: Sources: https://app.lagoon.finance/vault/1/0xd548f6ed03e718843124ed29ffd0ed9ae81e6dc5
    "0xd548f6ed03e718843124ed29ffd0ed9ae81e6dc5": {StrategyTag.liquidity_provider, StrategyTag.yield_farming},
}


def get_strategy_tags(address: str) -> set[StrategyTag] | None:
    """Look up a copy of the tags maintained for a Lagoon vault.

    :param address:
        Vault contract address.
    :return:
        A mutable copy of the maintained tags, or ``None`` when unclassified.
    """
    tags = LAGOON_STRATEGY_TAGS.get(address.lower())
    return set(tags) if tags is not None else None
