"""Test T3tris migration-pool lead discovery."""

import dataclasses
import datetime
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626 import discovery_base as discovery_base_module
from eth_defi.erc_4626.classification import VaultFeatureProbe
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, is_activity_filter_exempt, passes_price_scan_activity_filter
from eth_defi.erc_4626.discovery_base import DEFAULT_HARDCODED_VAULT_LEAD_SOURCES, LeadScanReport, PotentialVaultMatch, VaultDiscoveryBase, VaultEventKind, get_t3tris_vault_configuration_discovery_events, get_vault_discovery_events, get_vault_event_topic_map
from eth_defi.erc_4626.vault_protocol.t3tris.constants import ARBITRUM_CHAIN_ID, STRADA_YIELD_ARBITRUM_ADDRESS, STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT, STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT_BLOCK, T3TRIS_HARDCODED_LEADS


class DummyT3trisDiscovery(VaultDiscoveryBase):
    """Minimal backend for testing the configured T3tris migration lead."""

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=ARBITRUM_CHAIN_ID))
    web3factory = object()

    def fetch_leads(self, _start_block: int, _end_block: int, _display_progress: bool = True) -> LeadScanReport:
        """Return no event-derived leads so the registry injection is isolated."""
        return LeadScanReport()


def test_t3tris_configuration_topics_seed_discovery_candidates() -> None:
    """Treat T3tris setup events as candidate leads when migration skips flows."""
    web3 = Web3()
    configuration_events = get_t3tris_vault_configuration_discovery_events(web3)
    topic_map = get_vault_event_topic_map(web3)
    all_topics = {get_topic_signature_from_event(event) for event in get_vault_discovery_events(web3)}

    configuration_topics = {get_topic_signature_from_event(event) for event in configuration_events}
    assert [event.event_name for event in configuration_events] == ["T3treasuryUpdated"]
    assert configuration_topics <= all_topics
    assert all(topic_map[topic] == VaultEventKind.configuration for topic in configuration_topics)

    lead = PotentialVaultMatch(
        chain=ARBITRUM_CHAIN_ID,
        address=STRADA_YIELD_ARBITRUM_ADDRESS,
        first_seen_at_block=STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT_BLOCK,
        first_seen_at=STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT,
        configuration_count=1,
    )
    assert lead.is_candidate()


def test_t3tris_strada_yield_is_a_hardcoded_lead(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retain the known migration-pool vault without relying on activity logs."""

    def fake_probe_vaults(chain: int, web3factory: object, addresses: list[str], **kwargs: object) -> Iterator[VaultFeatureProbe]:
        """Yield the expected T3tris feature for the reviewed lead."""
        assert chain == ARBITRUM_CHAIN_ID
        assert web3factory is DummyT3trisDiscovery.web3factory
        assert addresses == [STRADA_YIELD_ARBITRUM_ADDRESS]
        yield VaultFeatureProbe(address=STRADA_YIELD_ARBITRUM_ADDRESS, features={ERC4626Feature.t3tris_like})

    assert ("T3tris", T3TRIS_HARDCODED_LEADS) in DEFAULT_HARDCODED_VAULT_LEAD_SOURCES
    assert T3TRIS_HARDCODED_LEADS == (
        (
            ARBITRUM_CHAIN_ID,
            STRADA_YIELD_ARBITRUM_ADDRESS,
            STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT_BLOCK,
            datetime.datetime(2026, 7, 14, 18, 57, 55, tzinfo=datetime.UTC).replace(tzinfo=None),
        ),
    )
    monkeypatch.setattr(discovery_base_module, "probe_vaults", fake_probe_vaults)
    report = DummyT3trisDiscovery(max_workers=1).scan_vaults(
        0,
        STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT_BLOCK,
        display_progress=False,
        hardcoded_lead_sources=(("T3tris", T3TRIS_HARDCODED_LEADS),),
    )
    assert report.new_leads == 1
    assert report.detections[STRADA_YIELD_ARBITRUM_ADDRESS].features == {ERC4626Feature.t3tris_like}
    assert report.detections[STRADA_YIELD_ARBITRUM_ADDRESS].configuration_count == 1


def test_t3tris_migration_lead_needs_configuration_event_for_missing_flow_events() -> None:
    """Allow zero deposits only after a T3tris configuration event was recorded."""
    detection = ERC4262VaultDetection(
        chain=ARBITRUM_CHAIN_ID,
        address=STRADA_YIELD_ARBITRUM_ADDRESS,
        features={ERC4626Feature.t3tris_like},
        first_seen_at_block=STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT_BLOCK,
        first_seen_at=STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT,
        updated_at=STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT,
        deposit_count=0,
        redeem_count=0,
        configuration_count=1,
    )

    assert is_activity_filter_exempt(detection) is False
    assert passes_price_scan_activity_filter(detection, min_deposit_threshold=5) is True

    missing_configuration = dataclasses.replace(detection, configuration_count=0)
    assert passes_price_scan_activity_filter(missing_configuration, min_deposit_threshold=5) is False
