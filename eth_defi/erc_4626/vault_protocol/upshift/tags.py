"""Maintained strategy classifications for Upshift vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    #: Vault: Axis Origin USDx.
    #: Added: 2026-08-17.
    #: Decision material: Axis describes its engine as a delta-neutral,
    #: cross-venue arbitrage strategy with multiple uncorrelated return sources;
    #: maintainer classification also marks it as algorithmic trading.
    #: Sources:
    #: - https://app.axis.to/origin
    #: - docs/protocol-research/axis-origin-0xad958c4c0c90bf0216e0f5472f074a9ab30f595f.md
    HexAddress("0xad958c4c0c90bf0216e0f5472f074a9ab30f595f"): {
        StrategyTag.algorithmic_trading,
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.multistrategy,
    },
}
