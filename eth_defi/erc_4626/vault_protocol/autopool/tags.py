"""Maintained strategy classifications for auto-finance vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Address-specific strategy classifications supported by official sources.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Anchorage/AUTOfinance anchrgUSD (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Reviewed 2026-10-22: solver proposals and onchain validation establish rule-based rebalancing; specific destination exposures are not classified.
    #: Sources:
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics/components-and-logic
    "0x1abd0403591be494771115d74ed9e120530f356e": {StrategyTag.algorithmic_trading},
    #: Vault: AUTOfinance gpuUSD (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Reviewed 2026-10-22: solver proposals and onchain validation establish rule-based rebalancing; specific destination exposures are not classified.
    #: Sources:
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics/components-and-logic
    "0x2916dfb3324377645cac4c5e78dffce910a61393": {StrategyTag.algorithmic_trading},
    #: Vault: Tokemak siloUSD (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Reviewed 2026-10-22: solver proposals and onchain validation establish rule-based rebalancing; specific destination exposures are not classified.
    #: Sources:
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics/components-and-logic
    "0x408b6a3e2daf288864968454aae786a2a042df36": {StrategyTag.algorithmic_trading},
    #: Vault: Tokemak autoUSD (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Reviewed 2026-10-22: solver proposals and onchain validation establish rule-based rebalancing; specific destination exposures are not classified.
    #: Sources:
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics/components-and-logic
    "0xa7569a44f348d3d70d8ad5889e50f78e33d80d35": {StrategyTag.algorithmic_trading},
    #: Vault: AUTOfinance infinifiUSD (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Reviewed 2026-10-22: solver proposals and onchain validation establish rule-based rebalancing; specific destination exposures are not classified.
    #: Sources:
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics/components-and-logic
    "0xc7d7f434c015f2a7e77df9763ade300f2171a05a": {StrategyTag.algorithmic_trading},
    #: Vault: AUTOfinance monUSD (chain 143).
    #: Added: 2026-10-21.
    #: Decision material: Reviewed 2026-10-22: solver proposals and onchain validation establish rule-based rebalancing; specific destination exposures are not classified.
    #: Sources:
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics/components-and-logic
    "0x288604ba0e9f08c22815eb36c2f4a49a44ee16bb": {StrategyTag.algorithmic_trading},
    #: Vault: Tokemak arbUSD (chain 42161).
    #: Added: 2026-10-21.
    #: Decision material: Reviewed 2026-10-22: solver proposals and onchain validation establish rule-based rebalancing; specific destination exposures are not classified.
    #: Sources:
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics/components-and-logic
    "0xf63b7f49b4f5dc5d0e7e583cfd79dc64e646320c": {StrategyTag.algorithmic_trading},
    #: Vault: Tokemak baseUSD (chain 8453).
    #: Added: 2026-10-21.
    #: Decision material: Reviewed 2026-10-22: solver proposals and onchain validation establish rule-based rebalancing; specific destination exposures are not classified.
    #: Sources:
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics
    #: - https://docs.auto.finance/auto-pools-protocol/protocol-mechanics/components-and-logic
    "0x9c6864105aec23388c89600046213a44c384c831": {StrategyTag.algorithmic_trading},
}
