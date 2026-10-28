"""Maintained strategy classifications for plutus vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Address-specific strategy classifications supported by official sources.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Plutus Hedge Token (chain 42161).
    #: Added: 2026-10-21.
    #: Decision material: Official Plutus documentation describes automated in-house strategies; current plvHEDGE is multistrategy.
    #: Sources:
    #: - https://docs.plutus.fi/products/plvassets
    #: - https://docs.plutus.fi/products/plvassets/plvdolo.md
    #: - https://docs.plutus.fi/products/plvassets/plvhedge.md
    "0x58bfc95a864e18e8f3041d2fcd3418f48393fe6a": {StrategyTag.algorithmic_trading, StrategyTag.multistrategy},
    #: Vault: Plutus Dolomite vault (chain 42161).
    #: Added: 2026-10-21.
    #: Decision material: Official Plutus documentation describes automated in-house strategies; current plvHEDGE is multistrategy.
    #: Sources:
    #: - https://docs.plutus.fi/products/plvassets
    #: - https://docs.plutus.fi/products/plvassets/plvdolo.md
    #: - https://docs.plutus.fi/products/plvassets/plvhedge.md
    "0xf84eaa0685626f84fe17bc6c3c9eb2ac8a90d3c1": {StrategyTag.algorithmic_trading},
}
