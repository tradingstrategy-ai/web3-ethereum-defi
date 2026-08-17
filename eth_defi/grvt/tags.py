"""Maintained strategy classifications for GRVT native vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: GRVT native vaults trade perpetual futures by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.perpetual_futures})

#: Vault-ID-specific classifications maintained in addition to the native
#: perpetual-futures default. GRVT IDs are stored in lowercase.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {}


def get_strategy_tags(address: str) -> set[StrategyTag]:
    """Get maintained strategy tags for a GRVT native vault.

    :param address:
        Lowercase-compatible GRVT vault ID.
    :return:
        New tag set containing the native perpetual-futures default and any
        address-specific classifications.
    """
    return set(DEFAULT_STRATEGY_TAGS) | STRATEGY_TAGS.get(address.lower(), set())
