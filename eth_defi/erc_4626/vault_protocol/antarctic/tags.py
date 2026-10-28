"""Maintained Antarctic strategy decisions for both reviewed products."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: AMLP. Added: 2026-09-30.
    #: The exchange documents market-making liquidity that is counterparty
    #: to perpetual futures traders. No AMM or CLOB execution tag is inferred.
    #: Sources:
    #: - https://docs.antarctic.exchange/antarctic-overview/liquidity-provider-protection
    #: - https://docs.antarctic.exchange/faq
    "0x152f5e6142db867f905a68617dbb6408d7993a4b": {StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.perpetual_futures},
    #: AHLP. Added: 2026-09-30.
    #: The exchange identifies AHLP as the hedging pool in its liquidity
    #: provider system. This establishes capital provision to the venue, but
    #: does not establish hedge instruments, delta neutrality or arbitrage.
    #: Sources:
    #: - https://docs.antarctic.exchange/antarctic-overview/liquidity-provider-protection
    #: - https://docs.antarctic.exchange/technical-framework/hybird-lp-model
    "0x5fd22da8315992dbbd82d5ac1087803ff134c2c4": {StrategyTag.liquidity_provider},
}
