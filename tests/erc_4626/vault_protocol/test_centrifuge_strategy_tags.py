"""Tests for maintained Centrifuge vault strategy classifications."""

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.centrifuge.vault import CentrifugeVault
from eth_defi.vault.strategy_tag import StrategyTag


def test_janus_henderson_spxa_strategy_tags() -> None:
    """SPXA returns its index and real-world-asset tags."""
    vault = object.__new__(CentrifugeVault)
    vault.vault_address = HexAddress("0x99e9092bae6d4394e54034ecb1e45441678323b9")

    assert vault.get_strategy_tags() == {StrategyTag.index, StrategyTag.rwa}


def test_unmapped_centrifuge_strategy_tags_return_none() -> None:
    """Unclassified Centrifuge vaults preserve the missing-information value."""
    vault = object.__new__(CentrifugeVault)
    vault.vault_address = HexAddress("0x000000000000000000000000000000000000f00d")

    assert vault.get_strategy_tags() is None
