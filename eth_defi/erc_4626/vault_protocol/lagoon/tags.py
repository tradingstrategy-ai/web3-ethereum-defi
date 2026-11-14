"""Maintained strategy classifications for Lagoon vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: GUSDC (chain 8453).
    #: Added: 2026-10-21.
    #: Decision material: The current exact-address description documents lending, liquidity provision and delta-neutral stablecoin positions.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0xd5c22fa3f7ee979ed7c28e36669b29797ab277e4
    "0xd5c22fa3f7ee979ed7c28e36669b29797ab277e4": {StrategyTag.delta_neutral, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: MSA - Portefeuille Dynamique  (chain 8453).
    #: Added: 2026-10-21.
    #: Decision material: The current exact-address description documents discretionary BTC/ETH and niche-asset allocations, active rebalancing and USDC yield between opportunities.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0xf56bfe07b8d6e6d74258cdb6969a633629b06b08
    "0xf56bfe07b8d6e6d74258cdb6969a633629b06b08": {StrategyTag.directional_trading, StrategyTag.discretionary_trading, StrategyTag.multistrategy},
    #: Vault: DeltaUSD HyperLiquid USDN Funding Arb.
    #: Added: 2026-09-28.
    #: Decision material: The maintained Lagoon description documents
    #: delta-neutral funding-rate arbitrage using offsetting SMARDEX
    #: Perpetuals and Hyperliquid positions, plus Everything Earn allocations
    #: through lending and liquidity markets.
    #: Sources:
    #: - https://app.lagoon.finance/vault/1/0x01f461a0bbb218bc1943aa027c5bbc424391e541
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x01f461a0bBb218Bc1943aa027c5bBC424391E541
    #: - https://app.lagoon.finance/api/vaults?chainId=1&pageIndex=0&pageSize=1000
    #: - https://everything.inc/
    #: - https://everything.inc/unified-liquidity
    "0x01f461a0bbb218bc1943aa027c5bbc424391e541": {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
        StrategyTag.perpetual_futures,
    },
    #: Vault: 9Summits flagship USDC.
    #: Added: 2026-09-28.
    #: Decision material: The maintained Lagoon and 9Summits descriptions
    #: document DEX liquidity provision, money-market lending, fixed-income
    #: positions and buying depegged stablecoins for redemption.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x03d1ec0d01b659b89a87eabb56e4af5cb6e14bfc
    #: - https://app.lagoon.finance/vault/1/0x03D1eC0D01b659b89a87eAbb56e4AF5Cb6e14BFc
    #: - https://vaults.9summits.io/vault/1/0x03D1eC0D01b659b89a87eAbb56e4AF5Cb6e14BFc
    #: - https://vaults.9summits.io/
    "0x03d1ec0d01b659b89a87eabb56e4af5cb6e14bfc": {
        StrategyTag.arbitrage,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: 9Summits flagship ETH.
    #: Added: 2026-09-28.
    #: Decision material: The maintained Lagoon description documents DEX
    #: liquidity provision, money-market lending, fixed-income positions and
    #: buying depegged liquid staking assets for redemption.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x07ed467acd4ffd13023046968b0859781cb90d9b
    #: - https://app.lagoon.finance/vault/1/0x07ed467acD4ffd13023046968b0859781cb90D9B
    #: - https://9summits.io/
    #: - https://vaults.9summits.io/
    "0x07ed467acd4ffd13023046968b0859781cb90d9b": {
        StrategyTag.arbitrage,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: Moon Digital AM ETH.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Odyssey describe this exact-address ETH
    #: product as using active market-neutral strategies and a multi-strategy
    #: approach.
    #: Sources:
    #: - https://app.lagoon.finance/vault/1/0x08d7eef35f3e317001fe12c19a32f93307b008b4
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x08d7eef35f3e317001fe12c19a32f93307b008b4
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://app.lagoon.finance/api/vaults?chainId=1&pageIndex=0&pageSize=1000
    #: - https://www.odysseydigitalam.com/
    "0x08d7eef35f3e317001fe12c19a32f93307b008b4": {
        StrategyTag.delta_neutral,
        StrategyTag.multistrategy,
    },
    #: Vault: Coinshift USPC High Yield.
    #: Added: 2026-09-28.
    #: Reviewed: 2026-11-10.
    #: Decision material: The current mandate explicitly supplies leveraged Curve/StakeDAO LP collateral
    #: through Morpho and actively manages LTV, alongside money markets, liquidity and institutional
    #: MMF/private-credit backing.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x09252d2c4afca9b1479efdd39faa53de9ff23114
    "0x09252d2c4afca9b1479efdd39faa53de9ff23114": {StrategyTag.amm, StrategyTag.lending, StrategyTag.lending_looping, StrategyTag.liquidity_provider, StrategyTag.money_market_fund, StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: Syntropia USDC Core.
    #: Added: 2026-09-28.
    #: Decision material: The maintained product description documents DEX
    #: liquidity provision, money-market lending, fixed-income assets and
    #: arbitrage within Syntropia's delta-neutral multi-strategy offering.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x1b2cb79a4564206f53ba80b4d780f251b4ae6765
    #: - https://app.lagoon.finance/vault/1/0x1b2CB79a4564206f53Ba80b4d780F251b4Ae6765
    #: - https://syntropia.ai/vault/synusd
    #: - https://syntropia.ai/
    #: - https://docs.syntropia.ai/risk
    #: - https://docs.syntropia.ai/transparency
    "0x1b2cb79a4564206f53ba80b4d780f251b4ae6765": {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: Gami ETH.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Gami document allocations across
    #: Morpho, Aave and Silo lending markets, Curve, Balancer and StakeDAO
    #: liquidity pools, and Pendle and Spectra fixed-income strategies.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x2031ECeec018549a2C729caCd6c0BFc4be2524ed
    #: - https://app.lagoon.finance/vault/1/0x2031ECeec018549a2C729caCd6c0BFc4be2524ed
    #: - https://app.lagoon.finance/api/vaults?chainId=1&pageIndex=0&pageSize=1000
    #: - https://gamilabs.io/vaults/1/0x2031eceec018549a2c729cacd6c0bfc4be2524ed
    #: - https://gamilabs.io/
    #: - https://gamilabs.io/about
    #: - https://earnbase.finance/vault/eth-gami-lagoon-ethereum
    #: - https://www.gami.capital/on-chain-strategies/
    "0x2031eceec018549a2c729cacd6c0bfc4be2524ed": {
        StrategyTag.amm,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: stETH Redemption Carry.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon documents buying discounted stETH on Curve,
    #: redeeming it at par through Lido's withdrawal queue, and parking idle
    #: capital in Morpho while the carry clears.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x2746f31096f23670caf4043f8b30d8d02405a257
    #: - https://app.lagoon.finance/vault/1/0x2746f31096f23670caf4043f8b30d8d02405a257
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://app.lagoon.finance/
    #: - https://www.sigmalabs.fi
    #: - https://www.ellen.capital
    #: - https://docs.lido.fi/contracts/withdrawal-queue-erc721/
    #: - https://docs.lido.fi/guides/lido-tokens-integration-guide/
    "0x2746f31096f23670caf4043f8b30d8d02405a257": {
        StrategyTag.amm,
        StrategyTag.arbitrage,
        StrategyTag.carry_trade,
        StrategyTag.lending,
        StrategyTag.multistrategy,
    },
    #: Vault: Mt Pelerin – USD strategy pool.
    #: Added: 2026-09-28.
    #: Reviewed: 2026-11-10.
    #: Decision material: The exact deployment automatically allocates among protocols under predefined
    #: rules, using delta-neutral and hedging techniques.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x28663161f9fa2963eb6102b88a741e195e974df6
    "0x28663161f9fa2963eb6102b88a741e195e974df6": {StrategyTag.algorithmic_trading, StrategyTag.delta_neutral, StrategyTag.multistrategy},
    #: Vault: Gami hemiBTC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Gami document Curve tripool liquidity,
    #: leveraged money-market positions and a later YieldBasis allocation
    #: split between yield-bearing and staked hemiBTC.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x2a676c2744421b4fae65ce86b47adacb620047d4
    #: - https://app.lagoon.finance/vault/1/0x2a676c2744421b4fae65ce86b47adacb620047d4
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://gamilabs.io/vaults/1/0x2a676c2744421b4fae65ce86b47adacb620047d4
    #: - https://gamilabs.io/vaults
    #: - https://hemi.xyz/
    #: - https://yieldbasis.com/
    #: - https://docs.yieldbasis.com/
    "0x2a676c2744421b4fae65ce86b47adacb620047d4": {
        StrategyTag.amm,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: Moon Digital AM BTC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon categorises the exact-address product as
    #: market-neutral, and Odyssey describes its BTC product as generating
    #: yield without exposure to price movements.
    #: Sources:
    #: - https://app.lagoon.finance/vault/1/0x2f945864126c6ba1dcadcb97ad114c9ef94f1379
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x2f945864126c6ba1dcadcb97ad114c9ef94f1379
    #: - https://www.odysseydigitalam.com/
    "0x2f945864126c6ba1dcadcb97ad114c9ef94f1379": {
        StrategyTag.delta_neutral,
    },
    #: Vault: Gami Stake DAO USDC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon documents stablecoin liquidity provision and
    #: lending across Curve, Pendle, Balancer, f(x), Yearn and StakeDAO, with
    #: most capital allocated to LP strategies.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x33e1339567c183fbadcb43f72d11c47229d468ab
    #: - https://app.lagoon.finance/vault/1/0x33e1339567c183fbadcb43f72d11c47229d468ab
    "0x33e1339567c183fbadcb43f72d11c47229d468ab": {
        StrategyTag.amm,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: DAMM Ethereum Fund.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and DAMM document market-neutral algorithmic
    #: LST/LRT liquidity provision and market making on Uniswap, lending,
    #: redemption arbitrage, and discretionary multi-strategy allocation.
    #: Sources:
    #: - https://app.lagoon.finance/vault/1/0x3c63f3cE75dc83735745CF4e86B63414D95Ee355
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x3c63f3ce75dc83735745cf4e86b63414d95ee355
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://dammcap.finance/
    #: - https://dammcap.finance/funds/dammeth/
    "0x3c63f3ce75dc83735745cf4e86b63414d95ee355": {
        StrategyTag.algorithmic_trading,
        StrategyTag.amm,
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.discretionary_trading,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_amm,
        StrategyTag.multistrategy,
    },
    #: Vault: Gami WBTC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Gami document WBTC allocations across
    #: Aave and Morpho money markets, Pendle and Spectra fixed-income
    #: strategies, and Curve and approved-DEX liquidity pools.
    #: Sources:
    #: - https://app.lagoon.finance/vault/1/0x414070fB9e64Fd69160d75Da57E75bA11f9F605A
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x414070fB9e64Fd69160d75Da57E75bA11f9F605A
    #: - https://gamilabs.io/vaults/1/0x414070fb9e64fd69160d75da57e75ba11f9f605a
    #: - https://gamilabs.io/vaults
    #: - https://gamilabs.io/about
    "0x414070fb9e64fd69160d75da57e75ba11f9f605a": {
        StrategyTag.amm,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: Rho X Liquidity Provider.
    #: Added: 2026-09-28.
    #: Decision material: The exact-address Lagoon metadata documents a
    #: market-neutral multi-strategy vault that supplies liquidity across Rho
    #: products for market making, trading and price discovery.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x4cb280e63251b9ab24a54def74bf5995d82ff398
    #: - https://app.lagoon.finance/vault/1/0x4cb280e63251b9ab24a54def74bf5995d82ff398
    #: - https://rho.trading/vaults
    #: - https://rho.trading/
    #: - https://rho.trading/blog/cryptonative-rates-market-rho-protocol-announces-public-launch
    #: - https://etherscan.io/address/0x4cb280e63251b9ab24a54def74bf5995d82ff398
    "0x4cb280e63251b9ab24a54def74bf5995d82ff398": {
        StrategyTag.delta_neutral,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.multistrategy,
    },
    #: Vault: DeltaETH HyperLiquid USDN EVERYTHING Funding Arb.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon documents offsetting SMARDEX and Hyperliquid
    #: perpetual positions for funding-rate arbitrage, plus USDN-yield and
    #: Everything liquidity allocations while maintaining neutral ETH delta.
    #: Sources:
    #: - https://app.lagoon.finance/vault/1/0x56105f694e9549cebd0d509f1de71b22abe8f1d8
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x56105f694e9549cebd0d509f1de71b22abe8f1d8
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://everything.inc/
    #: - https://everything.inc/unified-liquidity
    "0x56105f694e9549cebd0d509f1de71b22abe8f1d8": {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
        StrategyTag.perpetual_futures,
    },
    #: Vault: CEX Automated Funding Rate Arbitrage.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon documents a fully automated, delta-neutral
    #: spot and perpetual funding-rate arbitrage strategy using high-frequency
    #: market-making algorithms across centralised exchanges.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x60a2612996ddfb1aa1c53e7bfbf179ccc1fcd734
    #: - https://app.lagoon.finance/vault/1/0x60a2612996ddfb1aa1c53e7bfbf179ccc1fcd734
    #: - https://everything.inc/
    #: - https://everything.inc/unified-liquidity
    "0x60a2612996ddfb1aa1c53e7bfbf179ccc1fcd734": {
        StrategyTag.algorithmic_trading,
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.market_making,
        StrategyTag.perpetual_futures,
    },
    #: Vault: 9Summits Short Duration Institutional Credit — Powered by Rekord.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Rekord document short-duration,
    #: fixed-payment lending to offchain borrowers backed by receivables,
    #: collateral and structured private credit.
    #: Sources:
    #: - https://app.lagoon.finance/vault/1/0x6a39fe461278376f61acbbb6759550e24a78a7b2
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x6a39fe461278376f61acbbb6759550e24a78a7b2
    #: - https://app.lagoon.finance/api/vaults?chainId=1&pageIndex=0&pageSize=1000
    #: - https://www.rekord.io/
    #: - https://www.rekord.io/education-hub/what-rekord-does/
    #: - https://9summits.io/
    "0x6a39fe461278376f61acbbb6759550e24a78a7b2": {
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
        StrategyTag.rwa_lending,
    },
    #: Vault: RockSolid Looped ETH Vault.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and RockSolid document repeatedly supplying
    #: stETH to lending markets, borrowing ETH and restaking it at controlled
    #: leverage.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x7a12d4b719f5aa479ecd60defed909fb2a37e428
    #: - https://app.lagoon.finance/vault/1/0x7a12d4b719f5aa479ecd60defed909fb2a37e428
    #: - https://app.rocksolid.network/vaults/0x7a12d4b719f5aa479ecd60defed909fb2a37e428
    #: - https://app.rocksolid.network/
    #: - https://blog.lido.fi/lido-v3-pier-two-x-rocksolid-expanding-institutional-ethereum-staking-with-stvaults/
    "0x7a12d4b719f5aa479ecd60defed909fb2a37e428": {
        StrategyTag.lending,
        StrategyTag.lending_looping,
    },
    #: Vault: DAMM Bitcoin Fund.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and DAMM document discretionary allocation
    #: into a rule-based, market-neutral, concentrated-liquidity market-making
    #: strategy for WBTC and BTC tokens on decentralised exchanges.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x7ededf832b5c9d8afa8f7365936100581a6db756
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://app.lagoon.finance/vault/1/0x7ededf832b5c9d8afa8f7365936100581a6db756
    #: - https://dammcap.finance/
    #: - https://docs.dammcap.finance/funds/dammbtc
    #: - https://docs.dammcap.finance/funds
    #: - https://docs.dammcap.finance/integrations/dammbtc
    #: - https://docs.dammcap.finance/integrations/dammbtcalgo
    #: - https://docs.dammcap.finance/integrations
    #: - https://docs.dammcap.finance/introduction
    "0x7ededf832b5c9d8afa8f7365936100581a6db756": {
        StrategyTag.algorithmic_trading,
        StrategyTag.amm,
        StrategyTag.delta_neutral,
        StrategyTag.discretionary_trading,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_amm,
    },
    #: Vault: Flint USD.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Flint document tokenised real-estate
    #: bonds and private-credit lending backed by real-world collateral.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x7f35dEa44a192764aa50d50e5f0eCE1d5a8b0e45
    #: - https://app.lagoon.finance/vault/1/0x7f35dEa44a192764aa50d50e5f0eCE1d5a8b0e45
    #: - https://flintrwa.xyz/
    #: - https://flintrwa.xyz/vault/0x7f35dea44a192764aa50d50e5f0ece1d5a8b0e45
    #: - https://9summits.io/
    "0x7f35dea44a192764aa50d50e5f0ece1d5a8b0e45": {
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
        StrategyTag.rwa_lending,
    },
    #: Vault: Usual Invested USD0++ in USCC & USTB.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon documents allocations to Superstate USTB
    #: short-duration US Treasury bills and USCC crypto basis trades, which
    #: pair long spot assets with offsetting short futures.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x8245fd9ae99a482dfe76576dd4298f799c041d61
    #: - https://app.lagoon.finance/vault/1/0x8245FD9Ae99A482dFe76576dd4298f799c041D61
    #: - https://superstate.com/assets/uscc
    #: - https://superstate.com/assets/ustb
    #: - https://docs.usual.money/resources-and-ecosystem/fact-sheets/usual-products/busd0
    "0x8245fd9ae99a482dfe76576dd4298f799c041d61": {
        StrategyTag.arbitrage,
        StrategyTag.carry_trade,
        StrategyTag.delta_neutral,
        StrategyTag.money_market_fund,
        StrategyTag.multistrategy,
        StrategyTag.rwa,
    },
    #: Vault: Ammalgam USDC Vault.
    #: Added: 2026-09-28.
    #: Reviewed: 2026-11-10.
    #: Decision material: The current mandate describes AMM market-making and lending with zero-target ETH
    #: delta. Impermanent-loss convexity does not establish an options instrument.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x8417430a31851ae0a36a854394227c5d86be8fc9
    "0x8417430a31851ae0a36a854394227c5d86be8fc9": {StrategyTag.amm, StrategyTag.delta_neutral, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.market_making_amm, StrategyTag.multistrategy},
    #: Vault: Syntropia Boosted.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Syntropia document supplying synUSD as
    #: collateral, borrowing USDC and looping the proceeds back into synUSD.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x8dF3dEba711AE4A9af16cbcA5e4fbB1402f036D5
    #: - https://app.lagoon.finance/vault/1/0x8dF3dEba711AE4A9af16cbcA5e4fbB1402f036D5
    #: - https://syntropia.ai/
    #: - https://syntropia.ai/vault/synusd
    #: - https://syntropia.ai/vault/synusdx
    "0x8df3deba711ae4a9af16cbca5e4fbb1402f036d5": {
        StrategyTag.lending,
        StrategyTag.lending_looping,
    },
    #: Vault: RockSolid rETH Vault.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and RockSolid document lending-market
    #: allocation and optimisation, leveraged staking loops, Balancer AMM
    #: liquidity provision and a separate Spectra stablecoin carry sleeve.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x936facdf10c8c36294e7b9d28345255539d81bc7
    #: - https://app.lagoon.finance/vault/1/0x936facdf10c8c36294e7b9d28345255539d81bc7
    #: - https://app.rocksolid.network/vaults/0x936facdf10c8c36294e7b9d28345255539d81bc7
    #: - https://app.rocksolid.network/
    #: - https://blog.rocksolid.network/the-balancer-exploit-and-stream-collapse-how-vaults-can-strengthen-defi/
    #: - https://blog.rocksolid.network/
    #: - https://blog.rocksolid.network/rocket-pool-x-rocksolid-reth-liquid-vault/
    #: - https://blog.rocksolid.network/a-rocksolid-retrospective-one-month-post-launch/
    #: - https://blog.rocksolid.network/introducing-rocksolid-liquid-vaults/
    "0x936facdf10c8c36294e7b9d28345255539d81bc7": {
        StrategyTag.amm,
        StrategyTag.carry_trade,
        StrategyTag.lending,
        StrategyTag.lending_looping,
        StrategyTag.lending_optimisation,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: DAMM BTC Algo Fund.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and DAMM document automated, rule-based,
    #: market-neutral concentrated-liquidity market making across Uniswap and
    #: Aerodrome pools pairing BTC-pegged assets.
    #: Sources:
    #: - https://app.lagoon.finance/vault/1/0x9c414834a4a85aa15ec461edcd8cc9dd8ec979b9
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x9c414834a4a85aa15ec461edcd8cc9dd8ec979b9
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://docs.dammcap.finance/integrations/dammbtcalgo
    #: - https://docs.dammcap.finance/funds/dammbtc
    #: - https://docs.dammcap.finance/funds
    "0x9c414834a4a85aa15ec461edcd8cc9dd8ec979b9": {
        StrategyTag.algorithmic_trading,
        StrategyTag.amm,
        StrategyTag.delta_neutral,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_amm,
    },
    #: Vault: Moon Digital AM USDC.
    #: Added: 2026-09-28.
    #: Decision material: Odyssey links its USDC product to this Lagoon vault
    #: and describes it as market-neutral stablecoin yield without directional
    #: price bets. No narrower mechanism is documented for this address.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xa00f63e85b3d242568a9edecb48f5e2cf879b07b
    #: - https://app.lagoon.finance/vault/1/0xa00f63e85b3d242568a9edecb48f5e2cf879b07b
    #: - https://www.odysseydigitalam.com/
    "0xa00f63e85b3d242568a9edecb48f5e2cf879b07b": {
        StrategyTag.delta_neutral,
    },
    #: Vault: Alchemix Ecosystem ETH vault.
    #: Added: 2026-09-28.
    #: Decision material: The vault combines alAsset AMM liquidity pools,
    #: diversified Mix-Yield Tokens and fixed-yield Transmuter positions. The
    #: Transmuter sleeve acquires discounted alAssets and redeems them at par.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xa6e8d7871153d030c918a0ca5d33f90779967484
    #: - https://app.lagoon.finance/vault/1/0xa6e8d7871153d030c918a0ca5d33f90779967484
    #: - https://docs.alchemix.fi/user/tutorials/ecosystem-vaults
    #: - https://docs.alchemix.fi/user/tutorials/use-passive-myt
    #: - https://docs.alchemix.fi/
    #: - https://docs.alchemix.fi/Alchemix_Q2_2024_Financial_Report.pdf
    #: - https://docs.alchemix.fi/Alchemix_Q3_2022_Financial_Report%20%281%29.pdf
    #: - https://docs.alchemix.fi/Alchemix_Q2_2023_Financial_Report.pdf
    "0xa6e8d7871153d030c918a0ca5d33f90779967484": {
        StrategyTag.amm,
        StrategyTag.arbitrage,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: Mt Pelerin – ETH strategy pool.
    #: Added: 2026-09-28.
    #: Reviewed: 2026-11-10.
    #: Decision material: The exact deployment automatically allocates among protocols under predefined
    #: rules, using delta-neutral and hedging techniques.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xb118de4917f4bac5c3a453ea8de915a7cd84891c
    "0xb118de4917f4bac5c3a453ea8de915a7cd84891c": {StrategyTag.algorithmic_trading, StrategyTag.delta_neutral, StrategyTag.multistrategy},
    #: Vault: Zharta RWA Prime USDC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Zharta document fixed-rate institutional
    #: lending against tokenised Treasury bills, money-market funds and private
    #: credit through individually underwritten bilateral facilities.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xb4a4c9a736f91e2694c6b921445eef3e3585a591
    #: - https://app.lagoon.finance/vault/1/0xB4a4C9a736f91E2694c6b921445eeF3E3585a591
    #: - https://www.zharta.io/
    #: - https://www.zharta.io/blog/first-fixed-rate-borrow-loop-on-tokenized-securities
    #: - https://www.zharta.io/blog/why-fixed-rate-onchain-credit-matters
    #: - https://www.zharta.io/blog/zharta-and-kamui-execute-first-asynchronous-redemption-unwind-for-leveraged-positions-on-tokenized-securities-debuting-on-acred-from-securitize
    #: - https://institutional.zharta.io/lending/loan/0xbce4dcab3f6d6cd7f066f61888d883bed4e5ff7d50e349b60a83a7957fd297dc/activity
    "0xb4a4c9a736f91e2694c6b921445eef3e3585a591": {
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
        StrategyTag.rwa_lending,
    },
    #: Vault: RockSolid MegaETH USDm Vault.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon classifies the vault as market-neutral, and
    #: RockSolid reports reallocating stablecoins between Aave and Spark lending
    #: markets in response to utilisation and risk-adjusted yield.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xba71097e426983d840569edfa1a01396b56d86ad
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://app.lagoon.finance/vault/1/0xba71097e426983d840569edfa1a01396b56d86ad
    #: - https://app.rocksolid.network/vaults/0xba71097e426983d840569edfa1a01396b56d86ad
    #: - https://blog.rocksolid.network/now-live-the-rocksolid-megaeth-usdm-vault/
    #: - https://www.megaeth.com/blog-news/rocksolid-brings-usdm-to-ethereum
    "0xba71097e426983d840569edfa1a01396b56d86ad": {
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.lending_optimisation,
    },
    #: Vault: Ammalgam WETH Vault.
    #: Added: 2026-09-28.
    #: Reviewed: 2026-11-10.
    #: Decision material: The current mandate explicitly targets approximately 100% ETH delta alongside AMM
    #: market-making and lending. Impermanent loss is not itself an options allocation.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xbb211be8664128e30c6adcd5998eca9592be272f
    "0xbb211be8664128e30c6adcd5998eca9592be272f": {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.market_making_amm, StrategyTag.multistrategy},
    #: Vault: 1212.Stable.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and 1212 Capital document a diversified USD
    #: income allocation spanning AMM liquidity, lending, market-neutral
    #: arbitrage, fixed-income strategies and tokenised real-world assets.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xbb30c3b6046debcbe941281218d18dec8ecebeb5
    #: - https://app.lagoon.finance/vault/1/0xbb30c3b6046debcbe941281218d18dec8ecebeb5
    #: - https://1212.capital/
    #: - https://1212.capital/terms
    #: - https://1212capital.octav.fi/app/Vault?board=overview
    "0xbb30c3b6046debcbe941281218d18dec8ecebeb5": {
        StrategyTag.amm,
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
        StrategyTag.rwa,
    },
    #: Vault: Mt Pelerin – BTC strategy pool.
    #: Added: 2026-09-28.
    #: Reviewed: 2026-11-10.
    #: Decision material: The exact deployment automatically allocates among protocols under predefined
    #: rules, using delta-neutral and hedging techniques.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xbc6a48b30405cc1660627d8dfb3872505f14bca0
    "0xbc6a48b30405cc1660627d8dfb3872505f14bca0": {StrategyTag.algorithmic_trading, StrategyTag.delta_neutral, StrategyTag.multistrategy},
    #: Vault: 1212.Alpha.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and 1212 Capital document systematic,
    #: quantitative adjustment of directional digital-asset exposure using
    #: trend, momentum and sentiment signals, with a separate USD-yield sleeve.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xc35ce0c1acfc448d18ddacbf641b09f2a18e1958
    #: - https://app.lagoon.finance/vault/1/0xC35CE0c1aCFC448d18dDAcBF641B09F2A18E1958
    #: - https://www.1212.capital/
    "0xc35ce0c1acfc448d18ddacbf641b09f2a18e1958": {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.multistrategy,
        StrategyTag.trend_following,
    },
    #: Vault: Hub Capital USDC vault.
    #: Added: 2026-09-28.
    #: Decision material: The exact Lagoon composition documents distinct
    #: Stake DAO stablecoin AMM-liquidity, Pendle fixed-income and Morpho
    #: lending sleeves.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xca790385506b790554571cbc9da73f0130cdcfd5
    #: - https://app.lagoon.finance/vault/1/0xca790385506b790554571cbc9da73f0130cdcfd5
    #: - https://gov.stakedao.org/t/stake-dao-association-june-2026-report/1143
    #: - https://gov.stakedao.org/t/lmap-4-enable-the-new-stake-dao-frxusd-crvusd-91-5-ltv-curve-lp-market-as-collateral-in-the-stake-dao-frxusd-v2-vault/1148
    #: - https://gov.stakedao.org/t/lmap-2-add-frxusd-sdola-stake-dao-lp-as-collateral-to-the-stake-dao-frxusd-v2-vault/1133
    "0xca790385506b790554571cbc9da73f0130cdcfd5": {
        StrategyTag.amm,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: Tulipa USDC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon documents an actively managed, USD-denominated
    #: mix of tokenised real-world assets, RWA lending, fixed-yield products,
    #: structured liquidity-provider arrangements and incentivised liquidity.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xce0b790ae0d8cf91e01f3fb69025e14569b574f3
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://app.lagoon.finance/vault/1/0xce0b790ae0d8cf91e01f3fb69025e14569b574f3
    #: - https://tulipa-usdc.tulipa.capital/
    #: - https://tulipa.capital/
    "0xce0b790ae0d8cf91e01f3fb69025e14569b574f3": {
        StrategyTag.delta_neutral,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
        StrategyTag.rwa,
        StrategyTag.rwa_lending,
    },
    #: Vault: 9Summits Flagship EURC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and 9Summits document DEX liquidity provision,
    #: money-market lending, fixed-income positions and buying depegged
    #: stablecoins for redemption.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xd0c4c9386f7509c44987f43136be7d4349ccddc9
    #: - https://app.lagoon.finance/vault/1/0xD0C4C9386F7509c44987F43136BE7d4349Ccddc9
    #: - https://vaults.9summits.io/vault/1/0xD0C4C9386F7509c44987F43136BE7d4349Ccddc9
    #: - https://vaults.9summits.io/
    #: - https://9summits.io/
    "0xd0c4c9386f7509c44987f43136be7d4349ccddc9": {
        StrategyTag.arbitrage,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: Syntropia USDC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Syntropia document senior lending into a
    #: shared delta-neutral portfolio spanning DEX liquidity provision,
    #: money-market lending, fixed-income assets and arbitrage.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xd17049ed25d8f99fe3bfd10cef2263da9995cfd8
    #: - https://app.lagoon.finance/vault/1/0xd17049ed25d8f99fe3bfd10cef2263da9995cfd8
    #: - https://syntropia.ai/
    #: - https://syntropia.ai/discover
    #: - https://syntropia.ai/vault/synusd
    #: - https://docs.syntropia.ai/
    #: - https://docs.syntropia.ai/risk
    #: - https://docs.syntropia.ai/infrastructure
    #: - https://docs.syntropia.ai/transparency
    #: - https://docs.syntropia.ai/why-syntropia
    "0xd17049ed25d8f99fe3bfd10cef2263da9995cfd8": {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: Tulipa HemiBTC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and YieldBasis document hemiBTC capital in
    #: staked and unstaked Curve Cryptoswap LP positions paired with borrowed
    #: crvUSD and designed to neutralise LP divergence loss.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xd548f6ed03e718843124ed29ffd0ed9ae81e6dc5
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://app.lagoon.finance/vault/1/0xd548f6ed03e718843124ed29ffd0ed9ae81e6dc5
    #: - https://yieldbasis.com/earn
    #: - https://docs.yieldbasis.com/user/overview/how-yieldbasis-works
    #: - https://hemi.xyz/blog/hemi-is-home-for-btc-yield/
    "0xd548f6ed03e718843124ed29ffd0ed9ae81e6dc5": {
        StrategyTag.amm,
        StrategyTag.delta_neutral,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_amm,
    },
    #: Vault: Gami USDC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Gami document dynamic allocation across
    #: several lending markets, Curve and Balancer liquidity, Pendle and Spectra
    #: fixed-income strategies, and broadly identified RWA destinations.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xdae854d0896ad2fee335689a3f7b4a95fd1a3e46
    #: - https://app.lagoon.finance/vault/1/0xdAe854D0896ad2fEE335689A3F7B4a95fd1a3E46
    #: - https://gamilabs.io/vaults/1/0xdae854d0896ad2fee335689a3f7b4a95fd1a3e46
    #: - https://gamilabs.io/vaults
    #: - https://gamilabs.io/
    "0xdae854d0896ad2fee335689a3f7b4a95fd1a3e46": {
        StrategyTag.amm,
        StrategyTag.lending,
        StrategyTag.lending_optimisation,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
        StrategyTag.rwa,
    },
    #: Vault: Autonomous Liquidity USD.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Almanak document a code-based strategy
    #: that continuously evaluates and reallocates USDC among supply-side DeFi
    #: lending venues, rebalances positions and compounds yield automatically.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xdcd0f5ab30856f28385f641580bbd85f88349124
    #: - https://app.lagoon.finance/vault/1/0xdcd0f5ab30856f28385f641580bbd85f88349124
    #: - https://app.almanak.run/
    #: - https://app.almanak.co/vaults
    #: - https://almanak.co/
    #: - https://docs.almanak.co/
    #: - https://github.com/almanak-co/sdk
    #: - https://www6.twstalker.com/almanak/status/1987438988702228529
    #: - https://forum.euler.finance/t/proposal-integrate-pt-alusd-11dec2025-on-euler-yield/1685
    "0xdcd0f5ab30856f28385f641580bbd85f88349124": {
        StrategyTag.algorithmic_trading,
        StrategyTag.lending,
        StrategyTag.lending_optimisation,
    },
    #: Vault: DAMM ETH Algo Fund.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and DAMM document automated, rule-based,
    #: market-neutral concentrated-liquidity market making in LST/WETH pools
    #: across Uniswap and Aerodrome.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xe6f4bc20f08125818baaac57e6f398ffadcb8d28
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://app.lagoon.finance/vault/1/0xe6f4bc20f08125818baaac57e6f398ffadcb8d28
    #: - https://docs.dammcap.finance/integrations/dammethalgo
    #: - https://docs.dammcap.finance/integrations
    #: - https://docs.dammcap.finance/funds
    "0xe6f4bc20f08125818baaac57e6f398ffadcb8d28": {
        StrategyTag.algorithmic_trading,
        StrategyTag.amm,
        StrategyTag.delta_neutral,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_amm,
    },
    #: Vault: Cross-chain USDC Lending.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Sigma Labs document a cost-aware optimiser
    #: that lends USDC across whitelisted Morpho Blue markets on seven chains
    #: and automatically moves capital when the net yield improvement warrants.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89
    #: - https://app.lagoon.finance/vault/1/0xF02030AB0d7385CE4CC2f7F64b7B44430fB44c89
    #: - https://www.sigmalabs.fi/vault/0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89
    #: - https://www.sigmalabs.fi/
    #: - https://www.sigmalabs.fi/papers/cross-chain-usdc-lending-governance.pdf
    #: - https://www.ellen.capital/
    "0xf02030ab0d7385ce4cc2f7f64b7b44430fb44c89": {
        StrategyTag.algorithmic_trading,
        StrategyTag.lending,
        StrategyTag.lending_optimisation,
    },
    #: Vault: Coinshift USPC Prime, previously Coinshift Conservative USPC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon documents TermMax lending, Curve and StakeDAO
    #: AMM liquidity, Pendle and Spectra fixed-income positions, institutional
    #: money-market funds and Apollo tokenised private credit.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xfab0f56c28e3f874b15922b213e696f37b670916
    #: - https://app.lagoon.finance/vault/1/0xfab0f56c28e3f874b15922b213e696f37b670916
    #: - https://gamilabs.io/vaults/1/0xfab0f56c28e3f874b15922b213e696f37b670916
    #: - https://www.coinshift.xyz/
    #: - https://docs.coinshift.xyz/
    #: - https://telosc.com/research/coinshift-dd
    #: - https://defillama.com/rwa/asset/iUSPC
    "0xfab0f56c28e3f874b15922b213e696f37b670916": {
        StrategyTag.amm,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.money_market_fund,
        StrategyTag.multistrategy,
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
    },
    #: Vault: DeTrade Core AUSD.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon documents a fully automated strategy that
    #: rotates AUSD among eligible Monad stablecoin incentive campaigns, claims
    #: rewards every eight hours and compounds them.
    #: Sources:
    #: - https://app.lagoon.finance/vault/143/0xe95074a86fead8647edff36af2f3f1dd7e8f7adb
    #: - https://app.lagoon.finance/api/vault?chainId=143&address=0xe95074a86fead8647edff36af2f3f1dd7e8f7adb
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://detrade.fund/
    #: - https://docs.merkl.xyz/
    #: - https://monadscan.com/address/0xe95074a86fead8647edff36af2f3f1dd7e8f7adb
    "0xe95074a86fead8647edff36af2f3f1dd7e8f7adb": {
        StrategyTag.algorithmic_trading,
    },
    #: Vault: SP500 Hyperliquid Synth.
    #: Added: 2026-09-28.
    #: Reviewed: 2026-11-10.
    #: Decision material: Automatic rebalancing targets approximately 1x S&P 500 exposure, with idle funds
    #: in Hyperliquid Earn lending; account-level margin is not leveraged portfolio exposure.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=999&address=0x3b65e02b1ff8072bdc094a899a4e41d07ce034aa
    "0x3b65e02b1ff8072bdc094a899a4e41d07ce034aa": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.index, StrategyTag.lending, StrategyTag.multistrategy, StrategyTag.perpetual_futures},
    #: Vault: 722 Capital Hype.
    #: Added: 2026-09-28.
    #: Decision material: 722 Capital documents a delta-neutral HYPE mandate,
    #: and its live exact-vault portfolio shows HyperLend supply, Kinetiq
    #: staking and dated HYPE call and put positions on Derive.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=999&address=0x7dd73a986f6188da1baaf68541fd6ebd455abd1d
    #: - https://app.lagoon.finance/vault/999/0x7dd73a986f6188da1baaf68541fd6ebd455abd1d
    #: - https://722.capital/
    #: - https://app.722.capital/
    #: - https://app.722.capital/vaults
    #: - https://app.722.capital/api/vaults
    #: - https://app.722.capital/api/vaults/722-capital-hype
    #: - https://app.722.capital/api/portfolio?vaultSlug=722-capital-hype
    #: - https://app.722.capital/api/vaults/722-capital-hype/activity
    #: - https://www.linkedin.com/posts/722-capital_defi-onchain-assetmanagement-activity-7475224053499195392-pAEX
    #: - https://kinetiq.xyz/
    #: - https://docs.derive.xyz/
    "0x7dd73a986f6188da1baaf68541fd6ebd455abd1d": {
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.multistrategy,
        StrategyTag.options,
    },
    #: Vault: SpaceX Hyperliquid Synth.
    #: Added: 2026-09-28.
    #: Reviewed: 2026-11-10.
    #: Decision material: Automatic rebalancing targets approximately 1x SpaceX perpetual exposure, with
    #: idle funds in Hyperliquid Earn. Account margin does not establish leveraged portfolio exposure.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=999&address=0x8982df557d81540678285450898f77c31d69c8b2
    "0x8982df557d81540678285450898f77c31d69c8b2": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.lending, StrategyTag.multistrategy, StrategyTag.perpetual_futures},
    #: Vault: Nova NLP.
    #: Added: 2026-09-28.
    #: Decision material: Nova documents two-sided order-book liquidity for
    #: perpetual markets that graduate to Hyperliquid, while Lagoon documents
    #: a market-neutral mandate and an optional Morpho idle-capital sleeve.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=999&address=0xeeed7bb939d65938fe8f40dd898cd5942e32f09e
    #: - https://app.lagoon.finance/vault/999/0xEeEd7BB939d65938Fe8f40dd898Cd5942E32f09E
    #: - https://docs.nova.markets/#/nlp-vault
    #: - https://docs.nova.markets/#/what-nova
    #: - https://nova.markets/
    #: - https://app.nova.markets/
    "0xeeed7bb939d65938fe8f40dd898cd5942e32f09e": {
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_clob,
        StrategyTag.multistrategy,
        StrategyTag.perpetual_futures,
    },
    #: Vault: Venice AI - Liquid wrapped sVVV.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Venice document staking deposited VVV
    #: into sVVV to earn continuously accruing VVV emissions while retaining a
    #: small unstaked withdrawal buffer.
    #: Sources:
    #: - https://app.lagoon.finance/vault/8453/0x370f89ddd85ca2afcd2c891e67d10289d9d8d2d1
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0x370f89ddd85ca2afcd2c891e67d10289d9d8d2d1
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://venice.ai/faqs/all
    #: - https://cdn.venice.ai/lp/vvv
    "0x370f89ddd85ca2afcd2c891e67d10289d9d8d2d1": {
        StrategyTag.carry_trade,
    },
    #: Vault: MoneyFi FlowForge Vault.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and MoneyFi document software-driven,
    #: risk-scored allocation and auto-compounding across several Aptos
    #: stablecoin pools, including concentrated-liquidity venues.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0x4efc07dca8697792119484af33549f33ab11bf3c
    #: - https://app.lagoon.finance/vault/8453/0x4efc07dca8697792119484af33549f33ab11bf3c
    #: - https://moneyfi.fund/
    #: - https://moneyfi.fund/assets/index-B7zfE_eN.js
    #: - https://t.me/s/moneyfihub
    #: - https://be.moneyfi.fund/vaults
    #: - https://docs.moar.market/
    "0x4efc07dca8697792119484af33549f33ab11bf3c": {
        StrategyTag.algorithmic_trading,
        StrategyTag.amm,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_amm,
        StrategyTag.multistrategy,
    },
    #: Vault: 722Capital-ETH.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon classifies this exact ETH-yield vault as
    #: market-neutral. The curator confirms the product identity but does not
    #: publish enough address-specific detail for narrower mechanism tags.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0xfce2064b4221c54651b21c868064a23695e78f09
    #: - https://app.lagoon.finance/vault/8453/0xfce2064b4221c54651b21c868064a23695e78f09
    #: - https://722.capital/
    #: - https://app.722.capital/
    #: - https://app.722.capital/v/722-eth
    "0xfce2064b4221c54651b21c868064a23695e78f09": {
        StrategyTag.delta_neutral,
    },
    #: Vault: Plasma fxSAVE.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon restricts this vault to fxSAVE, whose official
    #: documentation describes a USD delta-neutral stability pool earning peg
    #: fees, staking carry and Aave lending yield through several sleeves.
    #: Sources:
    #: - https://app.lagoon.finance/vault/9745/0x5f264836ce02496ccf55d7f9aa5cb34e34319db5
    #: - https://app.lagoon.finance/api/vault?chainId=9745&address=0x5f264836ce02496ccf55d7f9aa5cb34e34319db5
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://9summits.io/
    #: - https://fxn.eth.limo/
    #: - https://fxprotocol.gitbook.io/fx-docs/llms.txt
    #: - https://fxprotocol.gitbook.io/fx-docs/f-x-protocol-documentation.md
    #: - https://fxprotocol.gitbook.io/fx-docs/overview/core-products-of-f-x-protocol.md
    #: - https://fxprotocol.gitbook.io/fx-docs/earn-with-f-x/usd-high-and-sustainable-yield.md
    #: - https://fxprotocol.gitbook.io/fx-docs/f-x-protocol-mechanisms/stability-pool.md
    #: - https://fxprotocol.gitbook.io/fx-docs/faq/where-does-the-yield-come-from.md
    #: - https://fxprotocol.gitbook.io/fx-docs/developers/integrating-fxsave.md
    "0x5f264836ce02496ccf55d7f9aa5cb34e34319db5": {
        StrategyTag.carry_trade,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: DACM LIT Strategy, previously Sandbox.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon documents Lighter LLP order-book market making
    #: for perpetuals, earning maker, trading, liquidation and funding returns
    #: on market-neutral inventory.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=42161&address=0x018282d5b510f00dcacb8f4a81c3901d2fc9da51
    #: - https://app.lagoon.finance/vault/42161/0x018282d5b510f00dcacb8f4a81c3901d2fc9da51
    #: - https://dacm.io/
    #: - https://docs.lighter.xyz/
    #: - https://assets.lighter.xyz/whitepaper.pdf
    "0x018282d5b510f00dcacb8f4a81c3901d2fc9da51": {
        StrategyTag.carry_trade,
        StrategyTag.delta_neutral,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_clob,
        StrategyTag.perpetual_futures,
    },
    #: Vault: DAMM Stablecoin Fund.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and DAMM document a market-neutral diversified
    #: fund combining algorithmic AMM market making, overcollateralised lending
    #: and fixed-yield markets, with discretionary allocation between sleeves.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=42161&address=0xe5d6eb448ac5a762c1ebe8cd1692b9cd08025176
    #: - https://app.lagoon.finance/vault/42161/0xe5d6eb448ac5a762c1ebe8cd1692b9cd08025176
    #: - https://dammcap.finance/funds/dammstable/
    #: - https://docs.dammcap.finance/funds/dammstable-arbitrum
    #: - https://docs.dammcap.finance/funds
    #: - https://docs.dammcap.finance/integrations
    #: - https://docs.dammcap.finance/introduction
    #: - https://dammcap.finance/research/dammstable-ecosystem-impact/
    #: - https://dammcap.finance/
    #: - https://docs.dammcap.finance/funds-architecture
    #: - https://docs.dammcap.finance/integrations/dammstable
    "0xe5d6eb448ac5a762c1ebe8cd1692b9cd08025176": {
        StrategyTag.algorithmic_trading,
        StrategyTag.amm,
        StrategyTag.delta_neutral,
        StrategyTag.discretionary_trading,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_amm,
        StrategyTag.multistrategy,
    },
    #: Vault: Avalanche USDC, previously Turtle Avalanche USDC.
    #: Added: 2026-09-28.
    #: Decision material: The exact-address curator proposal and Silo
    #: documentation describe allocation and reallocation among approved
    #: isolated lending markets to earn borrower interest and optimise yield.
    #: Sources:
    #: - https://app.lagoon.finance/vault/43114/0x3048925b3ea5a8c12eecccb8810f5f7544db54af
    #: - https://app.lagoon.finance/api/vault?chainId=43114&address=0x3048925b3ea5a8c12eecccb8810f5f7544db54af
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://gov.silo.finance/t/vault-proposal-turtle-avalanche-usdc/586
    #: - https://app.silo.finance/vaults/avalanche/0xF8C100aB4b2d416609b0Ef18105eBf601b2ec84A
    #: - https://docs.silo.finance/docs/vaults/core-concepts/vaults/
    #: - https://docs.silo.finance/docs/vaults/intro/
    #: - https://docs.silo.finance/docs/users/core-concepts/managed-vaults/
    #: - https://docs.silo.finance/docs/users/using-silo/supply/
    #: - https://docs.silo.finance/docs/vaults/core-concepts/roles/
    #: - https://docs.silo.finance/docs/vaults/manager-guide/managing-your-vault/
    #: - https://9summits.io/
    #: - https://tulipa.capital/
    #: - https://www.turtle.xyz/
    "0x3048925b3ea5a8c12eecccb8810f5f7544db54af": {
        StrategyTag.lending,
        StrategyTag.lending_optimisation,
    },
    #: Vault: USDC Avalanche Core.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Gami document dynamic allocation across
    #: Avalanche lending markets, Spectra fixed-income strategies, and named
    #: Curve, Balancer and Pharaoh liquidity pools.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=43114&address=0xb3a2bcb30c1460d88db18b42a29fae2399952874
    #: - https://app.lagoon.finance/vault/43114/0xb3a2bcb30c1460d88db18b42a29fae2399952874
    #: - https://gamilabs.io/
    #: - https://gamilabs.io/vaults
    "0xb3a2bcb30c1460d88db18b42a29fae2399952874": {
        StrategyTag.amm,
        StrategyTag.lending,
        StrategyTag.lending_optimisation,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: Excellion USDC Vault.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and Excellion document a market-neutral,
    #: actively allocated mix of liquidity provision, leveraged lending and
    #: yield farming across several protocols.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=43114&address=0xb8a14b03900828f863aedd9dd905363863bc31f4
    #: - https://app.lagoon.finance/vault/43114/0xb8a14b03900828f863aedd9dd905363863bc31f4
    #: - https://excellion.finance/
    #: - https://excellion.finance/en
    "0xb8a14b03900828f863aedd9dd905363863bc31f4": {
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: Kamui Stable Vault.
    #: Added: 2026-09-28.
    #: Decision material: Kamui describes Stable as holding tokenised US
    #: Treasury securities and money-market instruments.
    #: Sources:
    #: - https://www.kamui.finance/
    #: - https://www.linkedin.com/posts/kamui-finance_kamui-finance-brings-three-institutional-activity-7508163602609209344-rxYM
    "0xcda323c2df692d989b24ba51d0acca924cf9a344": {
        StrategyTag.money_market_fund,
        StrategyTag.rwa,
    },
    #: Vault: Kamui Balanced Vault.
    #: Added: 2026-09-28.
    #: Decision material: Kamui describes Balanced as diversified across
    #: asset classes. Contemporaneous launch coverage identifies its mandate
    #: as investment-grade credit, including CLOs, receivables finance,
    #: corporate bonds and commercial paper.
    #: Sources:
    #: - https://www.kamui.finance/
    #: - https://www.linkedin.com/posts/kamui-finance_kamui-finance-brings-three-institutional-activity-7508163602609209344-rxYM
    #: - https://newsletter.cryptofunds.watch/p/1b-multi-family-office-launches-systematic-xrp-strategy
    "0xa5ae405242f42c47996a0c6857ff10a77f9bdee6": {
        StrategyTag.multistrategy,
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
    },
    #: Vault: Kamui Boosted Vault.
    #: Added: 2026-09-28.
    #: Decision material: Kamui describes Boosted as combining private-credit
    #: yield with DeFi liquidity. Contemporaneous launch coverage also
    #: identifies fixed-income and reinsurance strategies in its mandate.
    #: Sources:
    #: - https://www.kamui.finance/
    #: - https://www.linkedin.com/posts/kamui-finance_kamui-finance-brings-three-institutional-activity-7508163602609209344-rxYM
    #: - https://newsletter.cryptofunds.watch/p/1b-multi-family-office-launches-systematic-xrp-strategy
    "0x9e0db8f43bb91e2148b0db920e21370525cf3aab": {
        StrategyTag.multistrategy,
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
    },
}


#: Strategy classifications for Lagoon deployments identified by chain and
#: address, including reused addresses. These must take precedence
#: over the address-only table above.
CHAIN_STRATEGY_TAGS: dict[tuple[int, str], set[StrategyTag]] = {
    #: Vault: Murmurr USDC One, previously DeTrade Morpho X-Chain USDC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon documents automated allocation and rotation
    #: across Morpho USDC lending sleeves using yield, collateral-quality,
    #: liquidity and utilisation rules.
    #: Reviewed: 2026-11-10.
    #: Murmurr combines curator expertise with scored allocation in diversified sleeves.
    #: This product invests through those sleeves and fixed-universe mono sleeves;
    #: the team has revised the product-level sleeve targets in its published hard rules.
    #: Sources:
    #: - https://app.lagoon.finance/vault/1/0x7d2C2F54792ad72CB834D298f542145b06B703cb
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x7d2c2f54792ad72cb834d298f542145b06b703cb
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://app.murmurr.xyz/transparency
    #: - https://murmurr.xyz/app/transparency
    #: - https://detrade.fund/
    #: - https://murmurr.xyz/
    #: - https://docs.murmurr.xyz/how-it-works/allocator
    #: - https://docs.murmurr.xyz/how-it-works/hard-rules
    (1, "0x7d2c2f54792ad72cb834d298f542145b06b703cb"): {
        StrategyTag.algorithmic_trading,
        StrategyTag.lending,
        StrategyTag.lending_optimisation,
        StrategyTag.discretionary_trading,
    },
    #: Vault: DeTrade Core ETH.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and DeTrade document lending LST/LRT
    #: collateral, conditional leverage loops, staking carry and distinct nested
    #: stablecoin-yield sleeves including Curve AMM liquidity.
    #: Reviewed: 2026-11-10.
    #: The website links this exact Core product and its opportunity-selection policy
    #: describes "selecting trusted protocols with active incentive campaigns".
    #: Automatic compounding does not remove that investment discretion.
    #: Sources:
    #: - https://app.lagoon.finance/vault/8453/0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://app.detrade.fund/vault/8453/0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523
    #: - https://app.detrade.fund/
    #: - https://detrade.fund/
    #: - https://app.lagoon.finance/vault/8453/0x0a63471da694fc822f5a0a0b65978dda8c8edbe5
    (8453, "0x9b97bfdfe44d1b113ecd4bf2f243ed36aca34523"): {
        StrategyTag.amm,
        StrategyTag.carry_trade,
        StrategyTag.lending,
        StrategyTag.lending_looping,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
        StrategyTag.discretionary_trading,
    },
    #: Vault: TruMarket.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and TruMarket document financing agricultural
    #: trade deals backed by real agricultural products.
    #: Reviewed: 2026-11-10.
    #: TruMarket sources, structures and monitors the agricultural trade deals funded by this
    #: exact vault.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0xbe7db44f4ce20dac83b578b94fd35087f66e9754
    #: - https://app.lagoon.finance/vault/8453/0xbe7db44f4ce20dac83b578b94fd35087f66e9754
    #: - https://www.trumarket.tech/
    #: - https://finance.trumarket.tech/
    #: - https://finance.trumarket.tech/shipments/68839958953ab2c775d51830
    (8453, "0xbe7db44f4ce20dac83b578b94fd35087f66e9754"): {
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
        StrategyTag.rwa_lending,
        StrategyTag.discretionary_trading,
    },
    #: Vault: DeTrade Core EURC.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and DeTrade document supplying EURC, borrowing
    #: USD stablecoins and deploying them into distinct higher-yield lending and
    #: Curve-liquidity sleeves while matching the USD liability exposure.
    #: Reviewed: 2026-11-10.
    #: The website links this exact Core product and its opportunity-selection policy
    #: describes "selecting trusted protocols with active incentive campaigns".
    #: Automatic compounding does not remove that investment discretion.
    #: Sources:
    #: - https://app.lagoon.finance/vault/8453/0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://app.detrade.fund/vault/8453/0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550
    #: - https://app.detrade.fund/
    #: - https://detrade.fund/
    #: - https://app.lagoon.finance/vault/8453/0x0a63471da694fc822f5a0a0b65978dda8c8edbe5
    (8453, "0xd4401d8bea82e4e6c40bb26ae3a04d2fb7ca4550"): {
        StrategyTag.amm,
        StrategyTag.carry_trade,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.lending_looping,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
        StrategyTag.discretionary_trading,
    },
    #: Vault: Flagship cbBTC on Ethereum.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon documents DEX liquidity provision,
    #: money-market lending, fixed-income positions and depeg-redemption
    #: arbitrage within a market-neutral multi-strategy mandate. The same
    #: address on Base belongs to the unrelated 722Capital-USDC vault.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xb09f761cb13baca8ec087ac476647361b6314f98
    #: - https://app.lagoon.finance/vault/1/0xb09f761cb13baca8ec087ac476647361b6314f98
    #: - https://app.lagoon.finance/api/vaults?pageIndex=0&pageSize=1000
    #: - https://9summits.io/
    #: - https://vaults.9summits.io/
    #: - https://tulipa.capital/
    (1, "0xb09f761cb13baca8ec087ac476647361b6314f98"): {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    },
    #: Vault: DeTrade Core USDC on Base.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and DeTrade document lending-market
    #: allocation, opportunistic leverage loops, fixed-yield carry and DEX
    #: liquidity. The same address on Ethereum is the unrelated Usual stUSR vault.
    #: Reviewed: 2026-11-10.
    #: The website links this exact Core product and its opportunity-selection policy
    #: describes "selecting trusted protocols with active incentive campaigns".
    #: Automatic compounding does not remove that investment discretion.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0x8092ca384d44260ea4feaf7457b629b8dc6f88f0
    #: - https://app.lagoon.finance/vault/8453/0x8092ca384d44260ea4feaf7457b629b8dc6f88f0
    #: - https://app.detrade.fund/vault/8453/0x8092ca384d44260ea4feaf7457b629b8dc6f88f0
    #: - https://app.detrade.fund/
    #: - https://detrade.fund/
    #: - https://docs.detrade.fund/vaults
    (8453, "0x8092ca384d44260ea4feaf7457b629b8dc6f88f0"): {
        StrategyTag.amm,
        StrategyTag.carry_trade,
        StrategyTag.lending,
        StrategyTag.lending_looping,
        StrategyTag.lending_optimisation,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
        StrategyTag.discretionary_trading,
    },
    #: Vault: 722Capital-USDC on Base.
    #: Added: 2026-09-28.
    #: Decision material: Lagoon and 722 Capital document delta-neutral basis
    #: trading, lending, leverage loops and liquidity strategies. The same
    #: address on Ethereum belongs to the unrelated Flagship cbBTC vault.
    #: Reviewed: 2026-11-10.
    #: The exact USDC mandate describes the team actively positioning in selected emerging
    #: protocols; its website describes conviction-led farm rotation.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0xb09f761cb13baca8ec087ac476647361b6314f98
    #: - https://app.lagoon.finance/vault/8453/0xB09F761Cb13baCa8eC087Ac476647361b6314F98
    #: - https://722.capital/
    #: - https://www.722.capital/
    #: - https://app.722.capital/v/722-usdc
    #: - https://app.722.capital/vaults
    (8453, "0xb09f761cb13baca8ec087ac476647361b6314f98"): {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.lending_looping,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
        StrategyTag.discretionary_trading,
    },
    #: Vault: Gami D1 (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The NVST mandate explicitly combines lending, liquidity pools, structured yield
    #: and privately negotiated deals; private deals alone do not establish RWA exposure.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x16b85563902556d205f5c2b4485df3d26b153f96
    (1, "0x16b85563902556d205f5c2b4485df3d26b153f96"): {StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: UEB3 (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The documented portfolio diversifies and caps allocations across strategies,
    #: assets, protocols and chains. Treasury yields are a benchmark, not evidence of Treasury holdings.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x23b27310451f2754de34d9c04aa24e8be367124a
    (1, "0x23b27310451f2754de34d9c04aa24e8be367124a"): {StrategyTag.multistrategy},
    #: Vault: Mt Pelerin – USD Senior Strategies (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The portfolio combines senior positions, Pendle PT and LP exposure, and Morpho
    #: lending; the vault is explicitly not itself a tranche.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x2927c6d7f89b5ffdf5ad293b4aa6519d495cb341
    (1, "0x2927c6d7f89b5ffdf5ad293b4aa6519d495cb341"): {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: VOLTA90 MULTI ASSET STRATEGY (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The operator chooses directional positions across traditional and crypto markets;
    #: execution and risk controls use programmed rules on Hyperliquid.
    #: The official operator describes leveraged equities, commodity and index exposure
    #: with cross/isolated margin limits: these are Hyperliquid perpetual positions.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x401550bc1ce4fb0ffdd86015f49142bbaad29ad8
    #: - https://volta90.trade/
    #: - https://volta90.trade/blog/volta90-ethereum-migration
    (1, "0x401550bc1ce4fb0ffdd86015f49142bbaad29ad8"): {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.discretionary_trading, StrategyTag.perpetual_futures},
    #: Vault: Turtle Katana WBTC (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The exact deployment describes money-market lending and DEX liquidity provision
    #: on Katana.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x423b469268b15821107c38d1e1f702877219bc52
    (1, "0x423b469268b15821107c38d1e1f702877219bc52"): {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Emerald USDC Deployement + Yield (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The published allocation holds 40% WBTC, 30% WETH and 30% stablecoins.
    #: Unspecified yield mechanisms do not justify additional tags.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x47b2010f3f0cb5782ad796dccc8d42b0c9062cb7
    (1, "0x47b2010f3f0cb5782ad796dccc8d42b0c9062cb7"): {StrategyTag.directional_trading},
    #: Vault: f(x) Protocol - Leveraged ecosystem vault (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The strategy recursively borrows USDC against fxSAVE or PT-fxSAVE collateral on
    #: Morpho to capture the collateral-yield/borrowing-cost spread.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x4ef75e307f7edbf0462c679192c1ff7e911c389a
    (1, "0x4ef75e307f7edbf0462c679192c1ff7e911c389a"): {StrategyTag.carry_trade, StrategyTag.lending, StrategyTag.lending_looping},
    #: Vault: Gami xBTCY, cbBTC deposits (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The BTCY mandate actively manages secondary liquidity, gauge incentives and vote
    #: allocations. The WBTC deployment explicitly names Curve and Spectra; the cbBTC deployment describes
    #: the same BTCY liquidity ecosystem.
    #: Only liquidity provision is assigned here; AMM venues named by the sister WBTC
    #: product are not assumed for this exact deployment.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x57e6824a8b15b709cefb4ccef644ba1349057e77
    (1, "0x57e6824a8b15b709cefb4ccef644ba1349057e77"): {StrategyTag.liquidity_provider},
    #: Vault: Gami lvlUSD (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The lvlUSD deployment combines money-market lending, fixed-income opportunities
    #: and deepening lvlUSD liquidity.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x59b7942f7d2afd085691ce65c152e0d38d4eff22
    (1, "0x59b7942f7d2afd085691ce65c152e0d38d4eff22"): {StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Autonomous Liquidity Plus (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The deployed code monitors yield farms and automatically enters new stablecoin-
    #: pool opportunities; the description does not identify the lending or market-making mechanism.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x5a97b0b97197299456af841f8605543b13b12ee3
    #: - https://docs.almanak.co/
    (1, "0x5a97b0b97197299456af841f8605543b13b12ee3"): {StrategyTag.algorithmic_trading},
    #: Vault: Flint EUR (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: EURC funds tokenised euro-denominated real-estate bonds and private credit.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x5f9efcc03b95609346798596494cf9dbaf176312
    (1, "0x5f9efcc03b95609346798596494cf9dbaf176312"): {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending},
    #: Vault: Mt Pelerin – HL Carry Strategies (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: Equal-quantity spot longs and perpetual shorts harvest funding on Hyperliquid,
    #: with USDC borrowing scaling the carry.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x6abf7b760a40c4f015803cba3cd8b430e41fba7f
    (1, "0x6abf7b760a40c4f015803cba3cd8b430e41fba7f"): {StrategyTag.arbitrage, StrategyTag.carry_trade, StrategyTag.delta_neutral, StrategyTag.funding_rate_arbitrage, StrategyTag.perpetual_futures},
    #: Vault: Mt Pelerin – USD Funding Strategies (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: Market-neutral spot/perpetual and perpetual/perpetual spreads rotate across
    #: venues; options may construct the synthetic long leg.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x6fd4a2bd1e879803a0e083360dddf764d0bbd2df
    (1, "0x6fd4a2bd1e879803a0e083360dddf764d0bbd2df"): {StrategyTag.arbitrage, StrategyTag.carry_trade, StrategyTag.delta_neutral, StrategyTag.funding_rate_arbitrage, StrategyTag.options, StrategyTag.perpetual_futures},
    #: Vault: Noon tacUSN (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: Noon sUSN yield and ecosystem incentives are combined with Tulipa delta-neutral
    #: leveraged looping.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x7895a046b26cc07272b022a0c9bafc046e6f6396
    (1, "0x7895a046b26cc07272b022a0c9bafc046e6f6396"): {StrategyTag.delta_neutral, StrategyTag.lending, StrategyTag.lending_looping, StrategyTag.multistrategy},
    #: Vault: Usual Invested USD0++ in stUSR (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The Ethereum deployment exchanges USD0++ into stUSR to obtain Resolv basis-
    #: trading yield; it is distinct from the Base DeTrade deployment.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x8092ca384d44260ea4feaf7457b629b8dc6f88f0
    (1, "0x8092ca384d44260ea4feaf7457b629b8dc6f88f0"): {StrategyTag.arbitrage, StrategyTag.carry_trade, StrategyTag.delta_neutral},
    #: Vault: Monarq WBTC Market Neutral Yield (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The exact deployment allocates across lending, fixed-rate DeFi and tokenised
    #: real-world assets; it does not identify a particular RWA credit structure.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x8b21fa55e3afc537de8abb74b2068746c676c828
    (1, "0x8b21fa55e3afc537de8abb74b2068746c676c828"): {StrategyTag.lending, StrategyTag.multistrategy, StrategyTag.rwa},
    #: Vault: Tulipa ETH+ (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: ETHplus is deployed in money markets and DEXes, including conditional looping,
    #: while maintaining the ETHplus mandate.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0x9b1c616f5d2d8efb451b2823a58e789dc370705f
    (1, "0x9b1c616f5d2d8efb451b2823a58e789dc370705f"): {StrategyTag.delta_neutral, StrategyTag.lending, StrategyTag.lending_looping, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Mt Pelerin – USD f(x) Strategies (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: Allocations rotate among fxUSD stability-pool/fxSAVE, Convex-boosted liquidity,
    #: Pendle PT/LP and Morpho markets.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xab122081cbe59388f96e320dae77468f64512232
    (1, "0xab122081cbe59388f96e320dae77468f64512232"): {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Gami xBTCY, WBTC deposits (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The BTCY mandate actively manages secondary liquidity, gauge incentives and vote
    #: allocations. The WBTC deployment explicitly names Curve and Spectra; the cbBTC deployment describes
    #: the same BTCY liquidity ecosystem.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xb34447f729abfd21b3d8997fedcb07e86268a2b3
    (1, "0xb34447f729abfd21b3d8997fedcb07e86268a2b3"): {StrategyTag.amm, StrategyTag.liquidity_provider},
    #: Vault: Turtle Katana WETH (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The exact deployment describes money-market lending and DEX liquidity provision
    #: on Katana.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xbca723c30d55f0915e32019a95aa29ea21fd555c
    (1, "0xbca723c30d55f0915e32019a95aa29ea21fd555c"): {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Usual Invested USD0++ in SYRUP (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: USD0++ is exchanged for USD0 and invested in Syrup USD lending yield; the
    #: description explicitly disclaims direct RWA backing.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xc5a7884709967a51f67d2daca7451fe3b820e2e9
    (1, "0xc5a7884709967a51f67d2daca7451fe3b820e2e9"): {StrategyTag.lending},
    #: Vault: Flint USD (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: USDC funds euro real-estate debt and private credit; an IBKR currency hedge
    #: captures the USD/EUR rate differential.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xd2386d8413c2ec4a761901da14c74a98ebe5054b
    (1, "0xd2386d8413c2ec4a761901da14c74a98ebe5054b"): {StrategyTag.carry_trade, StrategyTag.fx, StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending},
    #: Vault: Bizantine FXRP OmniVault (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The FXRP mandate explicitly combines non-directional lending, liquidity
    #: provision, basis spreads and onchain options.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xd4b21ed00aee22d992ffa75f567d4419c1312fed
    (1, "0xd4b21ed00aee22d992ffa75f567d4419c1312fed"): {StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.options},
    #: Vault: Turtle Katana USDC (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The exact deployment describes money-market lending and DEX liquidity provision
    #: on Katana.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xd56031b6e6860bd41dce2729d1bed21c387b26ce
    (1, "0xd56031b6e6860bd41dce2729d1bed21c387b26ce"): {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Turtle Katana USDT (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The exact deployment describes money-market lending and DEX liquidity provision
    #: on Katana.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xe0dfbe4748ed96350754f1328679bd9647bf9621
    (1, "0xe0dfbe4748ed96350754f1328679bd9647bf9621"): {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Carry trade yield WBTC (chain 1).
    #: Added: 2026-11-10.
    #: Decision material: The deprecated strategy borrows stablecoins against WBTC on Uncap and deploys
    #: them on Extended for yield. Borrowing alone does not establish supplied lending or recursive
    #: looping.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=1&address=0xeff2c1cc0e3bbb6bedc9622a309ed75eab730521
    (1, "0xeff2c1cc0e3bbb6bedc9622a309ed75eab730521"): {StrategyTag.carry_trade},
    #: Vault: DAMM Optimism Fund (chain 10).
    #: Added: 2026-11-10.
    #: Decision material: Proprietary algorithmic market-making models manage Velodrome OP-pair liquidity
    #: while retaining OP price exposure.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=10&address=0x6657a22a3ab82e2f46575423e74ccc1943c8005f
    (10, "0x6657a22a3ab82e2f46575423e74ccc1943c8005f"): {StrategyTag.algorithmic_trading, StrategyTag.amm, StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.market_making_amm},
    #: Vault: MuDigital Tulipa USDC (chain 143).
    #: Added: 2026-11-10.
    #: Decision material: Capital is divided among DEX liquidity, lending and minting AZND/muBOND; token
    #: names alone do not establish an RWA subtype.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=143&address=0x0da39b740834090c146dc48357f6a435a1bb33b3
    (143, "0x0da39b740834090c146dc48357f6a435a1bb33b3"): {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: S&P500 Hyperliquid Synth, deprecated deployment (chain 999).
    #: Added: 2026-11-10.
    #: Decision material: The deprecated S&P 500 deployment automatically rebalances approximately 1x
    #: index-perpetual exposure and idle Hyperliquid Earn lending. Account margin settings do not make
    #: overall exposure leveraged.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=999&address=0x2229eaf48774e6c49d8a45150b6110239f24e476
    (999, "0x2229eaf48774e6c49d8a45150b6110239f24e476"): {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.index, StrategyTag.lending, StrategyTag.multistrategy, StrategyTag.perpetual_futures},
    #: Vault: DeTrade Applefarm USDC (chain 8453).
    #: Added: 2026-11-10.
    #: Decision material: USDC/USDT capital is deployed to Curve on Etherlink to earn fees and Merkl
    #: ecosystem incentives.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0x0a63471da694fc822f5a0a0b65978dda8c8edbe5
    (8453, "0x0a63471da694fc822f5a0a0b65978dda8c8edbe5"): {StrategyTag.amm, StrategyTag.liquidity_provider},
    #: Vault: Tailwind ETH (chain 8453).
    #: Added: 2026-11-10.
    #: Decision material: The ETH-denominated portfolio rotates between ETH and BTC, with idle ETH
    #: optionally supplied to money markets. Systematic management alone does not prove programmed
    #: execution.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0x7dd04fa502c1ae6dc81762bea2177aa38398ecee
    (8453, "0x7dd04fa502c1ae6dc81762bea2177aa38398ecee"): {StrategyTag.directional_trading, StrategyTag.lending, StrategyTag.multistrategy},
    #: Vault: BTC DCA - Regime Vault (chain 8453).
    #: Added: 2026-11-10.
    #: Decision material: A proprietary onchain-data model selects weekly cbBTC exposure; humans approve
    #: execution and idle USDC earns Aave V3 lending yield.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0x87b433543660001cf64ad50afc51cc2a27e73e1d
    #: - https://regimevault.com/strategy
    (8453, "0x87b433543660001cf64ad50afc51cc2a27e73e1d"): {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.lending, StrategyTag.multistrategy},
    #: Vault: MSA - Portefeuille Défensif (chain 8453).
    #: Added: 2026-11-10.
    #: Decision material: The MSA portfolio actively reallocates BTC, ETH and selected volatile assets,
    #: taking profits into USDC yield while awaiting new opportunities.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=8453&address=0xb725f2277f3f62ad017a6dc418433b326a9f6334
    (8453, "0xb725f2277f3f62ad017a6dc418433b326a9f6334"): {StrategyTag.directional_trading, StrategyTag.discretionary_trading, StrategyTag.multistrategy},
    #: Vault: TID Capital Plasma Yield (chain 9745).
    #: Added: 2026-11-10.
    #: Decision material: TID combines leveraged farming, liquidity provision and lending, balancing liquid
    #: allocations for withdrawals. Leveraged farming alone does not establish recursive lending loops.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=9745&address=0x0c1c83c9c203f6920726a0045ff1ea95e9698d0a
    (9745, "0x0c1c83c9c203f6920726a0045ff1ea95e9698d0a"): {StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Cassa Yen Carry & Divergence Trade (chain 9745).
    #: Added: 2026-11-10.
    #: Decision material: The strategy combines JPY-funded carry with tactical cross-asset divergence
    #: trades around Japanese policy events. Systematic does not by itself establish automation.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=9745&address=0xff5ef6464896516c6ccf60da9e199680929fb01c
    (9745, "0xff5ef6464896516c6ccf60da9e199680929fb01c"): {StrategyTag.carry_trade, StrategyTag.directional_trading, StrategyTag.fx, StrategyTag.multistrategy},
    #: Vault: TID Capital Arbitrum Yield (chain 42161).
    #: Added: 2026-11-10.
    #: Decision material: TID combines leveraged farming, liquidity provision and lending, balancing liquid
    #: allocations for withdrawals. Leveraged farming alone does not establish recursive lending loops.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=42161&address=0x647b5ad322c9173a8d3bdf8041b8bddfc456ddc4
    (42161, "0x647b5ad322c9173a8d3bdf8041b8bddfc456ddc4"): {StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: v2 sky vault (chain 42161).
    #: Added: 2026-11-10.
    #: Decision material: The mandate explicitly deploys into Huma and USD.AI real-world payment/GPU
    #: financing and Aave/Morpho crypto lending; optional quantitative-manager examples do not prove
    #: specific trading methods.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=42161&address=0x7197b77e717d837c00204cb21144692b2dedaa12
    (42161, "0x7197b77e717d837c00204cb21144692b2dedaa12"): {StrategyTag.lending, StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending},
    #: Vault: Nutria (chain 42161).
    #: Added: 2026-11-10.
    #: Decision material: The discretionary mandate explicitly lists options, directional positions,
    #: prediction markets and event-driven opportunities.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=42161&address=0x7c43710b240d1810d9799001ec0964512ffdb077
    (42161, "0x7c43710b240d1810d9799001ec0964512ffdb077"): {StrategyTag.directional_trading, StrategyTag.discretionary_trading, StrategyTag.multistrategy, StrategyTag.options},
    #: Vault: Hemi USDC (chain 43111).
    #: Added: 2026-11-10.
    #: Decision material: The Hemi/Gami mandate combines SushiSwap liquidity, Feather money-market lending
    #: and cross-chain Morpho/Curve/Spectra allocations.
    #: Sources:
    #: - https://app.lagoon.finance/api/vault?chainId=43111&address=0x1e32c96757c07775ca4fc796c4f4311722eaf35e
    (43111, "0x1e32c96757c07775ca4fc796c4f4311722eaf35e"): {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: VOLTA90 legacy Arbitrum.
    #: Added: 2026-11-10.
    #: Decision material: The official migration announcement identifies both exact
    #: contracts and confirms the same directional Hyperliquid trading book. The
    #: Arbitrum share vault closed on 7 September 2026; this is a historical classification.
    #: The official operator describes leveraged equities, commodity and index exposure
    #: with cross/isolated margin limits: these are Hyperliquid perpetual positions.
    #: Sources:
    #: - https://volta90.trade/blog/volta90-ethereum-migration
    #: - https://volta90.trade/
    (42161, "0x9e6de1f581d63129af453fe1ea7253b280b4dca8"): {StrategyTag.directional_trading, StrategyTag.discretionary_trading, StrategyTag.algorithmic_trading, StrategyTag.perpetual_futures},
}


def lookup_lagoon_strategy_tags(chain_id: int, address: str) -> set[StrategyTag] | None:
    """Look up one Lagoon deployment's maintained strategy categories.

    Legacy classifications are address-scoped. New entries are scoped by
    chain and address, and take precedence over the legacy table so reused
    addresses can carry distinct mandates on different chains.
    A copy is returned so scan consumers cannot mutate the maintained tables.

    :param chain_id:
        EVM chain identifier.
    :param address:
        Lagoon vault contract address.
    :return:
        Maintained strategy categories, or ``None`` when exact-address
        evidence was insufficient.
    """

    normalised_address = address.lower()
    tags = CHAIN_STRATEGY_TAGS.get((chain_id, normalised_address))
    if tags is None:
        tags = STRATEGY_TAGS.get(normalised_address)
    return set(tags) if tags is not None else None
