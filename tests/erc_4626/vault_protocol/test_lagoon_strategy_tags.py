"""Tests for maintained Lagoon vault strategy classifications."""

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.vault.strategy_tag import StrategyTag


@pytest.mark.parametrize(
    ("address", "expected"),
    (
        (
            "0xcda323c2df692d989b24ba51d0acca924cf9a344",
            {StrategyTag.money_market_fund, StrategyTag.rwa},
        ),
        (
            "0xa5ae405242f42c47996a0c6857ff10a77f9bdee6",
            {StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.rwa_credit},
        ),
        (
            "0x9e0db8f43bb91e2148b0db920e21370525cf3aab",
            {StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.rwa_credit},
        ),
    ),
)
def test_kamui_lagoon_strategy_tags(address: str, expected: set[StrategyTag]) -> None:
    """Return the documented strategy tags for each Kamui vault."""

    vault = object.__new__(LagoonVault)
    vault.vault_address = HexAddress(address)

    assert vault.get_strategy_tags() == expected


def test_unmapped_lagoon_strategy_tags_return_none() -> None:
    """Unmapped Lagoon vaults retain the missing-information distinction."""

    vault = object.__new__(LagoonVault)
    vault.vault_address = HexAddress("0x0000000000000000000000000000000000000000")

    assert vault.get_strategy_tags() is None
