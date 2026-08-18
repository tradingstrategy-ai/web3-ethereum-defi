"""Maintained strategy classifications for Ethena vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    #: Vault: Staked USDe.
    #: Added: 2026-08-18.
    #: Decision material: Ethena documents automated, programmatic
    #: delta-neutral hedges using short perpetual futures. The protocol earns
    #: funding and basis spread from those positions, which is distributed to
    #: sUSDe stakers as carry. This supports algorithmic, delta-neutral,
    #: perpetual-futures, funding-rate-arbitrage, and carry-trade tags.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/staked-usde-3
    #: - https://docs.ethena.fi/how-usde-works
    #: - https://docs.ethena.fi/solution-design/staking-usde
    #: - https://etherscan.io/address/0x9d39a5de30e57443bff2a8307a4256c8797a3497
    #: - eth_defi/erc_4626/vault_protocol/ethena/vault.py
    HexAddress("0x9d39a5de30e57443bff2a8307a4256c8797a3497"): {
        StrategyTag.algorithmic_trading,
        StrategyTag.carry_trade,
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
    },
}
