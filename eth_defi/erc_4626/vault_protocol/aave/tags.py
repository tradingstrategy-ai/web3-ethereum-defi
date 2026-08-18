"""Strategy classifications for Aave vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: Aave vaults supply assets to Aave lending markets by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.lending})

#: Address-specific classifications maintained by the vault categorisation skill.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {}


def get_strategy_tags(address: HexAddress) -> set[StrategyTag]:
    """Return automatic Aave lending and any address-specific tags.

    :param address:
        Aave vault address.
    :return:
        A copy of the default lending tag plus any manually maintained tags.
    """
    return set(DEFAULT_STRATEGY_TAGS) | (lookup_strategy_tags(STRATEGY_TAGS, address) or set())
