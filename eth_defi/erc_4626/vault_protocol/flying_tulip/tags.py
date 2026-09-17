"""Maintained investment strategy classifications for Flying Tulip sftUSD."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Staked Flying Tulip USD (Ethereum).
    #: Added: 2026-09-17.
    #: Decision material: The ftUSD documentation identifies stablecoin lending
    #: as an implemented yield source and describes the product's delta-neutral
    #: construction and resulting carry. Flying Tulip's June update confirms
    #: that the fully onchain delta-neutral architecture was deployed on
    #: Ethereum and that collateral and delta-neutral strategy yield is
    #: distributed to sftUSD stakers. These are distinct strategy components,
    #: so the vault is also classified as multi-strategy.
    #: Sources:
    #: - https://docs.flyingtulip.com/product-suite/ft-usd/
    #: - https://blog.flyingtulip.com/flying-tulip-june-update/
    #: - https://tradingstrategy.ai/vaults/staked-flying-tulip-usd-6
    #: - eth_defi/erc_4626/vault_protocol/flying_tulip/vault.py
    "0xeb48218a4c35c814c7678cbcae88c6ee037f7625": {
        StrategyTag.carry_trade,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.multistrategy,
    },
    #: Vault: Staked Flying Tulip USD (Sonic).
    #: Added: 2026-09-17.
    #: Decision material: The ftUSD documentation identifies stablecoin lending
    #: as an implemented yield source and describes the product's delta-neutral
    #: construction and resulting carry. Flying Tulip's June update confirms
    #: that the fully onchain delta-neutral architecture was deployed first on
    #: Sonic and that collateral and delta-neutral strategy yield is distributed
    #: to sftUSD stakers. These are distinct strategy components, so the vault
    #: is also classified as multi-strategy.
    #: Sources:
    #: - https://docs.flyingtulip.com/product-suite/ft-usd/
    #: - https://blog.flyingtulip.com/flying-tulip-june-update/
    #: - eth_defi/erc_4626/vault_protocol/flying_tulip/vault.py
    "0xd1e5a86f1005f6356bd022c587de0f430cd2aeb1": {
        StrategyTag.carry_trade,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.multistrategy,
    },
}
