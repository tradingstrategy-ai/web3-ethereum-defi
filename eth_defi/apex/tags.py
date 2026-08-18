"""Maintained strategy classifications for ApeX native vaults."""

from eth_defi.vault.strategy_tag import StrategyTag, combine_strategy_tags

#: ApeX native vaults trade perpetual futures by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.perpetual_futures})

#: ApeX platform vault IDs may receive additional reviewed classifications.
#: Keys are the synthetic identities exported by the ApeX pipeline.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {}


def get_strategy_tags(address: str) -> set[StrategyTag]:
    """Get maintained strategy tags for an ApeX native vault.

    :param address:
        ApeX synthetic vault identifier, such as ``apex-vault-10001``.
    :return:
        A fresh tag set containing the perpetual-futures default and any
        address-specific classifications.
    """
    return combine_strategy_tags(DEFAULT_STRATEGY_TAGS, STRATEGY_TAGS, address)
