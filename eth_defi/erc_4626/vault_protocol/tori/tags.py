"""Maintained strategy classifications for Tori Finance vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Staked trUSD (strUSD), Ethereum native staking vault.
    #: Added: 2026-10-29.
    #: Decision material: Tori combines short-duration money-market positions,
    #: spot/futures arbitrage and calendar spreads under a market-neutral
    #: mandate. Carry and pricing differences fund the staking rewards.
    #: This does not establish perpetual-futures or funding-rate exposure.
    #: Sources:
    #: - https://docs.tori.finance/strategy/overview
    #: - https://docs.tori.finance/products/strusd
    #: - https://docs.tori.finance/resources/contracts
    "0x280839980a7ed0d7717f64125fe241012e5f5815": {
        StrategyTag.multistrategy,
        StrategyTag.delta_neutral,
        StrategyTag.arbitrage,
        StrategyTag.carry_trade,
    },
}
