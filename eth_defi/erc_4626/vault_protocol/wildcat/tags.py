"""Strategy classifications for Wildcat markets."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: Every Wildcat market lends its asset to one borrower, by definition, and
#: lenders are unsecured creditors: markets have no collateral to seize.
#: https://docs.wildcat.finance/overview/introduction
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.lending, StrategyTag.unsecured_credit})

#: Address-specific classifications maintained by the vault categorisation skill.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {}


def get_strategy_tags(address: HexAddress) -> set[StrategyTag]:
    """Return the automatic Wildcat lending and unsecured credit tags and any address-specific tags.

    :param address:
        Wildcat market address.
    :return:
        A copy of the default tags plus any manually maintained tags.
    """
    return set(DEFAULT_STRATEGY_TAGS) | (lookup_strategy_tags(STRATEGY_TAGS, address) or set())
