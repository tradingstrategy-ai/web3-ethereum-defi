"""Maintained strategy classifications for Panoptic vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    #: Vault: POPT-V1.1 USDC LP on ETH/USDC 5bps.
    #: Added: 2026-08-18.
    #: Decision material: Trading Strategy identifies this Base vault as a
    #: Panoptic USDC LP for the ETH/USDC 5bps market. Panoptic documents that
    #: it embraces automated market makers and permissionless liquidity
    #: provision in Uniswap; this supports AMM, liquidity-provider, market-
    #: maker, and both general and AMM-specific market-making tags.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/popt-v1-1-usdc-lp-on-eth-usdc-5bps-2
    #: - https://panoptic.xyz/docs/intro
    #: - https://app.panoptic.xyz/markets
    #: - https://basescan.org/address/0xabbad7a755bdf9bbec357e2bdf4c02934a8d7a71
    #: - eth_defi/erc_4626/vault_protocol/panoptic/vault.py
    HexAddress("0xabbad7a755bdf9bbec357e2bdf4c02934a8d7a71"): {
        StrategyTag.amm,
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
        StrategyTag.market_making_amm,
    },
}
