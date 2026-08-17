"""Maintained strategy classifications for Atoma vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

#: Atoma's documented strategy holds offsetting perpetual positions across
#: venues to capture funding-rate spreads. Its RWA vault applies this approach
#: to perpetual markets for traditional assets.
STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    HexAddress("0xcc56410e1a136af0eceb7241c6ae394f4d8b581c"): {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
    },
    HexAddress("0x1c788e14d8e5b446e3f71b5142e2edabcab36da1"): {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
        StrategyTag.rwa,
    },
}
