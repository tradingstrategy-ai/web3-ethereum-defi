"""Maintained strategy classifications for Derive v3 native vaults.

Derive vaults may use options, perpetuals, spot or lending. No strategy tag is
inferred solely from Derive hosting a vault. Add a mainnet address here only
after reviewing the current curator description and primary source evidence.
"""

from eth_defi.vault.strategy_tag import StrategyTag

#: Mainnet synthetic address to evidence-backed strategy tags.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {}


def get_strategy_tags(address: str) -> set[StrategyTag] | None:
    """Return a copy of maintained tags, or ``None`` for unknown strategy.

    :param address: Mainnet native vault synthetic address.
    :return: Strategy tags or ``None`` when source evidence is insufficient.
    """
    tags = STRATEGY_TAGS.get(address.lower())
    return set(tags) if tags is not None else None
