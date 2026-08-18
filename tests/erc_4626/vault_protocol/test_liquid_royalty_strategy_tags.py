"""Tests for Liquid Royalty strategy classifications."""

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.liquid_royalty.vault import LiquidRoyaltyVault
from eth_defi.vault.strategy_tag import StrategyTag


def test_alar_sailout_royalty_strategy_tags() -> None:
    """ALAR SailOut Royalty is classified as a real-world royalty stream."""
    vault = object.__new__(LiquidRoyaltyVault)
    vault.vault_address = HexAddress("0x09cea16a2563c2d7d807c86f5b8da760389b5915")

    assert vault.get_strategy_tags() == {StrategyTag.rwa_royalties}


def test_unmapped_liquid_royalty_strategy_tags_are_missing() -> None:
    """Other Liquid Royalty addresses remain unclassified until reviewed."""
    vault = object.__new__(LiquidRoyaltyVault)
    vault.vault_address = HexAddress("0x000000000000000000000000000000000000f00d")

    assert vault.get_strategy_tags() is None
