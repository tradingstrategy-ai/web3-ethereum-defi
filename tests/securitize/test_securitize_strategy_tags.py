"""Tests for maintained Securitize fund strategy classifications."""

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.tokenised_fund.securitize.description import BCAP_ETHEREUM, MI4_MANTLE
from eth_defi.tokenised_fund.securitize.vault import SecuritizeVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag


def test_mantle_index_four_strategy_tags() -> None:
    """Mantle Index Four returns its maintained index-fund tag."""
    vault = SecuritizeVault(Web3(), VaultSpec(chain_id=MI4_MANTLE.chain_id, vault_address=MI4_MANTLE.token))

    assert vault.get_strategy_tags() == {StrategyTag.index}


def test_bcap_strategy_tags() -> None:
    """BCAP returns its maintained venture-funding tag."""
    vault = SecuritizeVault(Web3(), VaultSpec(chain_id=BCAP_ETHEREUM.chain_id, vault_address=BCAP_ETHEREUM.token))

    assert vault.get_strategy_tags() == {StrategyTag.venture_funding}


def test_unmapped_securitize_strategy_tags_return_none() -> None:
    """Unmapped Securitize products retain the missing-information distinction."""
    vault = SecuritizeVault(
        Web3(),
        VaultSpec(
            chain_id=MI4_MANTLE.chain_id,
            vault_address=HexAddress("0x0000000000000000000000000000000000000000"),
        ),
    )

    assert vault.get_strategy_tags() is None
