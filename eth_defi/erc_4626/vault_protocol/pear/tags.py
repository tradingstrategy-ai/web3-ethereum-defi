"""Reviewed strategy tags for individual Pear vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Agent Pear, reviewed 2026-11-20. Automated convergence trades in correlated perpetuals.
    #: https://docs.pear.garden/vaults#the-agent-pear-vault
    #: https://vaults.pear.garden/0x65c9f4665c7d9ce96f53aafc67796dcad46f4096
    "0x65c9f4665c7d9ce96f53aafc67796dcad46f4096": {
        StrategyTag.algorithmic_trading,
        StrategyTag.delta_neutral,
        StrategyTag.long_short_portfolio,
        StrategyTag.mean_reversion,
        StrategyTag.perpetual_futures,
        StrategyTag.statistical_arbitrage,
    },
    #: Emporium Ventures SPC, reviewed 2026-11-20. Baskets of long and short crypto positions.
    #: https://vaults.pear.garden/0xd38a67a4f54a9b4286d72f08440485a395ddfa70
    "0xd38a67a4f54a9b4286d72f08440485a395ddfa70": {StrategyTag.long_short_portfolio, StrategyTag.perpetual_futures},
    #: Valkyrie L/S, reviewed 2026-11-20. The manager selects relative-value pairs and can take directional exposure.
    #: https://vaults.pear.garden/0xdf6917ee9a2d9b34075bb099a0a1e955bcfcdf84
    "0xdf6917ee9a2d9b34075bb099a0a1e955bcfcdf84": {StrategyTag.discretionary_trading, StrategyTag.long_short_portfolio, StrategyTag.perpetual_futures},
    #: Longevity Vault, reviewed 2026-11-20. Moritz chooses swing pairs and shorter-term trades.
    #: https://vaults.pear.garden/0x69f2ed780a4cec49e552dbcd35a60f41368bc362
    "0x69f2ed780a4cec49e552dbcd35a60f41368bc362": {StrategyTag.discretionary_trading, StrategyTag.long_short_portfolio, StrategyTag.perpetual_futures},
}
