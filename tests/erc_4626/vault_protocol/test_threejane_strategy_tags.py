"""Tests for 3Jane strategy classifications."""

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.threejane.tags import THREEJANE_STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.threejane.vault import SUSD3_ADDRESS, ThreeJaneVault
from eth_defi.vault.strategy_tag import STRATEGY_TAG_METADATA, StrategyTag

#: USD3 senior tranche on Ethereum.
USD3_ADDRESS = "0x056b269eb1f75477a8666ae8c7fe01b64dd55ecc"


@pytest.mark.parametrize("address", [USD3_ADDRESS, SUSD3_ADDRESS])
def test_threejane_tranche_strategy_tags(address: str) -> None:
    """Both tranches are classified as real-world lending including inventory financing."""
    vault = object.__new__(ThreeJaneVault)
    vault.vault_address = HexAddress(address)

    tags = vault.get_strategy_tags()

    assert tags == {StrategyTag.lending, StrategyTag.rwa, StrategyTag.rwa_lending, StrategyTag.inventory_financing}
    assert tags == set(THREEJANE_STRATEGY_TAGS)
    tags.clear()
    assert vault.get_strategy_tags() == set(THREEJANE_STRATEGY_TAGS)


def test_threejane_checksummed_address_lookup() -> None:
    """Lookups normalise a checksummed address to the lowercase mapping key."""
    vault = object.__new__(ThreeJaneVault)
    vault.vault_address = HexAddress("0x056B269Eb1f75477a8666ae8C7fE01b64dD55eCc")

    assert vault.get_strategy_tags() == set(THREEJANE_STRATEGY_TAGS)


def test_unmapped_threejane_strategy_tags_are_missing() -> None:
    """Other addresses remain unclassified until reviewed."""
    vault = object.__new__(ThreeJaneVault)
    vault.vault_address = HexAddress("0x000000000000000000000000000000000000f00d")

    assert vault.get_strategy_tags() is None


def test_inventory_financing_tag_metadata() -> None:
    """The new category has a plain-language label and summary."""
    metadata = STRATEGY_TAG_METADATA[StrategyTag.inventory_financing]

    assert metadata["label"] == "Inventory financing"
    assert "stock" in metadata["description"]
