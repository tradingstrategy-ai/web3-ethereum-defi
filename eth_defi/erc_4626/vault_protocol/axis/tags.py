"""Maintained strategy classifications for Axis vaults."""

from eth_defi.erc_4626.vault_protocol.axis.constants import AXIS_ETHEREUM_STAKED_USDX_VAULT, AXIS_PLASMA_STAKED_USDX_VAULT
from eth_defi.vault.strategy_tag import StrategyTag

#: Axis documents these market-neutral strategies for the sUSDx rewards vault.
#: Both reviewed deployments represent the same strategy-backed sUSDx product.
#: Added: 2026-08-30.
#: Sources:
#: - https://docs.axis.to/reference/staking-contracts
#: - https://docs.axis.to/susdx-the-rewards-vault/how-axis-earns-yield/trading-strategies
#: - https://docs.axis.to/susdx-the-rewards-vault/how-axis-earns-yield/trading-strategies/delta-neutrality
#: - https://docs.axis.to/susdx-the-rewards-vault/how-axis-earns-yield/trading-strategies/funding-rate-arbitrage
AXIS_STRATEGY_TAGS = frozenset(
    {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.multistrategy,
        StrategyTag.perpetual_futures,
    }
)

#: Address-routed strategy classifications for both reviewed deployments.
STRATEGY_TAGS: dict[str, frozenset[StrategyTag]] = {
    AXIS_ETHEREUM_STAKED_USDX_VAULT: AXIS_STRATEGY_TAGS,
    AXIS_PLASMA_STAKED_USDX_VAULT: AXIS_STRATEGY_TAGS,
}
