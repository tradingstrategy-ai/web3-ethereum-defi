"""Strategy classifications for Euler vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: Euler EVK and EulerEarn vaults supply assets to lending markets by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.lending})

#: Address-specific classifications maintained by the vault categorisation skill.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {}


def get_strategy_tags(address: HexAddress) -> set[StrategyTag]:
    """Return automatic Euler lending and any address-specific tags.

    :param address:
        Euler vault address.
    :return:
        A copy of the default lending tag plus any manually maintained tags.
    """
    return set(DEFAULT_STRATEGY_TAGS) | (lookup_strategy_tags(STRATEGY_TAGS, address) or set())
