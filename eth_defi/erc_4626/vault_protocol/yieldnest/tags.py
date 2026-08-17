"""Maintained strategy classifications for YieldNest vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

#: ynRWAx is YieldNest's fixed-maturity real-world-asset lending vault.
STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    HexAddress("0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8"): {
        StrategyTag.rwa,
        StrategyTag.rwa_lending,
    },
}
