"""Tests for maintained vault strategy classifications."""

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.atoma.vault import ATOMA_VAULT_2_ADDRESS, ATOMA_VAULT_ADDRESS, AtomaVault
from eth_defi.erc_4626.vault_protocol.yieldnest.vault import YNRWAX_VAULT_ADDRESS, YieldNestVault
from eth_defi.vault.base import VaultBase
from eth_defi.vault.strategy_tag import StrategyTag


def test_atoma_strategy_tags() -> None:
    """Atoma's reviewed strategies return their maintained address tags."""
    vault = object.__new__(AtomaVault)
    vault.vault_address = ATOMA_VAULT_ADDRESS

    tags = vault.get_strategy_tags()

    assert tags == {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
    }

    assert tags is not None
    tags.add(StrategyTag.carry_trade)
    assert vault.get_strategy_tags() == {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
    }

    vault.vault_address = ATOMA_VAULT_2_ADDRESS
    assert vault.get_strategy_tags() == {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
        StrategyTag.rwa,
    }


def test_yieldnest_strategy_tags() -> None:
    """YieldNest's RWA lending vault returns both relevant strategy tags."""
    vault = object.__new__(YieldNestVault)
    vault.vault_address = YNRWAX_VAULT_ADDRESS

    assert vault.get_strategy_tags() == {StrategyTag.rwa, StrategyTag.rwa_lending}


def test_missing_strategy_tags_return_none() -> None:
    """Unmapped vaults retain the missing-information distinction."""
    vault = object.__new__(AtomaVault)
    vault.vault_address = HexAddress("0x0000000000000000000000000000000000000000")

    assert vault.get_strategy_tags() is None
    assert VaultBase.get_strategy_tags(object.__new__(VaultBase)) is None
