"""Maintained strategy classifications for gmx vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Address-specific strategy classifications supported by official sources.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: GM SKY [WETH-USDC] (chain 42161).
    #: Added: 2026-10-21.
    #: Decision material: Native GM pool; SKY is the index market, not the curator.
    #: Sources:
    #: - https://docs.gmx.io/docs/providing-liquidity/
    "0x00310c6d8a9f821e3fe991f4835f2ca4d87034cf": {StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.market_making_amm, StrategyTag.perpetual_futures},
}
