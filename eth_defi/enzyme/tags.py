"""Maintained strategy classifications for Enzyme vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: OpalAccess - LiquidStone 2.
    #: Added: 2026-08-23.
    #: Decision material: BlackOpal describes LiquidStone II as a hybrid
    #: strategy investing in short-duration Brazilian credit-card receivables
    #: alongside an onchain liquid sleeve. This is real-world credit exposure
    #: implemented through more than one investment sleeve.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x1B6d1EDf854CA5d8A7c32DDb79C24B117eBc6433?network=base
    #: - https://www.blackopal.finance/
    "0x1b6d1edf854ca5d8a7c32ddb79c24b117ebc6433": {
        StrategyTag.multistrategy,
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
    },
    #: Vault: ACC Metaverse Fund.
    #: Added: 2026-08-24.
    #: Decision material: The manager describes an actively managed diversified
    #: crypto-asset fund.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xd618b03c7a1c0f3248ae049954d69e8d96a142c0?network=ethereum
    "0xd618b03c7a1c0f3248ae049954d69e8d96a142c0": {StrategyTag.discretionary_trading},
    #: Vault: CASΦBTC.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents an adaptive macro-quantitative
    #: Bitcoin engine that establishes structured long-short positions.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xd89551d350532d001ad3105968fecb24b1c3cec8?network=ethereum
    "0xd89551d350532d001ad3105968fecb24b1c3cec8": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading},
    #: Vault: CASΦNexus.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents a macro-quantitative system
    #: establishing long-short positions across crypto tokens.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x2ef704ea3a9976ca32b6ab06584d31e002d6bd7c?network=ethereum
    "0x2ef704ea3a9976ca32b6ab06584d31e002d6bd7c": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading},
    #: Vault: Evo AlphaPulse.
    #: Added: 2026-08-24.
    #: Decision material: The manager describes a complexity-driven quantitative
    #: engine for multi-asset allocation.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xd74216f41c849c0b06272ed3b15a62b9c1d02d0c?network=ethereum
    "0xd74216f41c849c0b06272ed3b15a62b9c1d02d0c": {StrategyTag.algorithmic_trading},
    #: Vault: Gemach LP.
    #: Added: 2026-08-24.
    #: Decision material: The manager explicitly states that the vault supplies
    #: liquidity to DeFi protocols for rewards.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x740cbfefb9ca9c1c99d95b711e959dd960f8bdb6?network=ethereum
    "0x740cbfefb9ca9c1c99d95b711e959dd960f8bdb6": {StrategyTag.liquidity_provider},
    #: Vault: HEXADEFI CAPITAL.
    #: Added: 2026-08-24.
    #: Decision material: The manager combines liquidity-pool activity with
    #: actively managed blue-chip swing trading.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x7dbfc77b308356a5d90c586c7e3f0e089b8e37ec?network=ethereum
    "0x7dbfc77b308356a5d90c586c7e3f0e089b8e37ec": {StrategyTag.directional_trading, StrategyTag.discretionary_trading, StrategyTag.liquidity_provider},
    #: Vault: Niska Capital Fund I (Ethereum).
    #: Added: 2026-08-24.
    #: Decision material: The manager documents long-term holdings, momentum
    #: investments, and DeFi/staking as separate investment baskets.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x308b02c6a4e346f1f6fb5c7d79d0de2c4f3abb82?network=ethereum
    "0x308b02c6a4e346f1f6fb5c7d79d0de2c4f3abb82": {StrategyTag.directional_trading, StrategyTag.multistrategy, StrategyTag.trend_following},
    #: Vault: Taurus.
    #: Added: 2026-08-24.
    #: Decision material: The manager describes a portfolio of multiple
    #: algorithmic momentum systems across BTC, ETH, and SOL.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x4819ac09e4619748b1cdf657283a948731fa6ab6?network=ethereum
    "0x4819ac09e4619748b1cdf657283a948731fa6ab6": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.multistrategy, StrategyTag.trend_following},
    #: Vault: Turing Fund.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents a dynamic BTC/ETH allocation
    #: with a PAXG hedge.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x57e9a5ff045dfc6215a2a6a793603db8f3838bd9?network=ethereum
    "0x57e9a5ff045dfc6215a2a6a793603db8f3838bd9": {StrategyTag.directional_trading},
    #: Vault: Walled Fund ETH.
    #: Added: 2026-08-24.
    #: Decision material: The manager combines covered-call writing with
    #: low-risk DeFi yield strategies around ETH.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x15ce0ce914f97ed7b0e3fe4da0c696002b3d2964?network=ethereum
    "0x15ce0ce914f97ed7b0e3fe4da0c696002b3d2964": {StrategyTag.multistrategy},
    #: Vault: Walled Fund II.
    #: Added: 2026-08-24.
    #: Decision material: The manager selects undervalued digital assets for a
    #: value-weighted portfolio and supplements it with DeFi yield.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x952a00d5fa79af70b28fed5725a86d60b17a3297?network=ethereum
    "0x952a00d5fa79af70b28fed5725a86d60b17a3297": {StrategyTag.directional_trading},
    #: Vault: Walled Fund Liquid.
    #: Added: 2026-08-24.
    #: Decision material: The manager describes a long-only liquid portfolio of
    #: BTC, L1/L2, and dApp assets alongside DeFi yield strategies.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xd22c061a1ee043c75f642fcfb643f9541997fb85?network=ethereum
    "0xd22c061a1ee043c75f642fcfb643f9541997fb85": {StrategyTag.directional_trading},
    #: Vault: seed USDN.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents an sUSDN/SDEX liquidity pool
    #: and liquidity farming campaign.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xf67e2dc041b8a3c39d066037d29f500757b1e886?network=ethereum
    "0xf67e2dc041b8a3c39d066037d29f500757b1e886": {StrategyTag.liquidity_provider},
    #: Vault: ATHENEA - II.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents a long-short combination of
    #: lending, AMM-pool, and other yield strategies.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x1b8cafe378c882446756b2bf49484cf26a648224?network=polygon
    "0x1b8cafe378c882446756b2bf49484cf26a648224": {StrategyTag.amm, StrategyTag.directional_trading, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: Artemis Trust.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents yield farming through liquidity
    #: pools, staking, and DeFi strategies.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x62cd97c6900d07a72eb318fdd0dff462b4a3d7e8?network=polygon
    "0x62cd97c6900d07a72eb318fdd0dff462b4a3d7e8": {StrategyTag.liquidity_provider},
    #: Vault: DEGEN II.
    #: Added: 2026-08-24.
    #: Decision material: The manager explicitly documents MATIC/USDC spot
    #: breakout trading.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x8b6a97d4e4f16f79d8a59b36dd57f33ac3b686bd?network=polygon
    "0x8b6a97d4e4f16f79d8a59b36dd57f33ac3b686bd": {StrategyTag.directional_trading},
    #: Vault: DeFi COINoisseurs.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents Uniswap V3 liquidity with delta
    #: hedging and mean-reversion tactics.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x4134d87c87df974577c580561b4776c2ab70cb43?network=polygon
    "0x4134d87c87df974577c580561b4776c2ab70cb43": {StrategyTag.amm, StrategyTag.delta_neutral, StrategyTag.liquidity_provider, StrategyTag.mean_reversion},
    #: Vault: DeFi Stable Return.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents Uniswap V3 liquidity structured
    #: with delta hedging and mean-reversion tactics.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x3cb4be1ff30cba586d339d16d010295d64794c78?network=polygon
    "0x3cb4be1ff30cba586d339d16d010295d64794c78": {StrategyTag.amm, StrategyTag.delta_neutral, StrategyTag.liquidity_provider, StrategyTag.mean_reversion},
    #: Vault: Ewpple DeFi Crypto Index Fund.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents a market-cap weighted MegaCap
    #: and DeFi-token index portfolio.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xc98e070cc35b98a0f8ba7579a68a1796b014c0b9?network=polygon
    "0xc98e070cc35b98a0f8ba7579a68a1796b014c0b9": {StrategyTag.index},
    #: Vault: VENUS FUND.
    #: Added: 2026-08-24.
    #: Decision material: The manager describes a market-cap weighted crypto
    #: index that seeks yield through lending and liquidity provision.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xe9ae2ec22dcd2a8242a80ecf7aaf1a2042ff07dd?network=polygon
    "0xe9ae2ec22dcd2a8242a80ecf7aaf1a2042ff07dd": {StrategyTag.index, StrategyTag.lending, StrategyTag.liquidity_provider},
    #: Vault: StarzFi Smart Savings Vault.
    #: Added: 2026-08-24.
    #: Decision material: The manager combines lending, swapping, and liquidity
    #: provision on Aave and Aerodrome.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x80dc1c8ad380c8ce9f45c94422b70edc52aa8804?network=base
    "0x80dc1c8ad380c8ce9f45c94422b70edc52aa8804": {StrategyTag.amm, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
    #: Vault: StarzFi Growth Reserve.
    #: Added: 2026-08-24.
    #: Decision material: The manager describes long-term BTC/ETH exposure with
    #: optional ETH lending for yield.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x7c05c4e1a0c35ff7a4b87302029af124a42375cb?network=base
    "0x7c05c4e1a0c35ff7a4b87302029af124a42375cb": {StrategyTag.directional_trading, StrategyTag.lending},
    #: Vault: BTC ALPHA.
    #: Added: 2026-08-24.
    #: Decision material: The manager combines discretionary long-short GMX V2
    #: perpetual trading with concentrated Uniswap V3 liquidity.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x6b303b674b65ca0e24fed4c47dc2474f238eb2cb?network=arbitrum
    "0x6b303b674b65ca0e24fed4c47dc2474f238eb2cb": {StrategyTag.amm, StrategyTag.directional_trading, StrategyTag.discretionary_trading, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.perpetual_futures},
    #: Vault: CRYPTO ALPHA.
    #: Added: 2026-08-24.
    #: Decision material: The manager combines discretionary long-short GMX V2
    #: perpetual trading with concentrated Uniswap V3 liquidity.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x25ec0f6b587c9ccfa870255656fa107c9476aeac?network=arbitrum
    "0x25ec0f6b587c9ccfa870255656fa107c9476aeac": {StrategyTag.amm, StrategyTag.directional_trading, StrategyTag.discretionary_trading, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.perpetual_futures},
    #: Vault: ETH ALPHA.
    #: Added: 2026-08-24.
    #: Decision material: The manager combines discretionary long-short GMX V2
    #: perpetual trading with concentrated Uniswap V3 liquidity.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x69afa06d013f7baef04143d3c2e57236c9448978?network=arbitrum
    "0x69afa06d013f7baef04143d3c2e57236c9448978": {StrategyTag.amm, StrategyTag.directional_trading, StrategyTag.discretionary_trading, StrategyTag.liquidity_provider, StrategyTag.multistrategy, StrategyTag.perpetual_futures},
    #: Vault: ETH and BTC long-term momentum.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents an automated RSI momentum
    #: strategy with excess cash lent through Aave V3.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x798a16fabce53c5a5ceffed963ad79ccd511b5e6?network=arbitrum
    "0x798a16fabce53c5a5ceffed963ad79ccd511b5e6": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.lending, StrategyTag.trend_following},
    #: Vault: Gemini.
    #: Added: 2026-08-24.
    #: Decision material: The manager allocates equally across its separate BTC,
    #: ETH, and SOL Gemini strategies.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0x11d41452fcd89c3622f20e680ebbec8587483a63?network=arbitrum
    "0x11d41452fcd89c3622f20e680ebbec8587483a63": {StrategyTag.multistrategy},
    #: Vault: Gemini BTC.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents a fully automated long-short
    #: Bitcoin swing strategy using systematic trend-following rules.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xd065f37a0ea7f277bf36d93043d20bfb58b93761?network=arbitrum
    "0xd065f37a0ea7f277bf36d93043d20bfb58b93761": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    #: Vault: Gemini ETH.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents an automated long-short Ethereum
    #: strategy with mechanical trend-following rules.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xfde46c2e43d54bbb94eb21452aac711f9b9d8e0e?network=arbitrum
    "0xfde46c2e43d54bbb94eb21452aac711f9b9d8e0e": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    #: Vault: Gemini SOL.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents a fully automated Solana
    #: long-short strategy with mechanical trend-following rules.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xc64197bf72d0ead4bd3563c4dab23b849848268c?network=arbitrum
    "0xc64197bf72d0ead4bd3563c4dab23b849848268c": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    #: Vault: Leo BTC.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents an automated long-only Bitcoin
    #: strategy that enters sustained bullish trends.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xba6f18dd30f5048177bbb32122d49f91f0909844?network=arbitrum
    "0xba6f18dd30f5048177bbb32122d49f91f0909844": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    #: Vault: Leo BTC Bull Run.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents tokenised PineScript trading
    #: signals with onchain trend-following execution.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xb99463e453e2a7ce9fcea6df0bf6991cffef13d1?network=arbitrum
    "0xb99463e453e2a7ce9fcea6df0bf6991cffef13d1": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    #: Vault: Leo ETH.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents an automated long-only Ethereum
    #: strategy that enters sustained bullish trends.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xc0ba7e66631106f5b8e5716b50bd167431b7f6ca?network=arbitrum
    "0xc0ba7e66631106f5b8e5716b50bd167431b7f6ca": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    #: Vault: Leo SOL.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents an automated long-only Solana
    #: strategy that enters major upside trends.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xf11ec3070a2288f6a768364ebf8382ba36b9425b?network=arbitrum
    "0xf11ec3070a2288f6a768364ebf8382ba36b9425b": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    #: Vault: Scorpio III.
    #: Added: 2026-08-24.
    #: Decision material: The manager states that the discontinued strategy is
    #: temporarily running the Leo BTC Bull Run system.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xb0960326a94c33e5fdc302832550f8f82c3d31d4?network=arbitrum
    "0xb0960326a94c33e5fdc302832550f8f82c3d31d4": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    #: Vault: Scorpio IV.
    #: Added: 2026-08-24.
    #: Decision material: The manager states that the discontinued strategy is
    #: temporarily running the Leo BTC Bull Run system.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xe8ce6ce5b39875321422a67ac5324e5990966c96?network=arbitrum
    "0xe8ce6ce5b39875321422a67ac5324e5990966c96": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    #: Vault: Scorpio V.
    #: Added: 2026-08-24.
    #: Decision material: The manager documents a fully automated leveraged
    #: Bitcoin strategy using predefined trend-following rules.
    #: Sources:
    #: - https://app.enzyme.finance/vault/0xd2c89dc816d0d2e9a52ab5124c66a2a731a74a7d?network=arbitrum
    "0xd2c89dc816d0d2e9a52ab5124c66a2a731a74a7d": {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
}


def get_strategy_tags(address: HexAddress) -> set[StrategyTag] | None:
    """Return the maintained strategy tags for an Enzyme shares address.

    Enzyme Blue VaultProxy and Onyx Shares deployments both use their share
    token as the vault identity. The address mapping is therefore shared by
    the two adapters, while retaining the ``None`` result that distinguishes
    a vault with no researched strategy classification from an empty tag set.

    :param address:
        Canonical Enzyme Blue VaultProxy or Onyx Shares address.
    :return:
        A mutable copy of the researched strategy tags, or ``None`` if the
        address has no documented classification.
    """

    return lookup_strategy_tags(STRATEGY_TAGS, address)
