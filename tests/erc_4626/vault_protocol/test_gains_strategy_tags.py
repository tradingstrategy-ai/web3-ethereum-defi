"""Tests for maintained Gains Network gTrade vault strategy classifications."""

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gains.tags import STRATEGY_TAGS
from eth_defi.erc_4626.vault_protocol.gains.vault import GTRADE_VAULT_ADDRESSES, GainsVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag

#: Current maintained Gains Network gTrade vault addresses.
GTRADE_DATABASE_ADDRESSES = {
    "0xad20523a7dc37babc1cc74897e4977232b3d02e5",
    "0xb7cb7cb7c3cd96e251c9bf8800b9631134bbadc6",
    "0x31297b564fb8ec52a7d84cc2dee437e0992ef2b8",
    "0x91993f2101cc758d0deb7279d41e880f7defe827",
    "0x1544e1ff1a6f6bdbfb901622c12bb352a43464fb",
    "0x29019fe2e72e8d4d2118e8d0318bef389ffe2c81",
    "0x6a6e4ad4a5ca14b940cd6949b1a90f947ae21c19",
    "0xdd560bc2c98bc3fa39fcafe256249707f9b83b3c",
    "0xfe3e29b3328026003a15bf0846846b03af86b537",
    "0xd85e038593d7a098614721eae955ec2022b9b91b",
    "0xf40808f50b8d858f3ac6d10c441bb61da4564d53",
    "0x992eb7040b66b13abea94e2621d4e61d5ce608bd",
    "0x5977a9682d7af81d347cfc338c61692163a2784c",
    "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0",
    "0x4beef1113f968326905224d2ca272f3032a9a9f4",
    "0xd796a9e7e30bfc1b1a9380f501430f681c31eb78",
    "0xfb34af2138280e13b0759fd322fe63fccc7508a6",
    "0x28e1afcd2d91a7f0ea49e81192599fbe1e700169",
    "0xd78bd3aef2e8aa7820fea8ffb33eddc4f13fa933",
    "0x1e98b6143a4eaf78ab63de8ea8186eec3dbe5edc",
    "0x46344456f130e9dcdea7f98cdb0e02fb9f4ab72d",
    "0xb7058370db10f0712eddb297bc3a58c3a2e5c3a7",
}


def make_gtrade_vault(address: str) -> GainsVault:
    """Create a Gains adapter without making JSON-RPC calls."""
    return GainsVault(
        web3=None,
        spec=VaultSpec(chain_id=42161, vault_address=address),
        features={ERC4626Feature.gains_like},
    )


def test_gtrade_database_addresses_are_all_classified() -> None:
    """Every Gains Network row found in the vault database has a mapping."""
    assert set(STRATEGY_TAGS) == GTRADE_DATABASE_ADDRESSES
    assert GTRADE_VAULT_ADDRESSES <= set(STRATEGY_TAGS)

    expected = {
        StrategyTag.amm,
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
        StrategyTag.market_making_amm,
        StrategyTag.perpetual_futures,
    }
    assert all(tags == expected for tags in STRATEGY_TAGS.values())


@pytest.mark.parametrize("address", sorted(GTRADE_DATABASE_ADDRESSES))
def test_gtrade_vault_returns_maintained_tags(address: str) -> None:
    """Gains adapters expose the per-address gTrade classifications."""
    vault = make_gtrade_vault(address)

    assert vault.get_strategy_tags() == STRATEGY_TAGS[address]
    assert StrategyTag.market_making_amm in vault.get_strategy_tags()


def test_unmapped_gains_like_vault_returns_none() -> None:
    """An unclassified Gains-like address preserves the missing distinction."""
    vault = make_gtrade_vault("0x000000000000000000000000000000000000f00d")

    assert vault.get_strategy_tags() is None
