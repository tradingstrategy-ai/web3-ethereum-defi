"""No-RPC Flying Tulip adapter classification and metadata tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_FT_BY_CHAIN, FLYING_TULIP_SFTUSD_BY_CHAIN, FLYING_TULIP_USDC_MINT_REDEEM_FEE_BY_CHAIN
from eth_defi.erc_4626.vault_protocol.flying_tulip.vault import FLYING_TULIP_UNSUPPORTED_FLOW_REASON, FlyingTulipVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import VaultFeeMode, get_vault_fee_mode
from eth_defi.vault.protocol_metadata import build_metadata_json
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk
from eth_defi.vault.strategy_tag import StrategyTag


def test_flying_tulip_usdc_equivalent_fee_schedule_is_chain_specific() -> None:
    """Keep the reviewed USDC acquisition and exit fees explicit per chain."""

    assert FLYING_TULIP_USDC_MINT_REDEEM_FEE_BY_CHAIN == {
        1: 0.0007,
        56: 0.001,
        146: 0.0007,
    }


def test_flying_tulip_official_proxies_are_chain_aware_and_route_to_adapter() -> None:
    """Classify only the reviewed chain/address pairs as Flying Tulip sftUSD."""

    for chain_id, address in FLYING_TULIP_SFTUSD_BY_CHAIN.items():
        features = _get_hardcoded_protocol_features(address, chain_id)
        assert features == {ERC4626Feature.flying_tulip_like, ERC4626Feature.share_price_equivalence}
        vault = create_vault_instance(SimpleNamespace(eth=SimpleNamespace(chain_id=chain_id)), address, features)
        assert isinstance(vault, FlyingTulipVault)
        assert vault.fetch_reward_token_address(0) == FLYING_TULIP_FT_BY_CHAIN[chain_id]
        assert vault.get_deposit_fee(0) == FLYING_TULIP_USDC_MINT_REDEEM_FEE_BY_CHAIN[chain_id]
        assert vault.get_withdraw_fee(0) == FLYING_TULIP_USDC_MINT_REDEEM_FEE_BY_CHAIN[chain_id]
        fee_data = vault.get_fee_data()
        assert fee_data.fee_mode is VaultFeeMode.externalised
        assert fee_data.internalised is False
        assert fee_data.get_net_fees().deposit == FLYING_TULIP_USDC_MINT_REDEEM_FEE_BY_CHAIN[chain_id]
        assert fee_data.get_net_fees().withdraw == FLYING_TULIP_USDC_MINT_REDEEM_FEE_BY_CHAIN[chain_id]
        assert "USDC" in vault.get_notes()
        assert "[verified Ethereum MintAndRedeem contract]" in vault.get_notes()
        assert vault.get_historical_reader(stateful=True).uses_contextual_history
        assert vault.get_historical_reader(stateful=True).uses_share_price_equivalence

    assert _get_hardcoded_protocol_features(FLYING_TULIP_SFTUSD_BY_CHAIN[1], 56) is None
    assert get_vault_protocol_name({ERC4626Feature.flying_tulip_like}) == "Flying Tulip"


def test_flying_tulip_queue_aware_transaction_support_is_fail_closed() -> None:
    """Do not expose an unsafe synchronous ERC-4626 manager for sftUSD."""

    vault = FlyingTulipVault(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), VaultSpec(1, FLYING_TULIP_SFTUSD_BY_CHAIN[1]))

    assert vault.get_deposit_manager_capability() is None
    with pytest.raises(NotImplementedError, match="queue ID"):
        vault.get_deposit_manager()
    assert FLYING_TULIP_UNSUPPORTED_FLOW_REASON


def test_flying_tulip_strategy_tags_are_evidence_scoped() -> None:
    """Expose only the documented current strategy and leave BNB untagged."""

    ethereum = FlyingTulipVault(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), VaultSpec(1, FLYING_TULIP_SFTUSD_BY_CHAIN[1]))
    sonic = FlyingTulipVault(SimpleNamespace(eth=SimpleNamespace(chain_id=146)), VaultSpec(146, FLYING_TULIP_SFTUSD_BY_CHAIN[146]))
    bnb = FlyingTulipVault(SimpleNamespace(eth=SimpleNamespace(chain_id=56)), VaultSpec(56, FLYING_TULIP_SFTUSD_BY_CHAIN[56]))

    assert ethereum.get_strategy_tags() == {StrategyTag.lending}
    assert sonic.get_strategy_tags() == {StrategyTag.lending}
    assert bnb.get_strategy_tags() is None


def test_flying_tulip_public_metadata_risk_and_fee_classification() -> None:
    """Export complete public protocol metadata with the reviewed classifications."""

    metadata = build_metadata_json(Path("eth_defi/data/vaults/metadata/flying-tulip.yaml"), "https://example.invalid")

    assert metadata["name"] == "Flying Tulip"
    assert metadata["slug"] == "flying-tulip"
    assert metadata["logos"]["light"] == "https://example.invalid/vault-protocol-metadata/flying-tulip/light.png"
    assert "variable" in metadata["short_description"]
    assert "stablecoin-peg" in metadata["long_description"]
    assert "0.07%" in metadata["fee_description"]
    assert "[verified MintAndRedeem contract]" in metadata["fee_description"]
    assert get_vault_risk("Flying Tulip") == VaultTechnicalRisk.severe
    assert get_vault_fee_mode("Flying Tulip", FLYING_TULIP_SFTUSD_BY_CHAIN[1]) == VaultFeeMode.externalised
