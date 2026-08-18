"""Strategy classifications for Morpho vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

#: Morpho MetaMorpho vaults supply assets to Morpho lending markets by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.lending})

#: Address-specific classifications maintained by the vault categorisation skill.
STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {}


def get_strategy_tags(address: HexAddress) -> set[StrategyTag]:
    """Return automatic Morpho lending and any address-specific tags.

    :param address:
        Morpho vault address.
    :return:
        A copy of the default lending tag plus any manually maintained tags.
    """
    tags = set(DEFAULT_STRATEGY_TAGS)
    tags.update(STRATEGY_TAGS.get(HexAddress(address.lower()), set()))
    return tags
