"""Tests for maintained Lagoon vault strategy classifications."""

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.lagoon.tags import CHAIN_STRATEGY_TAGS, STRATEGY_TAGS, lookup_lagoon_strategy_tags
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
        (
            "0x9e0db8f43bb91e2148b0db920e21370525cf3aab",
            {StrategyTag.multistrategy, StrategyTag.rwa, StrategyTag.rwa_credit},
        ),
    ),
)
def test_kamui_lagoon_strategy_tags(address: str, expected: set[StrategyTag]) -> None:
    """Return the documented strategy tags for each Kamui vault."""

    vault = create_lagoon_vault(1, address)

    assert vault.get_strategy_tags() == expected


@pytest.mark.parametrize(("address", "expected"), tuple(STRATEGY_TAGS.items()))
def test_all_address_scoped_lagoon_strategy_tags(address: str, expected: set[StrategyTag]) -> None:
    """Expose every maintained address-scoped classification through the hook.

    The returned set must be a copy so callers cannot mutate the maintained
    module-level table.
    """

    vault = create_lagoon_vault(1, address)
    actual = vault.get_strategy_tags()

    assert actual == expected
    assert actual is not expected


@pytest.mark.parametrize(("chain_id", "address", "expected"), tuple((chain_id, address, tags) for (chain_id, address), tags in CHAIN_STRATEGY_TAGS.items()))
def test_chain_scoped_lagoon_strategy_tags(chain_id: int, address: str, expected: set[StrategyTag]) -> None:
    """Keep reused Lagoon addresses isolated by deployment chain."""

    vault = create_lagoon_vault(chain_id, address)

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
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.lending_looping,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
    }


def test_chain_scoped_classification_does_not_leak_to_other_chain() -> None:
    """Leave an unrelated same-address Ethereum deployment unclassified."""

    address = "0x8092ca384d44260ea4feaf7457b629b8dc6f88f0"

    assert create_lagoon_vault(8453, address).get_strategy_tags() == CHAIN_STRATEGY_TAGS[8453, address]
    assert create_lagoon_vault(1, address).get_strategy_tags() is None


@pytest.mark.parametrize(
    ("chain_id", "address"),
    (
        (1, "0x22f99228f3ba7cfc7189ddf14366970fe0cef0cb"),
        (1, "0x23b27310451f2754de34d9c04aa24e8be367124a"),
        (1, "0xba6cfe8a9d199cd7f3e50114c4e4ec66f2d52c87"),
        (1, "0xef39d77c7fb6224ac974c5fa4e3151a6c6ce9594"),
        (1, "0xf10801bcc3deaf467fb8b3dbb7430111822e6dab"),
        (1, "0xfd104766499a3ff60ea85b5c6015ba9e32b8c891"),
        (8453, "0x02cf8d7863bc03d32fa18abba754fc300239d8f6"),
        (8453, "0xe051aec08361694bdcee7ae6d4f3783f12bf601e"),
        (42161, "0x1723cb57af58efb35a013870c90fcc3d60174a4e"),
        (42161, "0xc047d64dafe9e6ac76508835c17c6719f9278c1c"),
        (59144, "0x1b316fa2d6c44b65c1ed6d29b37743cd362f0f71"),
        (59144, "0x7df7e45ab573ace8f872b5d5a1689af7ff1a07f7"),
    ),
)
def test_insufficient_evidence_lagoon_vaults_remain_unmapped(chain_id: int, address: str) -> None:
    """Preserve ``None`` for reviewed vaults without exact strategy evidence."""

    assert create_lagoon_vault(chain_id, address).get_strategy_tags() is None


def test_lagoon_strategy_tag_keys_are_normalised() -> None:
    """Keep address keys lowercase and collision overrides out of the global table."""

    assert all(address == address.lower() for address in STRATEGY_TAGS)
    assert all(address == address.lower() for _, address in CHAIN_STRATEGY_TAGS)
    assert STRATEGY_TAGS.keys().isdisjoint(address for _, address in CHAIN_STRATEGY_TAGS)


def test_unmapped_lagoon_strategy_tags_return_none() -> None:
    """Unmapped Lagoon vaults retain the missing-information distinction."""

    vault = create_lagoon_vault(1, "0x0000000000000000000000000000000000000000")

    assert vault.get_strategy_tags() is None


def test_lagoon_strategy_lookup_returns_a_copy() -> None:
    """Keep the maintained source tables immutable through direct lookups."""

    address, maintained_tags = next(iter(STRATEGY_TAGS.items()))
    returned_tags = lookup_lagoon_strategy_tags(1, address)
    assert returned_tags is not None

    returned_tags.clear()

    assert STRATEGY_TAGS[address] == maintained_tags
