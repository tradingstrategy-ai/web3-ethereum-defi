"""Tests for maintained Panoptic vault strategy classifications."""

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.panoptic.vault import PanopticVault
from eth_defi.vault.strategy_tag import StrategyTag


def test_popt_v1_1_usdc_lp_strategy_tags() -> None:
    """POPT-V1.1 USDC LP returns its AMM market-making tags."""
    vault = object.__new__(PanopticVault)
    vault.vault_address = HexAddress("0xabbad7a755bdf9bbec357e2bdf4c02934a8d7a71")

    assert vault.get_strategy_tags() == {
        StrategyTag.amm,
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
        StrategyTag.market_making_amm,
    }


def test_unmapped_panoptic_strategy_tags_return_none() -> None:
    """Unmapped Panoptic vaults retain the missing-information distinction."""
    vault = object.__new__(PanopticVault)
    vault.vault_address = HexAddress("0x0000000000000000000000000000000000000000")

    assert vault.get_strategy_tags() is None
