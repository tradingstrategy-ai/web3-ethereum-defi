"""Tests for reviewed Lagoon vault strategy classifications."""

from eth_defi.erc_4626.vault_protocol.lagoon.tags import LAGOON_STRATEGY_TAGS, get_strategy_tags
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag

EXPECTED_REVIEWED_LAGOON_VAULTS = 52


def _make_vault(chain_id: int, vault_address: str) -> LagoonVault:
    """Create a Lagoon adapter without its RPC-backed constructor."""
    vault = object.__new__(LagoonVault)
    vault.__dict__["spec"] = VaultSpec(chain_id, vault_address)
    return vault


def test_all_reviewed_lagoon_strategy_tags_are_resolved() -> None:
    """Every maintained chain/address mapping resolves to a copied tag set."""
    assert len(LAGOON_STRATEGY_TAGS) == EXPECTED_REVIEWED_LAGOON_VAULTS

    for (chain_id, address), expected_tags in LAGOON_STRATEGY_TAGS.items():
        resolved_tags = get_strategy_tags(chain_id, address.upper())
        assert resolved_tags == set(expected_tags)
        assert resolved_tags is not expected_tags


def test_lagoon_adapter_uses_chain_scoped_strategy_tags() -> None:
    """The adapter distinguishes same-address Lagoon products across chains."""
    ethereum_vault = _make_vault(1, "0xb09f761cb13baca8ec087ac476647361b6314f98")
    base_vault = _make_vault(8453, "0xb09f761cb13baca8ec087ac476647361b6314f98")
    unknown_vault = _make_vault(1, "0x0000000000000000000000000000000000000000")

    assert ethereum_vault.get_strategy_tags() == {
        StrategyTag.amm,
        StrategyTag.arbitrage,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    }
    assert base_vault.get_strategy_tags() == {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.lending_looping,
        StrategyTag.multistrategy,
        StrategyTag.yield_farming,
    }
    assert unknown_vault.get_strategy_tags() is None
