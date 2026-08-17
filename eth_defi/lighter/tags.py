"""Maintained strategy classifications for Lighter native pools."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Lighter native pools trade perpetual futures by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.perpetual_futures})

#: Synthetic-address-specific classifications maintained in addition to the
#: native perpetual-futures default, e.g. ``lighter-pool-123``.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {}


def get_strategy_tags(address: str) -> set[StrategyTag]:
    """Get maintained strategy tags for a Lighter native pool.

    :param address:
        Lowercase-compatible synthetic Lighter pool address.
    :return:
        New tag set containing the native perpetual-futures default and any
        address-specific classifications.
    """
    return set(DEFAULT_STRATEGY_TAGS) | STRATEGY_TAGS.get(address.lower(), set())
