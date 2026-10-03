"""Automatic strategy classification for the complete Arcus pToken family."""

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.arcus.vault import ArcusVault
from eth_defi.vault.strategy_tag import StrategyTag


@pytest.mark.parametrize("address", ["0x1a596466cb593bee293be8366d9ce493582189c2", "0x5c3b9a9b021e86b54202abcb4580f1f5c271875b", "0x000000000000000000000000000000000000f00d"])
def test_arcus_strategy_tags_cover_products_without_display_overlays(address: HexAddress) -> None:
    """Classify GME products and future family members without display overlays.

    Construct the adapter without RPC reads and ensure callers cannot mutate
    the default classification through a previously returned set.

    :param address:
        Lowercase address of a detected Arcus pToken.
    :return:
        ``None``; validates both tags and independent returned sets.
    """
    vault = object.__new__(ArcusVault)
    vault.__dict__["vault_address"] = address
    tags = vault.get_strategy_tags()
    assert tags == {StrategyTag.directional_leverage, StrategyTag.perpetual_futures}
    tags.clear()
    assert vault.get_strategy_tags() == {StrategyTag.directional_leverage, StrategyTag.perpetual_futures}
