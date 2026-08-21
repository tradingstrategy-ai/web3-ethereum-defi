"""Tests for reviewed Lagoon vault strategy classifications."""

from eth_defi.erc_4626.vault_protocol.lagoon.tags import LAGOON_STRATEGY_TAGS, get_strategy_tags
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag

EXPECTED_REVIEWED_LAGOON_VAULTS = 51


def _make_vault(chain_id: int, vault_address: str) -> LagoonVault:
    """Create a Lagoon adapter without its RPC-backed constructor."""
    vault = object.__new__(LagoonVault)
    vault.__dict__["spec"] = VaultSpec(chain_id, vault_address)
    return vault


def test_all_reviewed_lagoon_strategy_tags_are_resolved() -> None:
    """Every maintained address mapping resolves to a copied tag set."""
    assert len(LAGOON_STRATEGY_TAGS) == EXPECTED_REVIEWED_LAGOON_VAULTS

    for address, expected_tags in LAGOON_STRATEGY_TAGS.items():
        resolved_tags = get_strategy_tags(address.upper())
        assert resolved_tags == set(expected_tags)
        assert resolved_tags is not expected_tags


def test_lagoon_adapter_uses_address_strategy_tags() -> None:
    """The adapter uses the shared address mapping on every deployment."""
    ethereum_vault = _make_vault(1, "0xb09f761cb13baca8ec087ac476647361b6314f98")
    base_vault = _make_vault(8453, "0xb09f761cb13baca8ec087ac476647361b6314f98")
    unknown_vault = _make_vault(1, "0x0000000000000000000000000000000000000000")

    expected_tags = {
        StrategyTag.amm,
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.lending_looping,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
        StrategyTag.yield_farming,
    }
    assert ethereum_vault.get_strategy_tags() == expected_tags
    assert base_vault.get_strategy_tags() == expected_tags
    assert unknown_vault.get_strategy_tags() is None
