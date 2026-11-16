"""Maintained strategy tags for reviewed Neutral Trade native accounts.

Solana identity conversion preserves base58 case. Unreviewed vaults have no
classification; neither curator identity nor protocol name implies a strategy.
"""

from eth_defi.neutral_trade.constants import make_solana_vault_address
from eth_defi.vault.strategy_tag import StrategyTag

#: Provider-neutral identities used by native metadata and numbered migrations.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Options Market Making. Added: 2026-11-12.
    #: Decision material: Automated BTC option sales and perpetual hedges on OKX.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/options-market-making
    make_solana_vault_address("3vZKAcd74bzwNYZmsJndYExkGj6ABQwrUoiMtdNfzLZe"): {StrategyTag.algorithmic_trading, StrategyTag.options, StrategyTag.perpetual_futures, StrategyTag.delta_neutral},
    #: Vault: Mid-frequency Market Making. Added: 2026-11-12.
    #: Decision material: Software quotes both sides of the Binance ETHUSDT perpetual order book.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/mid-frequency-market-making
    make_solana_vault_address("46gSEJLNTdDFq7DPjH8RbcTmG1gyw4JCKCr1XfuAfYvh"): {StrategyTag.algorithmic_trading, StrategyTag.market_making, StrategyTag.market_making_clob, StrategyTag.perpetual_futures},
    #: Vault: Multi-Asset Volatility Arbitrage. Added: 2026-11-12.
    #: Decision material: Systematic relative-value volatility trades in listed equity, fixed-income and VIX instruments; no inferred crypto-perpetual exposure.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/rwa/multi-asset-volatility-arbitrage
    make_solana_vault_address("4QBTzqUbn1crLg2PDUGt8abJfqHjeuZkjNLjCCit69ui"): {StrategyTag.algorithmic_trading, StrategyTag.arbitrage, StrategyTag.rwa},
    #: Vault: CTA - Momentum LongShort. Added: 2026-11-12.
    #: Decision material: Systematic directional momentum signals and hourly rebalancing; instrument type is not specified.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/directional/cta-momentum-longshort
    make_solana_vault_address("Aea2VZfX6hw758cxx2WaGm7kqmdVjhbYsLfde5chVG1W"): {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following},
    #: Vault: HFT Multi-factor. Added: 2026-11-12.
    #: Decision material: Automated maker-dominant perpetual trading, with momentum and mean-reversion signals.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/hft-multi-factor
    make_solana_vault_address("C68A4mAhA9EE4rWq9HmFnq3SPcbNmst6qiBWcna5VDHy"): {StrategyTag.algorithmic_trading, StrategyTag.market_making, StrategyTag.market_making_clob, StrategyTag.perpetual_futures, StrategyTag.mean_reversion, StrategyTag.trend_following},
    #: Vault: Delta Neutral Arbitrage. Added: 2026-11-12.
    #: Decision material: Offsetting exchange trades, funding/basis arbitrage and DeFi market making; no disclosed human allocation decisions.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/delta-neutral-arbitrage
    make_solana_vault_address("De47QBuMP7xukYBek5r4ScZF4HEk1kHW34ymKASz3DLt"): {StrategyTag.arbitrage, StrategyTag.funding_rate_arbitrage, StrategyTag.market_making, StrategyTag.perpetual_futures, StrategyTag.delta_neutral},
    #: Vault: HFT Market Making. Added: 2026-11-12.
    #: Decision material: Automated order-book quotes in Binance ETHUSDT perpetuals.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/hft-market-making
    make_solana_vault_address("DzmixPGgru86YkQY4LSEuM1Sfz7ZFPc1wHwSm4fejXbJ"): {StrategyTag.algorithmic_trading, StrategyTag.market_making, StrategyTag.market_making_clob, StrategyTag.perpetual_futures},
    #: Vault: CTA - Systematic Alpha. Added: 2026-11-12.
    #: Decision material: Long-only systematic US equity trend-following and mean reversion, with automatic capital deployment.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/directional/systematic-alpha
    make_solana_vault_address("Ec6uSz5EsffwfXp43pvpz4rf9ttJRFKES4ZSVX7gVVkQ"): {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following, StrategyTag.mean_reversion, StrategyTag.rwa},
    #: Vault: CTA - Adaptive Alpha. Added: 2026-11-12.
    #: Decision material: Automated directional crypto derivatives models switch between trend and mean reversion; derivatives are not explicitly perpetuals.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/directional/cta-adaptive-alpha
    make_solana_vault_address("Eo3m78EQcHnwMyHtxaiz1vw4nwHa85RuGE8jLsrELhxj"): {StrategyTag.algorithmic_trading, StrategyTag.directional_trading, StrategyTag.trend_following, StrategyTag.mean_reversion},
    #: Vault: CTA - Longshort Alpha. Added: 2026-11-12.
    #: Decision material: Systematic long-short perpetuals, intraday momentum, funding collection and mean-reversion signals.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/directional/cta-longshort-alpha
    make_solana_vault_address("G5aMxQTbGWMnYycpfjHpD7Y1muoKBwaB1HtCdpUUcQZp"): {StrategyTag.algorithmic_trading, StrategyTag.perpetual_futures, StrategyTag.directional_trading, StrategyTag.funding_rate_arbitrage, StrategyTag.mean_reversion},
    #: Vault: NT Earn. Added: 2026-11-12.
    #: Decision material: Software ranks and rebalances lending markets by risk, interest and borrowing capacity.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/nt-earn
    make_solana_vault_address("HWjMfYfc7KoPchoEDa5UFyVSpJxY3aV1RAccKvABYb9z"): {StrategyTag.algorithmic_trading, StrategyTag.lending, StrategyTag.lending_optimisation},
    #: Vault: Neutral Autopilot. Added: 2026-11-12.
    #: Decision material: The allocation engine automatically weights a set of trading vaults, including a limited directional sleeve; no blanket delta-neutral or human-discretion tag.
    #: Source: https://docs.neutral.trade/for-capital-allocators/neutral-autopilot
    make_solana_vault_address("J7qhMAKnB6G5dvoAN9281ufabajKbyGQxxd2bq6R7fPJ"): {StrategyTag.algorithmic_trading, StrategyTag.multistrategy},
    #: Vault: Hyperliquid Funding Arb. Added: 2026-11-12.
    #: Decision material: Automatically rebalanced long spot/short perpetual positions collect funding; a separate HLP allocation supplies liquidity.
    #: Source: https://docs.neutral.trade/for-capital-allocators/usdc-yield/market-neutral/hyperliquid-funding-arb
    make_solana_vault_address("nE1x7KQq2sm3GQrafQUUdBkSPPT52FmiMM9qAS1dgnC"): {StrategyTag.algorithmic_trading, StrategyTag.arbitrage, StrategyTag.funding_rate_arbitrage, StrategyTag.delta_neutral, StrategyTag.perpetual_futures, StrategyTag.liquidity_provider},
}


def get_strategy_tags(address: str) -> set[StrategyTag] | None:
    """Return a copy of the reviewed native strategy classification.

    Keys use the provider-neutral Solana identity, not mutable names or
    lowercased base58 accounts. Unknown identities remain unclassified.

    :param address: Synthetic ``solana-vault-`` identity used in shared storage.
    :return: Independent tag set, or ``None`` when no review exists.
    """
    tags = STRATEGY_TAGS.get(address)
    return set(tags) if tags is not None else None
