"""Maintained strategy classifications for YieldNest vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

#: Address-specific classifications maintained by the vault categorisation skill.
STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    #: Vault: ynRWAx (YieldNest RWA MAX).
    #: Added: 2026-08-18.
    #: Decision material: YieldNest describes ynRWAx as a fixed-maturity
    #: real-world-asset strategy that lends against RWA collateral, supporting
    #: both the broad RWA and specific RWA-lending tags.
    #: Sources:
    #: - https://www.yieldnest.finance
    #: - https://docs.yieldnest.finance
    #: - https://etherscan.io/address/0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8
    #: - eth_defi/erc_4626/vault_protocol/yieldnest/vault.py
    HexAddress("0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8"): {
        StrategyTag.rwa,
        StrategyTag.rwa_lending,
    },
}
