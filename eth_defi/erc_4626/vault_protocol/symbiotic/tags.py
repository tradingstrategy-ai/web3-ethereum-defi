"""Maintained strategy classifications for Symbiotic vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    #: Vault: KPK USDC LiquidLane.
    #: Added: 2026-08-18.
    #: Decision material: Trading Strategy describes the vault as market-making
    #: tokenised RWA collateral by buying at a discount when holders need
    #: immediate liquidity and redeeming at par with the issuer. The same
    #: description identifies the collateral as RWA and calls out 24/7 risk
    #: management automation, so market-making, RWA, and algorithmic-trading
    #: tags apply.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/kpk-usdc-liquidlane
    #: - https://app.symbiotic.fi/vault/0x8bcd746976885b5832bad07b4921e3f2dd1d3703
    #: - eth_defi/erc_4626/vault_protocol/symbiotic/vault.py
    HexAddress("0x8bcd746976885b5832bad07b4921e3f2dd1d3703"): {
        StrategyTag.algorithmic_trading,
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
        StrategyTag.rwa,
    },
}
