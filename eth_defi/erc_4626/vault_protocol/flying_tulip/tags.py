"""Maintained investment strategy classifications for Flying Tulip sftUSD."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Staked Flying Tulip USD (Ethereum).
    #: Added: 2026-08-24.
    #: Decision material: Flying Tulip's current ftUSD documentation identifies
    #: stablecoin lending as the only implemented onchain strategy. Other
    #: strategy modules remain roadmap items.
    #: Sources:
    #: - https://docs.flyingtulip.com/product-suite/ft-usd/
    #: - eth_defi/erc_4626/vault_protocol/flying_tulip/vault.py
    "0xeb48218a4c35c814c7678cbcae88c6ee037f7625": {
        StrategyTag.lending,
    },
    #: Vault: Staked Flying Tulip USD (Sonic).
    #: Added: 2026-08-24.
    #: Decision material: the current official product documentation identifies
    #: stablecoin lending as the only implemented onchain strategy. The mapping
    #: intentionally omits dormant BNB Chain.
    #: Sources:
    #: - https://docs.flyingtulip.com/product-suite/ft-usd/
    #: - eth_defi/erc_4626/vault_protocol/flying_tulip/vault.py
    "0xd1e5a86f1005f6356bd022c587de0f430cd2aeb1": {
        StrategyTag.lending,
    },
}
