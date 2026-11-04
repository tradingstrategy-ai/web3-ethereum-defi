"""Maintained strategy classifications for Veranta."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Avantis USDC Vault. Added: 2026-11-04.
    #: Decision material: Unified avUSDC supplies counterparty liquidity to
    #: perpetual markets and earns trading fees while bearing payout risk.
    #: Sources:
    #: - https://docs.veranta.xyz/liquidity-providers/veranta-lp-vault-avusdc
    #: - https://basescan.org/address/0x944766f715b51967e56afde5f0aa76ceacc9e7f9
    "0x944766f715b51967e56afde5f0aa76ceacc9e7f9": {StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.perpetual_futures},
}
