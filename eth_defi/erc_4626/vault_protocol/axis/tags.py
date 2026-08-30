"""Maintained strategy classifications for Axis vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Axis StakedUSDx V2.
    #: Added: 2026-08-30.
    #: Decision material: Axis documents cross-venue and cross-currency
    #: arbitrage as its two core strategies, with delta-neutral hedging and
    #: funding-rate arbitrage through perpetual futures as a supporting
    #: strategy. The rewards funded into sUSDx therefore come from multiple
    #: documented market-neutral strategies.
    #: Sources:
    #: - https://docs.axis.to/reference/staking-contracts
    #: - https://docs.axis.to/susdx-the-rewards-vault/how-axis-earns-yield/trading-strategies
    #: - https://docs.axis.to/susdx-the-rewards-vault/how-axis-earns-yield/trading-strategies/delta-neutrality
    #: - https://docs.axis.to/susdx-the-rewards-vault/how-axis-earns-yield/trading-strategies/funding-rate-arbitrage
    "0xeb892628d1e58bc475a6dcb7f5dbc4f591632aa4": {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.multistrategy,
        StrategyTag.perpetual_futures,
    },
}
