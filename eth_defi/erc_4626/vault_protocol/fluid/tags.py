"""Maintained strategy classifications for fluid vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Address-specific strategy classifications supported by official sources.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Fluid USD Coin (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Native Fluid lending deployment; excludes external wrappers and the unverified legacy fiUSDC deployment.
    #: Sources:
    #: - https://api.fluid.instadapp.io/v2/lending/1/tokens
    "0x9fb7b4477576fe5b32be4c1843afb1e55f251b33": {StrategyTag.lending},
    #: Vault: Fluid USD Coin (chain 42161).
    #: Added: 2026-10-21.
    #: Decision material: Native Fluid lending deployment; excludes external wrappers and the unverified legacy fiUSDC deployment.
    #: Sources:
    #: - https://api.fluid.instadapp.io/v2/lending/42161/tokens
    "0x1a996cb54bb95462040408c06122d45d6cdb6096": {StrategyTag.lending},
    #: Vault: Fluid USD Coin (chain 8453).
    #: Added: 2026-10-21.
    #: Decision material: Native Fluid lending deployment; excludes external wrappers and the unverified legacy fiUSDC deployment.
    #: Sources:
    #: - https://api.fluid.instadapp.io/v2/lending/8453/tokens
    "0xf42f5795d9ac7e9d757db633d693cd548cfd9169": {StrategyTag.lending},
    #: Vault: Fluid Lite USD Vault (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Native Fluid lending deployment; excludes external wrappers and the unverified legacy fiUSDC deployment.
    #: Sources:
    #: - https://docs.fluid.instadapp.io/lite-usd/index.html
    "0x273da948aca9261043fbdb2a857bc255ecc29012": {StrategyTag.lending},
}
