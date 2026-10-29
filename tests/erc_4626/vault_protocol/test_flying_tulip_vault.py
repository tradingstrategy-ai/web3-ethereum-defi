"""No-RPC Flying Tulip adapter classification and metadata tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.classification import _get_hardcoded_protocol_features, create_vault_instance  # noqa: PLC2701
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault import ERC4626HistoricalReader
from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_FT_BY_CHAIN, FLYING_TULIP_SFTUSD_BY_CHAIN, FLYING_TULIP_SHORT_DESCRIPTION, FLYING_TULIP_SSFTUSD_BY_CHAIN, FLYING_TULIP_SSFTUSD_NOTES, FLYING_TULIP_USDC_MINT_REDEEM_FEE_BY_CHAIN
from eth_defi.erc_4626.vault_protocol.flying_tulip.vault import FLYING_TULIP_UNSUPPORTED_FLOW_REASON, FlyingTulipStableVault, FlyingTulipVault
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.curator import identify_curator
from eth_defi.vault.fee import VaultFeeMode, get_vault_fee_mode
from eth_defi.vault.flag import get_notes, is_flagged_vault
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


def test_flying_tulip_short_description_is_plain_text() -> None:
    """Keep Markdown out of the listing summary, which the website renders verbatim."""

    assert "separately claimable FT tokens" in FLYING_TULIP_SHORT_DESCRIPTION
    assert not any(token in FLYING_TULIP_SHORT_DESCRIPTION for token in ("](", "[", "**", "`", "\n"))


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
        assert vault.get_link() == "https://flyingtulip.com/ftusd/"
        assert "Displayed entry and exit fees cover buying ftUSD with USDC" in vault.get_notes()
        assert "[Ethereum FT/ftUSD Curve pool]" in vault.get_notes()
        assert "divided by the average amount staked during the period" in vault.get_notes()
        assert "Withdrawals can be delayed" in vault.get_notes()
        assert vault.short_description == FLYING_TULIP_SHORT_DESCRIPTION
        assert vault.get_historical_reader(stateful=True).uses_contextual_history
        assert vault.get_historical_reader(stateful=True).uses_share_price_equivalence

    assert _get_hardcoded_protocol_features(FLYING_TULIP_SFTUSD_BY_CHAIN[1], 56) is None
    assert _get_hardcoded_protocol_features(FLYING_TULIP_SFTUSD_BY_CHAIN[1]) is None
    assert get_vault_protocol_name({ERC4626Feature.flying_tulip_like}) == "Flying Tulip"


def test_flying_tulip_queue_aware_transaction_support_is_fail_closed() -> None:
    """Do not expose an unsafe synchronous ERC-4626 manager for sftUSD."""

    vault = FlyingTulipVault(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), VaultSpec(1, FLYING_TULIP_SFTUSD_BY_CHAIN[1]))

    assert vault.get_deposit_manager_capability() is None
    with pytest.raises(NotImplementedError, match="queue ID"):
        vault.get_deposit_manager()
    assert FLYING_TULIP_UNSUPPORTED_FLOW_REASON


def test_flying_tulip_strategy_tags_cover_documented_strategy_mix() -> None:
    """Expose Flying Tulip's documented strategy mix and leave BNB untagged."""

    ethereum = FlyingTulipVault(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), VaultSpec(1, FLYING_TULIP_SFTUSD_BY_CHAIN[1]))
    sonic = FlyingTulipVault(SimpleNamespace(eth=SimpleNamespace(chain_id=146)), VaultSpec(146, FLYING_TULIP_SFTUSD_BY_CHAIN[146]))
    bnb = FlyingTulipVault(SimpleNamespace(eth=SimpleNamespace(chain_id=56)), VaultSpec(56, FLYING_TULIP_SFTUSD_BY_CHAIN[56]))

    expected = {
        StrategyTag.carry_trade,
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.multistrategy,
        StrategyTag.options,
    }
    assert ethereum.get_strategy_tags() == expected
    assert sonic.get_strategy_tags() == expected
    assert bnb.get_strategy_tags() is None


def test_flying_tulip_public_metadata_risk_and_fee_classification() -> None:
    """Export complete public protocol metadata with the reviewed classifications."""

    metadata = build_metadata_json(Path("eth_defi/data/vaults/metadata/flying-tulip.yaml"), "https://example.invalid")

    assert metadata["name"] == "Flying Tulip"
    assert metadata["slug"] == "flying-tulip"
    assert metadata["logos"]["light"] == "https://example.invalid/vault-protocol-metadata/flying-tulip/light.png"
    assert "automatically reinvested ftUSD rewards for ssftUSD holders" in metadata["short_description"]
    assert "founded by [Andre Cronje]" in metadata["long_description"]
    assert "$200 million private round" in metadata["long_description"]
    assert "0.07%" in metadata["fee_description"]
    assert "[Ethereum conversion contract]" in metadata["fee_description"]
    assert all(venue in metadata["long_description"] for venue in ("Aave", "Spark", "delta-neutral carry"))
    assert get_vault_risk("Flying Tulip") == VaultTechnicalRisk.severe
    assert get_vault_fee_mode("Flying Tulip", FLYING_TULIP_SFTUSD_BY_CHAIN[1]) == VaultFeeMode.externalised


