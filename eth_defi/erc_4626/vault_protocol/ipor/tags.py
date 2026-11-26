"""Maintained strategy classifications for IPOR Fusion vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
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
    "0xdf8a0d3c90462c4c9b5a8697c119fa67cb84a874": {
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
    "0x7f1f605e755c06d428a80db3d473fc46a14ee2cb": {
        StrategyTag.algorithmic_trading,
        StrategyTag.lending_optimisation,
    },
    #: Vault: Origin Protocol wOUSD Leverage Loop (Ethereum).
    #: Added: 2026-11-26.
    #: Decision material: The vault posts wOUSD on Morpho, borrows USDC,
    #: converts the proceeds to wOUSD and supplies it again.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/origin-protocol-wousd-leverage-loop
    #: - https://www.originprotocol.com/blog/june-2026-token-holder-update
    "0xf373a4d4d200d709aa73d4e0be49b822b0cc866d": {StrategyTag.lending_looping},
    #: Vault: Bitcoin Dollar USDC (Ethereum).
    #: Added: 2026-11-26.
    #: Decision material: IPOR classifies the deployment as leveraged looping.
    #: Its description says the vault borrows wBTC to reach 2x sBTCD exposure.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/bitcoin-dollar-usdc
    #: - https://app.ipor.io/fusion/ethereum/0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8
    "0xf8f226da66244f89e70c5b5d1a5c5b0d505eb1d8": {StrategyTag.lending_looping},
}
