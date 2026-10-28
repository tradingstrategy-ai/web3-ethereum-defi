"""Reviewed Nova Markets strategy classifications, separate from gTrade."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Nova Conviction Pool, Arbitrum. Added: 2026-10-28.
    #: Decision material: Nova's exact-address disclosure identifies its
    #: USDC counterparty pool for oracle-priced perpetual markets. Depositors
    #: earn fees and bear trader PnL; this is AMM liquidity provision, rather
    #: than the separate Lagoon NLP order-book market-making strategy.
    #: Sources:
    #: - https://docs.nova.markets/#/contracts
    #: - https://docs.nova.markets/#/conviction-pool
    #: - https://docs.nova.markets/#/pricing
    "0x9ca80193712f372e2ff33a00127292884f6f8598": {
        StrategyTag.amm,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_amm,
        StrategyTag.perpetual_futures,
    },
}
