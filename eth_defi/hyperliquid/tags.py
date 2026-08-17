"""Maintained strategy classifications for Hyperliquid native vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Hyperliquid native vaults trade perpetual futures by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.perpetual_futures})

#: Address-specific strategy classifications maintained in addition to the
#: native perpetual-futures default. Addresses are lowercase.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {}


def get_strategy_tags(address: str) -> set[StrategyTag]:
    """Get maintained strategy tags for a Hyperliquid native vault.

    :param address:
        Lowercase-compatible Hyperliquid vault address.
    :return:
        New tag set containing the native perpetual-futures default and any
        address-specific classifications.
    """
    return set(DEFAULT_STRATEGY_TAGS) | STRATEGY_TAGS.get(address.lower(), set())
