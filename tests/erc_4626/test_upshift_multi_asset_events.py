"""Test Upshift multi-asset event discovery."""

import datetime

from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, is_activity_filter_exempt
from eth_defi.erc_4626.discovery_base import (
    PotentialVaultMatch,
    VaultEventKind,
    get_standard_erc_4626_vault_discovery_events,
    get_upshift_multi_asset_discovery_events,
    get_vault_discovery_events,
    get_vault_event_topic_map,
)


def _native_datetime(year: int, month: int, day: int) -> datetime.datetime:
    """Create a fixed naive UTC datetime for lead metadata."""
    return datetime.datetime(year, month, day, tzinfo=datetime.timezone.utc).replace(tzinfo=None)


def test_upshift_multi_asset_deposit_topic_is_deposit():
    """Verify the Upshift multi-asset deposit event topic.

    Upshift ``multiAssetVault`` contracts emit a custom deposit event whose
    first argument is the deposited asset. This topic must be treated as a
    deposit-like vault lead event by both JSON-RPC and Hypersync discovery.
    """
    web3 = Web3()
    upshift_events = get_upshift_multi_asset_discovery_events(web3)
    topic_map = get_vault_event_topic_map(web3)

    deposit_topic = get_topic_signature_from_event(upshift_events[0])

    assert deposit_topic == "0xc436f473cd90c9b4dd731856a14b80f713d384a1688a506d4230140c5b36d5cd"
    assert topic_map[deposit_topic] == VaultEventKind.deposit


def test_upshift_multi_asset_deposit_topic_is_distinct_from_standard_erc4626():
    """Verify Upshift multi-asset deposits cannot collide with ERC-4626 deposits."""
    web3 = Web3()
    erc4626_events = get_standard_erc_4626_vault_discovery_events(web3)
    upshift_events = get_upshift_multi_asset_discovery_events(web3)

    erc4626_deposit_topic = get_topic_signature_from_event(erc4626_events[0])
    upshift_deposit_topic = get_topic_signature_from_event(upshift_events[0])

    assert upshift_deposit_topic != erc4626_deposit_topic


def test_upshift_multi_asset_topics_are_in_aggregate_discovery_events():
    """Verify aggregate discovery scans include all Upshift multi-asset topics."""
    web3 = Web3()
    all_topics = {get_topic_signature_from_event(event) for event in get_vault_discovery_events(web3)}
    upshift_topics = {get_topic_signature_from_event(event) for event in get_upshift_multi_asset_discovery_events(web3)}

    assert upshift_topics <= all_topics


def test_upshift_multi_asset_withdraw_topics_are_withdrawals():
    """Verify Upshift multi-asset withdrawal events are counted as redemptions."""
    web3 = Web3()
    upshift_events = get_upshift_multi_asset_discovery_events(web3)
    topic_map = get_vault_event_topic_map(web3)

    withdrawal_requested_topic = get_topic_signature_from_event(upshift_events[1])
    withdrawal_processed_topic = get_topic_signature_from_event(upshift_events[2])

    assert withdrawal_requested_topic == "0xcf41fab81bee2456b7007d9d1a9e2261a6627a41eba8c3302b6b07f9a7a46395"
    assert withdrawal_processed_topic == "0x2e06b2c9d4ccae2592eda2017cb2fb604b8d7418e85f023375514ab25ff2cc4c"
    assert topic_map[withdrawal_requested_topic] == VaultEventKind.withdraw
    assert topic_map[withdrawal_processed_topic] == VaultEventKind.withdraw


def test_deposit_only_lead_is_candidate():
    """Deposit-only vault leads are eligible for protocol probing.

    Large curated vaults can have deposits but no withdrawals yet due to
    pre-deposit phases or lock-ups. The scan must not wait for a withdrawal
    before probing the contract and classifying the vault.
    """
    lead = PotentialVaultMatch(
        chain=1,
        address="0xcd69123b3FBBfC666E1f6a501da27B564C00De54",
        first_seen_at_block=22_000_000,
        first_seen_at=_native_datetime(2026, 6, 10),
        deposit_count=1,
        withdrawal_count=0,
    )

    assert lead.is_candidate()


def test_withdraw_only_lead_is_not_candidate():
    """Withdraw-only logs do not seed a vault candidate without a deposit."""
    lead = PotentialVaultMatch(
        chain=1,
        address="0xc87DBBB8C67e4F19fCD2E297c05937567b2572Ce",
        first_seen_at_block=22_000_000,
        first_seen_at=_native_datetime(2026, 5, 1),
        deposit_count=0,
        withdrawal_count=1,
    )

    assert not lead.is_candidate()


def test_upshift_multi_asset_detection_is_activity_filter_exempt():
    """Upshift multi-asset rows are eligible for targeted price rescans.

    A production operator may seed these known vaults by address to avoid a full
    Ethereum discovery rescan. The price scanner must not drop the rows just
    because old metadata has a stale low deposit counter.
    """
    detection = ERC4262VaultDetection(
        chain=1,
        address="0xcd69123b3FBBfC666E1f6a501da27B564C00De54",
        first_seen_at_block=22_000_000,
        first_seen_at=_native_datetime(2026, 6, 10),
        features={ERC4626Feature.upshift_like, ERC4626Feature.upshift_multi_asset_like},
        updated_at=_native_datetime(2026, 7, 3),
        deposit_count=0,
        redeem_count=0,
    )

    assert is_activity_filter_exempt(detection) is True
