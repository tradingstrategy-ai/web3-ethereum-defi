"""Maintained strategy classifications for Hibachi native vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Hibachi native vaults trade perpetual futures by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.perpetual_futures})

#: Synthetic-address-specific classifications maintained in addition to the
#: native perpetual-futures default, e.g. ``hibachi-vault-2``.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {}


def get_strategy_tags(address: str) -> set[StrategyTag]:
    """Get maintained strategy tags for a Hibachi native vault.

    :param address:
        Lowercase-compatible synthetic Hibachi vault address.
    :return:
        New tag set containing the native perpetual-futures default and any
        address-specific classifications.
    """
    return set(DEFAULT_STRATEGY_TAGS) | STRATEGY_TAGS.get(address.lower(), set())
