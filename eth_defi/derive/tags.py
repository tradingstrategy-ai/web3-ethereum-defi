"""Maintained strategy classifications for Derive v3 native vaults.

Derive vaults may use options, perpetuals, spot or lending. For each mainnet
entry, record the source supporting its classification in a nearby comment.
Do not add testnet vaults. A missing entry means the strategy is unclassified.
"""

from eth_defi.vault.strategy_tag import StrategyTag

#: Mainnet synthetic vault address to reviewed strategy tags.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {}


def get_strategy_tags(address: str) -> set[StrategyTag] | None:
    """Return a copy of maintained tags, or ``None`` for unknown strategy.

    :param address: Mainnet native vault synthetic address.
    :return: Strategy tags or ``None`` when source evidence is insufficient.
    """
    tags = STRATEGY_TAGS.get(address.lower())
    return set(tags) if tags is not None else None
