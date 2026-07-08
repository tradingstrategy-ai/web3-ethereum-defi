"""Offline tests for D2 settlement and phase event conversion."""

import datetime

from hexbytes import HexBytes
from web3.datastructures import AttributeDict

from eth_defi.erc_4626.vault_protocol.d2.settlement import D2_PROTOCOL_NAME, build_d2_settlement_rows_from_logs

EPOCH_STARTED_TOPIC = "0x" + "aa" * 32
FUNDS_CUSTODIED_TOPIC = "0x" + "bb" * 32
FUNDS_RETURNED_TOPIC = "0x" + "cc" * 32
NEW_MAX_DEPOSITS_TOPIC = "0x" + "dd" * 32
EVENT_BY_TOPIC = {
    EPOCH_STARTED_TOPIC: "EpochStarted",
    FUNDS_CUSTODIED_TOPIC: "FundsCustodied",
    FUNDS_RETURNED_TOPIC: "FundsReturned",
    NEW_MAX_DEPOSITS_TOPIC: "NewMaxDeposits",
}


class FakeD2Vault:
    """Minimal D2 vault stand-in for log conversion tests."""

    chain_id = 42161
    address = "0xd200000000000000000000000000000000000000"
    web3 = None


def make_log(tx_hash: str, log_index: int, topic: str) -> AttributeDict:
    """Build a Web3-like log for a D2 settlement or phase event."""
    return AttributeDict(
        {
            "blockNumber": 456,
            "blockHash": HexBytes("0x" + "11" * 32),
            "transactionHash": HexBytes(tx_hash),
            "topics": [HexBytes(topic)],
            "logIndex": log_index,
            "blockTimestamp": datetime.datetime(2026, 3, 1, 12, 0, 0),
        }
    )


def test_d2_settlement_rows_keep_phase_event_names() -> None:
    """D2 phase logs produce generic settlement rows with event names."""
    tx_hash = "0x" + "22" * 32
    rows = build_d2_settlement_rows_from_logs(
        FakeD2Vault(),
        [
            make_log(tx_hash, 10, EPOCH_STARTED_TOPIC),
            make_log(tx_hash, 11, FUNDS_CUSTODIED_TOPIC),
            make_log(tx_hash, 12, FUNDS_RETURNED_TOPIC),
            make_log(tx_hash, 13, NEW_MAX_DEPOSITS_TOPIC),
        ],
        event_by_topic=EVENT_BY_TOPIC,
    )

    assert len(rows) == 4
    assert [row.event_name for row in rows] == [
        "EpochStarted",
        "FundsCustodied",
        "FundsReturned",
        "NewMaxDeposits",
    ]
    assert {row.protocol for row in rows} == {D2_PROTOCOL_NAME}
    assert {row.chain_id for row in rows} == {42161}
    assert {row.address for row in rows} == {FakeD2Vault.address}
    assert {row.tx_hash for row in rows} == {tx_hash}
