"""Maintained strategy classifications for Hibachi native vaults."""

from eth_defi.vault.strategy_tag import StrategyTag, combine_strategy_tags

#: Hibachi native vaults trade perpetual futures by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.perpetual_futures})

#: Synthetic-address-specific classifications maintained in addition to the
#: native perpetual-futures default, e.g. ``hibachi-vault-2``.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Growi Alpha Vault.
    #: Added: 2026-08-17.
    #: Decision material: Hibachi identifies this as Growi Finance's core
    #: vault and describes its strategy as systematic mean-reversion on crypto
    #: perpetual futures.
    #: Sources:
    #: - https://data-api.hibachi.xyz/vault/info?vaultId=2
    #: - https://hibachi.xyz/vaults
    #: - eth_defi/hibachi/README.md
    "hibachi-vault-2": {StrategyTag.mean_reversion},
    #: Vault: Fire Liquidity Provider.
    #: Added: 2026-08-17.
    #: Decision material: Hibachi describes FLP as market making across all
    #: Hibachi markets and identifies it as operated by Kappa Lab.
    #: Sources:
    #: - https://data-api.hibachi.xyz/vault/info?vaultId=3
    #: - https://hibachi.xyz/vaults
    #: - eth_defi/hibachi/README.md
    "hibachi-vault-3": {
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
    },
}


def get_strategy_tags(address: str) -> set[StrategyTag]:
    """Get maintained strategy tags for a Hibachi native vault.

    :param address:
        Lowercase-compatible synthetic Hibachi vault address.
    :return:
        New tag set containing the native perpetual-futures default and any
        address-specific classifications.
    """
    return combine_strategy_tags(DEFAULT_STRATEGY_TAGS, STRATEGY_TAGS, address)
