"""Tests for maintained Spiko strategy classifications."""

from dataclasses import replace

from eth_typing import HexAddress

from eth_defi.tokenised_fund.spiko.constants import EUTBL_PRODUCT
from eth_defi.tokenised_fund.spiko.vault import SpikoVault
from eth_defi.vault.strategy_tag import StrategyTag


def test_eutbl_strategy_tags() -> None:
    """EUTBL returns its money-market-fund and RWA tags."""
    vault = object.__new__(SpikoVault)
    vault.product = EUTBL_PRODUCT

    assert vault.get_strategy_tags() == {
        StrategyTag.money_market_fund,
        StrategyTag.rwa,
    }


def test_unmapped_spiko_strategy_tags_return_none() -> None:
    """An unreviewed Spiko product retains the missing-information result."""
    vault = object.__new__(SpikoVault)
    vault.product = replace(EUTBL_PRODUCT, token=HexAddress("0x000000000000000000000000000000000000f00d"))

    assert vault.get_strategy_tags() is None
