"""Maintained Antarctic strategy decisions for both reviewed products."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: AMLP. Added: 2026-09-30.
    #: The exchange documents market-making liquidity that is counterparty
    #: to perpetual futures traders. No AMM or CLOB execution tag is inferred.
    #: Sources:
    #: - https://docs.antarctic.exchange/antarctic-overview/liquidity-provider-protection
    #: - https://docs.antarctic.exchange/faq
    "0x152f5e6142db867f905a68617dbb6408d7993a4b": {StrategyTag.market_making, StrategyTag.perpetual_futures},
}

#: AHLP was separately reviewed on 2026-09-30 against
#: https://docs.antarctic.exchange/antarctic-overview/liquidity-provider-protection.
#: Its hedging label does not establish instruments or delta neutrality.
#: Deliberately unmapped: the adapter returns None for missing information.
