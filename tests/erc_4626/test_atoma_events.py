"""Test Atoma event discovery."""

from web3 import Web3

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.discovery_base import (
    VaultEventKind,
    get_atoma_vault_discovery_events,
    get_atoma_vault_event_contract,
    get_vault_discovery_events,
    get_vault_event_topic_map,
)


def test_atoma_withdraw_claim_topic_is_withdrawal() -> None:
    """Verify Atoma's async withdrawal claim event is counted as a redemption."""
    web3 = Web3()
    atoma_events = get_atoma_vault_discovery_events(web3)
    topic_map = get_vault_event_topic_map(web3)

    withdrawal_claimed_topic = get_topic_signature_from_event(atoma_events[0])

    assert withdrawal_claimed_topic == "0x64c866b33aa9f619b66496cc313a1a9b159aae4238af633aec343ce747512aa0"
    assert topic_map[withdrawal_claimed_topic] == VaultEventKind.withdraw


def test_atoma_withdraw_request_topic_is_not_counted_as_redeem() -> None:
    """Verify withdrawal requests do not inflate Atoma redeem counts."""
    web3 = Web3()
    atoma_contract = get_atoma_vault_event_contract(web3)
    topic_map = get_vault_event_topic_map(web3)

    withdrawal_requested_topic = get_topic_signature_from_event(atoma_contract.events.WithdrawalRequested)

    assert withdrawal_requested_topic == "0x38e3d972947cfef94205163d483d6287ef27eb312e20cb8e0b13a49989db232e"
    assert withdrawal_requested_topic not in topic_map


def test_atoma_topics_are_in_aggregate_discovery_events() -> None:
    """Verify aggregate discovery scans include Atoma redemption topics."""
    web3 = Web3()
    all_topics = {get_topic_signature_from_event(event) for event in get_vault_discovery_events(web3)}
    atoma_topics = {get_topic_signature_from_event(event) for event in get_atoma_vault_discovery_events(web3)}

    assert atoma_topics <= all_topics
