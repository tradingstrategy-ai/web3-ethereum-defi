"""Maintained strategy classifications for IPOR Fusion vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    #: Vault: Prime HELOC Loop.
    #: Added: 2026-08-18.
    #: Decision material: The vault description says deposits are converted
    #: into PRIME, supplied as collateral, and used to borrow pyUSD to reach
    #: 5x target leverage (80% LTV), with flashloan-based leverage management.
    #: It also describes Figure's onchain private-credit infrastructure and
    #: Figure-originated HELOC collateral, supporting RWA and RWA credit tags.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/prime-heloc-loop
    #: - https://app.ipor.io/fusion/ethereum/0xdf8a0d3c90462c4c9b5a8697c119fa67cb84a874
    #: - eth_defi/erc_4626/vault_protocol/ipor/vault.py
    HexAddress("0xdf8a0d3c90462c4c9b5a8697c119fa67cb84a874"): {
        StrategyTag.lending_looping,
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
    },
    #: Vault: TAU Base USDC LO.
    #: Added: 2026-08-18.
    #: Decision material: The vault description says it allocates capital to
    #: optimal risk-adjusted lending markets across different money markets
    #: and programmatically rebalances using TAU's risk engine. This supports
    #: lending optimisation and algorithmic trading.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/tau-base-usdc-lo
    #: - https://app.ipor.io/fusion/base/0x7f1f605e755c06d428a80db3d473fc46a14ee2cb
    #: - eth_defi/erc_4626/vault_protocol/ipor/vault.py
    HexAddress("0x7f1f605e755c06d428a80db3d473fc46a14ee2cb"): {
        StrategyTag.algorithmic_trading,
        StrategyTag.lending_optimisation,
    },
}
