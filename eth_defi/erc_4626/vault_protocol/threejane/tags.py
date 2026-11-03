"""Maintained strategy classifications for 3Jane vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Both 3Jane tranches share one lending pool, so they share one classification.
#:
#: Decision material: 3Jane lends the pooled USDC as uncollateralised credit
#: lines to crypto-native borrowers and through funding facilities for U.S.
#: fintech lenders, whose loans are real-world receivables. In November 2026
#: the protocol announced a forward-flow agreement under which USD3 buys the
#: inventory financing loans that Settle makes to consumer goods brands.
#: Sources:
#: - https://docs.3jane.xyz/resources/faq
#: - https://docs.3jane.xyz/usd3-susd3/suppliers
#: - https://x.com/3janexyz/status/2117624841050411120
THREEJANE_STRATEGY_TAGS = frozenset({StrategyTag.lending, StrategyTag.rwa, StrategyTag.rwa_lending, StrategyTag.inventory_financing})

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: USD3 (senior tranche) on Ethereum.
    #: Added: 2026-11-03.
    #: Decision material and sources: see :py:data:`THREEJANE_STRATEGY_TAGS`.
    "0x056b269eb1f75477a8666ae8c7fe01b64dd55ecc": set(THREEJANE_STRATEGY_TAGS),
    #: Vault: sUSD3 (junior tranche) on Ethereum.
    #: Added: 2026-11-03.
    #: Decision material and sources: see :py:data:`THREEJANE_STRATEGY_TAGS`.
    "0xf689555121e529ff0463e191f9bd9d1e496164a7": set(THREEJANE_STRATEGY_TAGS),
}
