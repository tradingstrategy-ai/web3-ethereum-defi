"""Reviewed Infinit strategy classifications."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: APEX MultiStrategy, added and reviewed 2026-10-26.
    #: Curator: Apex Quant Capital, identified through Infinit's manager links.
    #: Deterministic relative-strength rules match long and short perpetual books.
    #: https://docs.infinit.tech/infinit-ai-managed-trading-strategy-marketplace/the-debut-vault
    #: https://app.infinit.tech/vaults/apex-multistrategy
    #: https://www.apexquants.xyz/
    "0xe64655e24b66073c73300aa924cd6df5c716638f": {StrategyTag.algorithmic_trading, StrategyTag.delta_neutral, StrategyTag.perpetual_futures},
}


def get_strategy_tags(address: HexAddress) -> set[StrategyTag] | None:
    """Resolve reviewed tags without exposing the maintained mutable set.

    Unknown products deliberately retain missing strategy information.

    :param address: Actual vault address.
    :return: Copy of the reviewed tags, or ``None``.
    """
    return lookup_strategy_tags(STRATEGY_TAGS, address)
