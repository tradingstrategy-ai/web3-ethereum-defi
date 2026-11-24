"""Tests for maintained Lagoon vault strategy classifications."""

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.lagoon.tags import lookup_lagoon_strategy_tags
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag


def create_lagoon_vault(chain_id: int, address: str) -> LagoonVault:
    """Create a minimal Lagoon vault for strategy-tag lookup tests.

    The strategy classification hook only needs the vault specification. This
    avoids constructing Web3 and contract objects in table-driven unit tests.

    :param chain_id:
        Deployment chain ID.

    :param address:
        Vault contract address.

    :return:
        Minimal vault instance suitable for :py:meth:`LagoonVault.get_strategy_tags`.
    """

    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(chain_id=chain_id, vault_address=HexAddress(address))
    return vault


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
    ),
)
def test_kamui_lagoon_strategy_tags(address: str, expected: set[StrategyTag]) -> None:
    """Resolve Kamui mandates case-insensitively and isolate each lookup's result."""

    vault = create_lagoon_vault(1, address)

    tags = lookup_lagoon_strategy_tags(1, address.upper())
    assert tags == expected
    tags.clear()
    assert vault.get_strategy_tags() == expected


def test_reused_lagoon_address_has_distinct_chain_classifications() -> None:
    """Do not conflate the Ethereum and Base vaults at the reused 722 address."""

    address = "0xb09f761cb13baca8ec087ac476647361b6314f98"

    ethereum_tags = create_lagoon_vault(1, address).get_strategy_tags()
    base_tags = create_lagoon_vault(8453, address).get_strategy_tags()

    assert ethereum_tags == {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    }
    assert base_tags == {
        StrategyTag.discretionary_trading,
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.lending_looping,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    }
    assert create_lagoon_vault(10, address).get_strategy_tags() is None
