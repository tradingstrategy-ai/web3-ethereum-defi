"""Maintained strategy classifications for 40acres vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Address-specific strategy classifications supported by official sources.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Aerodrome USDC (chain 8453).
    #: Added: 2026-10-21.
    #: Decision material: Protocol USDC supply vault finances veNFT collateral loans repaid using DEX reward cashflows.
    #: Sources:
    #: - https://docs.40acres.finance/contracts
    #: - https://docs.40acres.finance/lending
    "0xb99b6df96d4d5448cc0a5b3e0ef7896df9507cf5": {StrategyTag.lending},
    #: Vault: Blackhole USDC (chain 43114).
    #: Added: 2026-10-21.
    #: Decision material: Protocol USDC supply vault finances veNFT collateral loans repaid using DEX reward cashflows.
    #: Sources:
    #: - https://docs.40acres.finance/contracts
    #: - https://docs.40acres.finance/lending
    "0xc0485c4bafb594ae1457820fb6e5b67e8a04bcfd": {StrategyTag.lending},
    #: Vault: Pharaoh USDC (chain 43114).
    #: Added: 2026-10-21.
    #: Decision material: Protocol USDC supply vault finances veNFT collateral loans repaid using DEX reward cashflows.
    #: Sources:
    #: - https://docs.40acres.finance/contracts
    #: - https://docs.40acres.finance/lending
    "0x124d00b1ce4453ffc5a5f65ce83af13a7709bac7": {StrategyTag.lending},
}
