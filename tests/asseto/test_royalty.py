"""Asseto IP Vault routing, review status and safe metadata migration."""

# Exact fixture counts and unchanged mock callback signatures are intentional.
# ruff: noqa: PLR2004

import datetime
from dataclasses import replace
from types import SimpleNamespace

import pytest
from web3 import Web3

from eth_defi.erc_4626 import classification
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, passes_price_scan_activity_filter
from eth_defi.erc_4626.discovery_base import DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
from eth_defi.tokenised_fund.asseto import royalty_migration
from eth_defi.tokenised_fund.asseto.royalty import AssetoRoyaltyVault
from eth_defi.tokenised_fund.asseto.royalty_constants import ROYALTY_CHAIN_ID, ROYALTY_DEPLOYED_AT, ROYALTY_DEPLOYMENT_BLOCK, ROYALTY_HARDCODED_LEADS, ROYALTY_VAULT_ADDRESS
from eth_defi.tokenised_fund.asseto.tags import STRATEGY_TAGS
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import get_vault_fee_mode
from eth_defi.vault.flag import VaultFlag, get_notes
from eth_defi.vault.risk import get_vault_risk
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def vault() -> AssetoRoyaltyVault:
    """Create the actual adapter with a connection that cannot make RPC calls."""
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=ROYALTY_CHAIN_ID, block_number=30_000_000))
    return classification.create_vault_instance_autodetect(web3, ROYALTY_VAULT_ADDRESS)


def test_royalty_hardcoded_routing_and_review(vault: AssetoRoyaltyVault) -> None:
    """Classify only the proxy on Soneium and keep its assessment unverified."""
    assert isinstance(vault, AssetoRoyaltyVault)
    assert vault.features == {ERC4626Feature.asseto_royalty_like}
    assert vault.get_protocol_name() == "Asseto"
    assert classification._get_hardcoded_protocol_features(ROYALTY_VAULT_ADDRESS, chain_id=1) is None
    assert classification._get_hardcoded_protocol_features(ROYALTY_VAULT_ADDRESS, chain_id=None) is None
    assert classification._get_hardcoded_protocol_features("0x8CfDe91D3a4c55b426732f17dF1745E6d0603e73", chain_id=ROYALTY_CHAIN_ID) is None
    assert VaultFlag.review_needed in vault.get_flags()
    assert "still under verification" in vault.get_notes()
    assert vault.get_notes() == get_notes(ROYALTY_VAULT_ADDRESS, chain_id=ROYALTY_CHAIN_ID, protocol_name="Asseto")
    assert "https://x.com/soneium/status/2114999606400692706" in vault.get_notes()
    assert "Still under verification" in vault.short_description
    assert vault.get_risk() is None
    assert get_vault_risk("Asseto", ROYALTY_VAULT_ADDRESS) is None
    assert get_vault_fee_mode("Asseto", ROYALTY_VAULT_ADDRESS) is None
    assert vault.get_fee_data().management is None
    assert vault.get_fee_data().withdraw is None
    assert vault.has_custom_fees()
    assert not vault.supports_generic_deposit_manager()
    assert vault.get_deposit_manager_capability() is None
    with pytest.raises(NotImplementedError, match="under verification"):
        vault.get_deposit_manager()
    assert not vault.has_block_range_event_support()
    assert vault.get_strategy_tags() == {StrategyTag.rwa, StrategyTag.rwa_royalties}
    vault.get_strategy_tags().clear()
    assert vault.get_strategy_tags() == {StrategyTag.rwa, StrategyTag.rwa_royalties}
    assert lookup_strategy_tags(STRATEGY_TAGS, "0x0000000000000000000000000000000000000001") is None


def test_royalty_batch_classification_makes_no_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The discovery batch must not probe the registered RoyaltyVault."""

    def reject_probes(*args: object, **kwargs: object) -> None:
        del args, kwargs
        message = "RoyaltyVault classification must not probe contracts"
        raise AssertionError(message)

    monkeypatch.setattr(classification, "create_probe_calls", reject_probes)
    result = list(classification.probe_vaults(ROYALTY_CHAIN_ID, None, [Web3.to_checksum_address(ROYALTY_VAULT_ADDRESS)], block_identifier=30_000_000))
    assert len(result) == 1
    assert result[0].features == {ERC4626Feature.asseto_royalty_like}
    assert ("Asseto IP Vault", ROYALTY_HARDCODED_LEADS) in DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
    detection = ERC4262VaultDetection(chain=ROYALTY_CHAIN_ID, address=ROYALTY_VAULT_ADDRESS, features=result[0].features, first_seen_at_block=ROYALTY_DEPLOYMENT_BLOCK, first_seen_at=ROYALTY_DEPLOYED_AT, updated_at=ROYALTY_DEPLOYED_AT, deposit_count=0, redeem_count=0)
    assert passes_price_scan_activity_filter(detection, min_deposit_threshold=5)


def test_royalty_unknown_oracle_status_is_not_a_price(vault: AssetoRoyaltyVault) -> None:
    """Missing freshness evidence must not create a fresh valuation row."""
    reader = vault.get_historical_reader(stateful=False)
    result = reader.process_result(30_000_000, datetime.datetime(2026, 10, 27, tzinfo=datetime.UTC).replace(tzinfo=None), [])
    assert result.share_price is None
    assert result.total_assets is None
    assert result.errors == ["IP Vault RWA oracle freshness is stale or unavailable"]


def test_royalty_metadata_migration_preserves_existing_rows(vault: AssetoRoyaltyVault, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dry run, creation and refresh preserve other rows and discovery history."""
    database = VaultDatabase()
    unrelated_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    unrelated = {"Name": "Untouched", "custom": 123}
    database.rows[unrelated_spec] = unrelated

    def build_record(web3: Web3, detection: ERC4262VaultDetection, block_identifier: int, token_cache: dict | None) -> dict:
        del web3, block_identifier, token_cache
        return {"Protocol": "Asseto", "Symbol": "sIPUSD", "_flags": {VaultFlag.review_needed}, "_detection_data": detection}

    monkeypatch.setattr(royalty_migration, "create_vault_scan_record", build_record)
    row = royalty_migration.migrate_royalty_metadata(database, vault.web3)
    assert database.rows == {unrelated_spec: unrelated}
    assert row["_detection_data"].first_seen_at_block == ROYALTY_DEPLOYMENT_BLOCK
    assert row["_detection_data"].deposit_count == 0
    royalty_migration.migrate_royalty_metadata(database, vault.web3, dry_run=False)
    existing = database.rows[vault.spec]
    existing["custom"] = "preserve"
    existing["_detection_data"] = replace(existing["_detection_data"], deposit_count=12, redeem_count=3)
    updated = royalty_migration.migrate_royalty_metadata(database, vault.web3, dry_run=False)
    assert updated["custom"] == "preserve"
    assert updated["_detection_data"].deposit_count == 12
    assert updated["_detection_data"].redeem_count == 3
    assert len(database.rows) == 2
    assert database.rows[unrelated_spec] is unrelated
