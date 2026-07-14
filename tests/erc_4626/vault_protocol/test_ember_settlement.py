"""Test Ember successful operator settlement collection."""

import datetime
import os

import pytest
from hexbytes import HexBytes
from web3.datastructures import AttributeDict

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.ember.settlement import (
    EMBER_PROTOCOL_NAME,
    build_ember_settlement_rows_from_logs,
    fetch_ember_settlements,
    get_ember_settlement_events_by_topic,
)
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
EMBER_VAULT = "0xf3190A3ECC109F88e7947b849b281918c798A0C4"
PROCESS_BLOCK = 24_290_495
PROCESS_TX = "0x9ad0c9fe93adcbffb158da6d4b8694059afea77b24c8a09deb8ae3ebba15ae79"
PROCESS_BLOCK_HASH = "0xb9cd1320438f956457ae081802fc404fa3d07c670bce61c7b60efa043f91209c"

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def vault() -> EmberVault:
    """Open the historical Ember vault through a live Ethereum archive RPC."""
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    vault = create_vault_instance_autodetect(web3, EMBER_VAULT)
    assert isinstance(vault, EmberVault)
    return vault


def _get_success_log(vault: EmberVault) -> AttributeDict:
    """Fetch the known successful historical Ember processing event."""
    event = vault.vault_contract.events.RequestProcessed
    logs = vault.web3.eth.get_logs(
        {
            "address": vault.address,
            "topics": [[event.topic]],
            "fromBlock": PROCESS_BLOCK,
            "toBlock": PROCESS_BLOCK,
        }
    )
    assert len(logs) == 1
    return AttributeDict(logs[0])


def _with_terminal_flags(log: AttributeDict, *, skipped: bool, cancelled: bool) -> AttributeDict:
    """Return a decoded-fixture log with controlled RequestProcessed flags."""
    words = [bytearray(log["data"][index : index + 32]) for index in range(0, len(log["data"]), 32)]
    words[4][-1] = int(skipped)
    words[5][-1] = int(cancelled)
    return AttributeDict({**log, "data": HexBytes(b"".join(words))})


def test_ember_settlement_conversion_filters_terminal_non_success(vault: EmberVault) -> None:
    """Retain successful processing and exclude skipped/cancelled requests."""
    # 1. Create successful, skipped and cancelled variants of one known log.
    success = _get_success_log(vault)
    skipped = _with_terminal_flags(success, skipped=True, cancelled=False)
    cancelled = _with_terminal_flags(success, skipped=False, cancelled=True)

    # 2. Persist only the operator transaction that paid assets.
    rows = build_ember_settlement_rows_from_logs(vault, [success, skipped, cancelled])
    assert len(rows) == 1
    row = rows[0]
    assert row.protocol == EMBER_PROTOCOL_NAME
    assert row.event_name == "RequestProcessed"
    assert row.address == EMBER_VAULT
    assert row.chain_id == 1
    assert row.tx_hash == PROCESS_TX
    assert row.block_hash == HexBytes(PROCESS_BLOCK_HASH)
    assert row.timestamp == datetime.datetime(2026, 1, 22, 13, 2, 59)


def test_ember_settlement_conversion_deduplicates_per_vault_transaction(vault: EmberVault) -> None:
    """Collapse two successful request logs in one operator transaction only."""
    # 1. Model batched processing and a distinct transaction in the same block.
    success = _get_success_log(vault)
    second_same_transaction = AttributeDict({**success, "logIndex": int(success["logIndex"]) + 1})
    other_transaction = AttributeDict({**success, "transactionHash": HexBytes("0x" + "11" * 32), "logIndex": int(success["logIndex"]) + 2})

    # 2. Retain one marker per settlement transaction, not per request log.
    rows = build_ember_settlement_rows_from_logs(vault, [success, second_same_transaction, other_transaction])
    assert len(rows) == 2
    assert {str(row.tx_hash) for row in rows} == {PROCESS_TX, "0x" + "11" * 32}


def test_ember_live_settlement_reader(vault: EmberVault) -> None:
    """Read the known successful settlement from the exact historical block."""
    assert list(get_ember_settlement_events_by_topic(vault)) == ["0x14239ade46d853ae1a98641c2a237d05a11e24ff2678eb6bf0e409953779a057"]
    rows = fetch_ember_settlements(vault, PROCESS_BLOCK, PROCESS_BLOCK, use_hypersync=False)
    assert len(rows) == 1
    assert rows[0].tx_hash == PROCESS_TX
    assert rows[0].block_hash == HexBytes(PROCESS_BLOCK_HASH)
    assert rows[0].timestamp == datetime.datetime(2026, 1, 22, 13, 2, 59)
