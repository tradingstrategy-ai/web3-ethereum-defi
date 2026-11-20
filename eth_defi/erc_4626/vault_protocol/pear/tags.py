"""Reviewed strategy tags for individual Pear vaults."""

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_CHAIN_ID
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Agent Pear, reviewed 2026-11-20. The algorithm uses spread deviations
    #: to open pairs of correlated perpetuals with hedge-ratio-weighted legs.
    #: It aims to profit as the spread returns to its average.
    #: https://docs.pear.garden/vaults#the-agent-pear-vault
    #: https://pro-gateway.pearprotocol.io/vaults
    "0x65c9f4665c7d9ce96f53aafc67796dcad46f4096": {
        StrategyTag.algorithmic_trading,
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.long_short_portfolio,
        StrategyTag.mean_reversion,
        StrategyTag.pair_trading,
        StrategyTag.perpetual_futures,
        StrategyTag.statistical_arbitrage,
    },
    #: Emporium Ventures SPC, reviewed 2026-11-20. The vault tracks its own
    #: long-short index with quantitative weight rebalancing and rules for
    #: constituent changes. The team targets an even long/short split.
    #: https://pro-gateway.pearprotocol.io/vaults
    #: https://x.com/EV_labs_/status/2100983716658970819
    #: https://x.com/Pasta_Capital/status/2122280477826425294
    "0xd38a67a4f54a9b4286d72f08440485a395ddfa70": {
        StrategyTag.algorithmic_trading,
        StrategyTag.delta_neutral,
        StrategyTag.index,
        StrategyTag.long_short_portfolio,
        StrategyTag.perpetual_futures,
    },
    #: Valkyrie L/S, reviewed 2026-11-20. The manager selects relative-value
    #: pairs and usually targets beta neutrality. The mandate also allows
    #: occasional directional trades at the manager's discretion.
    #: https://pro-gateway.pearprotocol.io/vaults
    #: https://x.com/pear_protocol/status/2117995803239239840
    "0xdf6917ee9a2d9b34075bb099a0a1e955bcfcdf84": {
        StrategyTag.delta_neutral,
        StrategyTag.directional_trading,
        StrategyTag.discretionary_trading,
        StrategyTag.long_short_portfolio,
        StrategyTag.pair_trading,
        StrategyTag.perpetual_futures,
    },
    #: Longevity Vault, reviewed 2026-11-20. Moritz chooses swing pairs and
    #: shorter-term trades. The published leverage limits are ceilings.
    #: https://pro-gateway.pearprotocol.io/vaults
    "0x69f2ed780a4cec49e552dbcd35a60f41368bc362": {
        StrategyTag.discretionary_trading,
        StrategyTag.long_short_portfolio,
        StrategyTag.pair_trading,
        StrategyTag.perpetual_futures,
    },
}


def get_pear_strategy_tags(chain_id: int, address: HexAddress) -> set[StrategyTag] | None:
    """Resolve the reviewed strategy for a Pear deployment on HyperEVM.

    The adapter and the persisted metadata refresh share this rule. A
    same-address contract on another chain needs its own evidence.

    :param chain_id: EVM chain ID.
    :param address: Vault contract address.
    :return: Copy of the maintained tags, or None when no classification exists.
    """
    if chain_id == PEAR_CHAIN_ID:
        return lookup_strategy_tags(STRATEGY_TAGS, address)
    return None
