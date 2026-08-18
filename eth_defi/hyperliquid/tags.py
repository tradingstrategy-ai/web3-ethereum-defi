"""Maintained strategy classifications for Hyperliquid native vaults."""

from eth_defi.vault.strategy_tag import StrategyTag, combine_strategy_tags

#: Hyperliquid native vaults trade perpetual futures by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.perpetual_futures})

#: Address-specific strategy classifications maintained in addition to the
#: native perpetual-futures default. Addresses are lowercase.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: pmalt.
    #: Added: 2026-08-17.
    #: Decision material: Maintainer classification marks pmalt's former
    #: Hyperliquid strategy as algorithmic trading and pair trading.
    #: Sources:
    #: - eth_defi/data/feeds/curators/pmalt.yaml
    #: - https://app.hyperliquid.xyz/vaults/0x4dec0a851849056e259128464ef28ce78afa27f6
    #: - https://app.lighter.xyz/public-pools/281474976552918
    "0x4dec0a851849056e259128464ef28ce78afa27f6": {
        StrategyTag.algorithmic_trading,
        StrategyTag.pair_trading,
    },
    #: Vault: Hyperliquidity Provider (HLP).
    #: Added: 2026-08-17.
    #: Decision material: Hyperliquid describes HLP as a community-owned vault
    #: that provides liquidity through multiple market-making strategies.
    #: Sources:
    #: - https://app.hyperliquid.xyz/vaults/0xdfc24b077bc1425ad1dea75bcb6f8158e10df303
    #: - https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults
    "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303": {
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
    },
    #: Vault: Growi HF.
    #: Added: 2026-08-17.
    #: Decision material: The published vault description explicitly says it
    #: employs a quantitative, mean-reversion strategy and is fully automated.
    #: Sources:
    #: - https://app.hyperliquid.xyz/vaults/0x1e37a337ed460039d1b15bd3bc489de789768d5e
    #: - https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
    "0x1e37a337ed460039d1b15bd3bc489de789768d5e": {StrategyTag.mean_reversion},
    #: Vault: Gen Wealth Algo.
    #: Added: 2026-08-17.
    #: Decision material: The Hyperliquid vault description explicitly says
    #: "Trend-following algo trading majors & alts".
    #: Sources:
    #: - https://app.hyperliquid.xyz/vaults/0xdda7f4805dfdf145a74cd68992d90780f73cf6c7
    #: - https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
    "0xdda7f4805dfdf145a74cd68992d90780f73cf6c7": {StrategyTag.trend_following},
    #: Vault: Opportunistic Fund 1.
    #: Added: 2026-08-17.
    #: Decision material: The Hyperliquid vault description explicitly says
    #: "Discretionary trend following with a contrarian twist".
    #: Sources:
    #: - https://app.hyperliquid.xyz/vaults/0xfb7b73ff7c93f5552541de37454ffa0f8b76462a
    #: - https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
    "0xfb7b73ff7c93f5552541de37454ffa0f8b76462a": {StrategyTag.trend_following},
    #: Vault: S&P MA375 Alpha Vault.
    #: Added: 2026-08-17.
    #: Decision material: The Hyperliquid vault description explicitly says
    #: "MA375 Trend-Following" and describes automated execution.
    #: Sources:
    #: - https://app.hyperliquid.xyz/vaults/0x1b03878805333a0e13d7eea4abdfa2d97977c448
    #: - https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
    "0x1b03878805333a0e13d7eea4abdfa2d97977c448": {StrategyTag.trend_following},
    #: Vault: Super Trend Following.
    #: Added: 2026-08-17.
    #: Decision material: The Hyperliquid vault description explicitly says
    #: "Trend-Following Strategy".
    #: Sources:
    #: - https://app.hyperliquid.xyz/vaults/0x6b13de56131bfa2256e2dcd64b67c38272c72318
    #: - https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
    "0x6b13de56131bfa2256e2dcd64b67c38272c72318": {StrategyTag.trend_following},
    #: Vault: Tera Liquid.
    #: Added: 2026-08-17.
    #: Decision material: The Hyperliquid vault description explicitly says it
    #: uses "systematic trend-following strategies" for major cryptocurrencies.
    #: Sources:
    #: - https://app.hyperliquid.xyz/vaults/0x15a141990fc6591838646467273c41c92999772f
    #: - https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
    "0x15a141990fc6591838646467273c41c92999772f": {StrategyTag.trend_following},
    #: Vault: Wilkins Capital.
    #: Added: 2026-08-17.
    #: Decision material: The Hyperliquid vault description explicitly says
    #: it is a "Systematic trend-following portfolio".
    #: Sources:
    #: - https://app.hyperliquid.xyz/vaults/0x5048900eb10b569e77f515efe85f8da5cfd5fb3a
    #: - https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
    "0x5048900eb10b569e77f515efe85f8da5cfd5fb3a": {StrategyTag.trend_following},
}


def get_strategy_tags(address: str) -> set[StrategyTag]:
    """Get maintained strategy tags for a Hyperliquid native vault.

    :param address:
        Lowercase-compatible Hyperliquid vault address.
    :return:
        New tag set containing the native perpetual-futures default and any
        address-specific classifications.
    """
    return combine_strategy_tags(DEFAULT_STRATEGY_TAGS, STRATEGY_TAGS, address)
