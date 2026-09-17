"""Maintained strategy classifications for YieldBasis markets."""

from eth_defi.vault.strategy_tag import StrategyTag

#: All reviewed products supply both sides of a Curve AMM through YieldBasis'
#: LEVAMM. The borrowed crvUSD is financing for the LP position, not evidence
#: that the product is a lending or stable-yield vault.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: YieldBasis WBTC market 7.
    #: Added: 2026-08-27.
    #: Decision material: the product supplies WBTC and borrowed crvUSD to an
    #: AMM, making the LT an automated market-making liquidity-provider share.
    #: Sources: https://yieldbasis.com/earn and
    #: https://docs.yieldbasis.com/user/overview/how-yieldbasis-works.
    "0x651d4b8168488fa163d85304662e8278d4c55baa": {StrategyTag.market_making, StrategyTag.market_making_amm, StrategyTag.liquidity_provider, StrategyTag.amm},
    #: Vault: YieldBasis cbBTC market 8.
    #: Added: 2026-08-27.
    #: Decision material: the product supplies cbBTC and borrowed crvUSD to an
    #: AMM, making the LT an automated market-making liquidity-provider share.
    #: Sources: https://yieldbasis.com/earn and
    #: https://docs.yieldbasis.com/user/overview/how-yieldbasis-works.
    "0x722fc3640ba007c3e9867ccdb0dca59f2e2f29f9": {StrategyTag.market_making, StrategyTag.market_making_amm, StrategyTag.liquidity_provider, StrategyTag.amm},
    #: Vault: YieldBasis tBTC market 9.
    #: Added: 2026-08-27.
    #: Decision material: the product supplies tBTC and borrowed crvUSD to an
    #: AMM, making the LT an automated market-making liquidity-provider share.
    #: Sources: https://yieldbasis.com/earn and
    #: https://docs.yieldbasis.com/user/overview/how-yieldbasis-works.
    "0x771f7290428d830ecd41e980745c327e507823ec": {StrategyTag.market_making, StrategyTag.market_making_amm, StrategyTag.liquidity_provider, StrategyTag.amm},
    #: Vault: YieldBasis WETH market 10.
    #: Added: 2026-08-27.
    #: Decision material: the product supplies WETH and borrowed crvUSD to an
    #: AMM, making the LT an automated market-making liquidity-provider share.
    #: Sources: https://yieldbasis.com/earn and
    #: https://docs.yieldbasis.com/user/overview/how-yieldbasis-works.
    "0x2b9c9f3bdceb5d8e36a4704f08a78fca53343cea": {StrategyTag.market_making, StrategyTag.market_making_amm, StrategyTag.liquidity_provider, StrategyTag.amm},
}
