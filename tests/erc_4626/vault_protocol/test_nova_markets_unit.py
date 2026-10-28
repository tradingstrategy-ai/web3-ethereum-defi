"""Nova protocol identity, public metadata and exact deployment isolation."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance, identify_vault_features  # noqa: PLC2701 - classification regression
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.gains.tags import get_strategy_tags as get_gains_tags
from eth_defi.erc_4626.vault_protocol.nova_markets.constants import NOVA_MARKETS_CHAIN_ID, NOVA_POOL_ADDRESS, NOVA_POOL_APP
from eth_defi.erc_4626.vault_protocol.nova_markets.vault import NovaMarketsVault
from eth_defi.feed.sources import load_feeder_metadata
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.flag import VAULT_FLAGS_AND_NOTES, VaultFlag
from eth_defi.vault.protocol_metadata import build_metadata_json
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.strategy_tag import StrategyTag


def test_hardcoded_nova_identity_precedes_generic_gains_probes() -> None:
    """No probe result or name can turn the reviewed Nova pool into gTrade."""
    expected = {ERC4626Feature.nova_markets_like}
    assert identify_vault_features(Web3.to_checksum_address(NOVA_POOL_ADDRESS), {}, "Nova", chain_id=NOVA_MARKETS_CHAIN_ID) == expected
    assert _get_hardcoded_protocol_features(NOVA_POOL_ADDRESS.upper(), NOVA_MARKETS_CHAIN_ID) == expected
    assert _get_hardcoded_protocol_features(NOVA_POOL_ADDRESS, 1) is None
    assert _get_hardcoded_protocol_features(NOVA_POOL_ADDRESS, None) is None
    assert get_vault_protocol_name(expected | {ERC4626Feature.gains_like}) == "Nova Markets"
    expected.clear()
    assert _get_hardcoded_protocol_features(NOVA_POOL_ADDRESS, NOVA_MARKETS_CHAIN_ID) == {ERC4626Feature.nova_markets_like}


def test_factory_uses_nova_even_with_stale_gains_feature() -> None:
    """Factory ordering preserves Nova identity during cached-feature transitions."""
    web3 = Web3()
    web3.eth = Mock(chain_id=NOVA_MARKETS_CHAIN_ID)
    vault = create_vault_instance(web3, NOVA_POOL_ADDRESS, features={ERC4626Feature.nova_markets_like, ERC4626Feature.gains_like})
    assert isinstance(vault, NovaMarketsVault)
    assert vault.get_link() == NOVA_POOL_APP
    assert "gTrade" not in vault.name


def test_nova_notes_and_tags_are_separate_from_gtrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nova has its own strategy, app, operator evidence and warning precedence."""
    vault = NovaMarketsVault(web3=None, spec=VaultSpec(NOVA_MARKETS_CHAIN_ID, NOVA_POOL_ADDRESS), features={ERC4626Feature.nova_markets_like})
    note = vault.get_notes()
    assert "USDC on Arbitrum" in note
    assert "15%" in note
    assert "profitable traders are paid from the pool" in note
    assert "controlled by Nova Markets" in note
    assert "separate from Nova's Lagoon" in note
    assert NOVA_POOL_APP in note
    assert "separately dated launch announcement" in note
    assert vault.get_strategy_tags() == {StrategyTag.amm, StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.market_making_amm, StrategyTag.perpetual_futures}
    assert get_gains_tags(NOVA_MARKETS_CHAIN_ID, NOVA_POOL_ADDRESS) is None
    assert vault.get_deposit_manager_capability().can_redeem is True
    fees = vault.get_fee_data()
    assert fees.fee_mode == VaultFeeMode.feeless
    assert vault.get_risk() == VaultTechnicalRisk.dangerous
    assert fees.can_calculate_investor_net_performance() is True
    assert fees.get_net_fees() == fees
    assert "no management, performance, entry or exit fee" in note
    monkeypatch.setitem(VAULT_FLAGS_AND_NOTES, NOVA_POOL_ADDRESS, (VaultFlag.illiquid, "Manual warning"))
    assert vault.get_notes() == "Manual warning"


def test_nova_protocol_metadata_has_logo_and_canonical_feed() -> None:
    """New protocol metadata reuses the existing official Nova artwork and identity."""
    root = Path(__file__).resolve().parents[3]
    metadata = build_metadata_json(root / "eth_defi/data/vaults/metadata/nova-markets.yaml", "https://example.invalid")
    assert metadata["name"] == "Nova Markets"
    assert metadata["links"]["app"] == NOVA_POOL_APP
    assert metadata["logos"]["light"]
    protocol = load_feeder_metadata(root / "eth_defi/data/feeds/protocols/nova-markets.yaml")
    curator = load_feeder_metadata(root / "eth_defi/data/feeds/curators/nova-markets.yaml")
    assert protocol["twitter"] == "novadotmarkets"
    assert curator["canonical-feeder-id"] == protocol["feeder-id"]
