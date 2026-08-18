"""Maintained strategy classifications for Lighter native pools."""

from eth_defi.vault.strategy_tag import StrategyTag, combine_strategy_tags

#: Lighter native pools trade perpetual futures by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.perpetual_futures})

#: Synthetic-address-specific classifications maintained in addition to the
#: native perpetual-futures default.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: pmalt.
    #: Added: 2026-08-17.
    #: Decision material: Maintainer classification marks pmalt's public
    #: Lighter pool as algorithmic trading and pair trading.
    #: Sources:
    #: - eth_defi/data/feeds/curators/pmalt.yaml
    #: - https://app.lighter.xyz/public-pools/281474976552918
    "lighter-pool-281474976552918": {
        StrategyTag.algorithmic_trading,
        StrategyTag.pair_trading,
    },
    #: Vault: Steady Wealth Builder.
    #: Added: 2026-08-18.
    #: Decision material: The current Lighter description explicitly calls
    #: this a long-only DCA grid strategy, supporting directional grid trading.
    #: Sources:
    #: - https://app.lighter.xyz/public-pools/281474976552443
    #: - https://mainnet.zklighter.elliot.ai/api/v1/account?by=index&value=281474976552443
    "lighter-pool-281474976552443": {
        StrategyTag.directional_trading,
        StrategyTag.grid_trading,
    },
}


def get_strategy_tags(address: str) -> set[StrategyTag]:
    """Get maintained strategy tags for a Lighter native pool.

    :param address:
        Lowercase-compatible synthetic Lighter pool address.
    :return:
        New tag set containing the native perpetual-futures default and any
        address-specific classifications.
    """
    return combine_strategy_tags(DEFAULT_STRATEGY_TAGS, STRATEGY_TAGS, address)