def test_flying_tulip_stable_routing_and_public_copy() -> None:
    """Keep the auto-compounder under Flying Tulip without FT reward history.

    Classification, notes and curator identity use the reviewed Ethereum
    deployment. The separately maintained notes must not flag the vault.

    :return:
        ``None`` after checking routing and exported metadata inputs.
    """
    address = FLYING_TULIP_SSFTUSD_BY_CHAIN[1]
    features = _get_hardcoded_protocol_features(address, 1)
    assert features == {ERC4626Feature.flying_tulip_stable_like}
    vault = create_vault_instance(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), address, features)
    assert isinstance(vault, FlyingTulipStableVault)
    assert vault.get_protocol_name() == "Flying Tulip"
    assert _get_hardcoded_protocol_features(address, 56) is None
    assert _get_hardcoded_protocol_features(address) is None
    assert identify_curator(1, "ssftUSD", "Stable Staked Flying Tulip USD", address, "flying-tulip") == "flying-tulip"
    assert vault.get_notes() == FLYING_TULIP_SSFTUSD_NOTES
    assert get_notes(address, chain_id=56) is None
    assert not is_flagged_vault(address, protocol_name="Flying Tulip")
    assert "September 2026" in vault.description
    assert "2102404682685501823" in vault.get_notes()
    assert "Holders can withdraw during this period" in vault.get_notes()
    assert vault.description != vault.get_notes()
    assert vault.fetch_scan_record_extra_data() == {}
    assert vault.get_deposit_manager_capability() is None
    assert vault.get_fee_data().fee_mode is VaultFeeMode.externalised
    assert vault.get_fee_data().deposit == 0.0007
    assert vault.get_fee_data().withdraw == 0.0007
    assert vault.get_fee_data().management == 0
    assert vault.get_fee_data().performance == 0
    assert vault.get_strategy_tags() == {StrategyTag.lending, StrategyTag.delta_neutral, StrategyTag.carry_trade, StrategyTag.multistrategy}
    assert vault.get_link() == "https://flyingtulip.com/ftusd/"
    assert not vault.get_historical_reader(False).uses_contextual_history
    assert not vault.get_historical_reader(False).uses_share_price_equivalence


@pytest.mark.parametrize("vault_class,address", [(FlyingTulipVault, FLYING_TULIP_SFTUSD_BY_CHAIN[1]), (FlyingTulipStableVault, FLYING_TULIP_SSFTUSD_BY_CHAIN[1])])
def test_flying_tulip_descriptions_and_strategy_notes(vault_class: type[FlyingTulipVault] | type[FlyingTulipStableVault], address: HexAddress) -> None:
    """Keep strategy explanations accessible and listing text correctly formatted.

    Notes retain the detailed backing strategies for both reward products,
    while descriptions remain single-line and summaries one plain sentence.

    :param vault_class: Adapter for one of the two staking products.
    :param address: Reviewed Ethereum deployment address.
    :return: ``None`` after checking public accessor output.
    """
    vault = vault_class(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), VaultSpec(1, address))
    assert vault.description == normalise_vault_description(vault.description)
    assert vault.short_description == normalise_vault_short_description(vault.short_description)
    assert not any(token in vault.short_description for token in ("](", "[", "**", "`", "\n"))
    assert all(term in vault.get_notes() for term in ("Aave", "Spark", "ETH", "delta-neutral carry", "borrowing costs"))
    assert "#yield-sources)" in vault.get_notes()


def test_flying_tulip_stable_capacity_exclusion_is_local() -> None:
    """Exclude zero-address capacity only for the new auto-compounder.

    Price multicalls retain standard accounting. Disabling the metadata probe
    must avoid a network read and leave the generic reader's policy intact.

    :return:
        ``None`` after checking the local capacity exclusion.
    """
    vault = FlyingTulipStableVault(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), VaultSpec(1, FLYING_TULIP_SSFTUSD_BY_CHAIN[1]))
    reader = vault.get_historical_reader(False)
    assert reader.should_skip_call("maxDeposit")
    assert not reader.should_skip_call("total_assets")
    assert not ERC4626HistoricalReader(vault, stateful=False).should_skip_call("maxDeposit")
    assert vault.fetch_deposit_closed_reason() is None
    with pytest.raises(ValueError, match="Unsupported"):
        FlyingTulipStableVault(SimpleNamespace(), VaultSpec(56, vault.address))
    with pytest.raises(ValueError, match="external FT"):
        FlyingTulipStableVault(SimpleNamespace(), VaultSpec(1, vault.address), features={ERC4626Feature.share_price_equivalence})
