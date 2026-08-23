"""Maintained strategy classifications for ApeX native vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: User-created ApeX vaults do not publish their strategy details. Categorise
#: them as discretionary perpetual-futures strategies unless ApeX documents a
#: different strategy.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset(
    {
        StrategyTag.discretionary_trading,
        StrategyTag.perpetual_futures,
    }
)

#: ApeX platform vault IDs with an explicit, non-discretionary strategy.
#: Keys are the synthetic identities exported by the ApeX pipeline.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Protocol Vault.
    #: Added: 2026-08-23.
    #: Decision material: ApeX describes its flagship protocol-operated vault
    #: as the equivalent of a market-making/liquidity-provider vault. Returns
    #: derive from perpetual-exchange liquidation fees, not directional trades.
    #: Sources:
    #: - https://www.apex.exchange/blog/detail/Introducing-Protocol-Vaults-on-ApeX-Omni-Stable-Returns-Backed-by-Real-Fees
    #: - eth_defi/apex/constants.py
    "apex-vault-10000": {
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
    },
    #: Vault: New Vault (New User Vault).
    #: Added: 2026-08-23.
    #: Decision material: ApeX says this onboarding vault earns the same
    #: perpetual-exchange liquidation-fee yield as the Protocol Vault, giving
    #: it the same protocol liquidity-provider/market-making classification.
    #: Sources:
    #: - https://www.apex.exchange/blog/detail/weekly-update-11may2026
    #: - eth_defi/apex/constants.py
    "apex-vault-10001": {
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
    },
}


def get_strategy_tags(address: str) -> set[StrategyTag]:
    """Get maintained strategy tags for an ApeX native vault.

    :param address:
        ApeX synthetic vault identifier, such as ``apex-vault-10001``.
    :return:
        A fresh tag set containing the discretionary perpetual-futures default,
        or the documented classification for an official vault.
    """
    specific_tags = STRATEGY_TAGS.get(address.lower())
    if specific_tags is None:
        return set(DEFAULT_STRATEGY_TAGS)

    return {StrategyTag.perpetual_futures} | set(specific_tags)
