"""Maintained strategy classifications for ember vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Address-specific strategy classifications supported by official sources.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Ember Third Eye (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Ember live API explicitly names the manager and describes the strategy.
    #: Sources:
    #: - https://vaults.api.sui-prod.bluefin.io/api/v2/vaults?chain=ethereum
    "0xf3190a3ecc109f88e7947b849b281918c798a0c4": {StrategyTag.arbitrage, StrategyTag.carry_trade, StrategyTag.delta_neutral, StrategyTag.multistrategy},
    #: Vault: Ember Earn (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Ember live API explicitly names the manager and describes the strategy.
    #: Sources:
    #: - https://vaults.api.sui-prod.bluefin.io/api/v2/vaults?chain=ethereum
    "0x9be9294722f8aad37b11a9792be2c782182cafa2": {StrategyTag.carry_trade, StrategyTag.lending, StrategyTag.liquidity_provider, StrategyTag.multistrategy},
}
