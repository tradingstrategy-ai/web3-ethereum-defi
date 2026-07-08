"""Offline tests for Lagoon settlement event conversion."""

import datetime
import os
from typing import cast

import pytest
from hexbytes import HexBytes
from web3.datastructures import AttributeDict

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.settlement import (
    LAGOON_PROTOCOL_NAME,
    build_settlement_rows_from_logs,
    fetch_lagoon_settlements,
)
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.provider.multi_provider import create_multi_provider_web3

SETTLE_DEPOSIT_TOPIC = "0x" + "aa" * 32
SETTLE_REDEEM_TOPIC = "0x" + "bb" * 32
HUB_CAPITAL_USDC_VAULT = "0xca790385506b790554571cbc9da73f0130cdcfd5"
HUB_SETTLEMENT_BLOCK = 24_372_419
HUB_SETTLEMENT_TX_HASH = "0x78aba883dbdcc8bbe7dfaee2c429c8bdb95c141940f3170141e469b44ba70b75"
JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")
EVENT_BY_TOPIC = {
    SETTLE_DEPOSIT_TOPIC: "SettleDeposit",
    SETTLE_REDEEM_TOPIC: "SettleRedeem",
}


class FakeLagoonVault:
    """Minimal Lagoon vault stand-in for log conversion tests."""

    chain_id = 1
    address = "0xabc0000000000000000000000000000000000000"
    web3 = None


def make_log(tx_hash: str, log_index: int, topic: str = SETTLE_DEPOSIT_TOPIC) -> AttributeDict:
    """Build a Web3-like log for a Lagoon settlement event."""
    return AttributeDict(
        {
            "blockNumber": 123,
            "blockHash": HexBytes("0x" + "11" * 32),
            "transactionHash": HexBytes(tx_hash),
            "topics": [HexBytes(topic)],
            "logIndex": log_index,
            "blockTimestamp": datetime.datetime(2026, 2, 1, 12, 0, 0),
        }
    )


def test_lagoon_settlement_rows_keep_all_logs() -> None:
    """Deposit and redeem settlement logs in one tx produce separate generic rows."""
    tx_hash = "0x" + "22" * 32
    rows = build_settlement_rows_from_logs(
        FakeLagoonVault(),
        [
            make_log(tx_hash, 10, SETTLE_DEPOSIT_TOPIC),
            make_log(tx_hash, 11, SETTLE_REDEEM_TOPIC),
        ],
        event_by_topic=EVENT_BY_TOPIC,
    )

    assert len(rows) == 2
    assert [row.event_name for row in rows] == ["SettleDeposit", "SettleRedeem"]
    for row in rows:
        assert row.chain_id == 1
        assert row.address == FakeLagoonVault.address
        assert row.block_number == 123
        assert row.protocol == LAGOON_PROTOCOL_NAME
        assert row.block_hash == HexBytes("0x" + "11" * 32)
        assert row.tx_hash == tx_hash
        assert row.timestamp == datetime.datetime(2026, 2, 1, 12, 0, 0)


def test_lagoon_settlement_rows_keep_multiple_transactions_in_same_block() -> None:
    """Multiple settlement transactions in one block produce multiple rows."""
    first_tx_hash = "0x" + "22" * 32
    second_tx_hash = "0x" + "33" * 32
    rows = build_settlement_rows_from_logs(
        FakeLagoonVault(),
        [
            make_log(first_tx_hash, 10, SETTLE_DEPOSIT_TOPIC),
            make_log(second_tx_hash, 11, SETTLE_REDEEM_TOPIC),
        ],
        event_by_topic=EVENT_BY_TOPIC,
    )

    assert len(rows) == 2
    assert {row.block_number for row in rows} == {123}
    assert {row.tx_hash for row in rows} == {first_tx_hash, second_tx_hash}
    assert {row.event_name for row in rows} == {"SettleDeposit", "SettleRedeem"}


@pytest.mark.live
@pytest.mark.skipif(
    not JSON_RPC_ETHEREUM,
    reason="Set JSON_RPC_ETHEREUM environment variable to run this test",
)
@pytest.mark.skipif(
    not HYPERSYNC_API_KEY,
    reason="Set HYPERSYNC_API_KEY environment variable to run this test",
)
def test_lagoon_fetch_settlement_events_from_hypersync_live_1000_blocks() -> None:
    """Scan a live 1,000 block Lagoon settlement window using Hypersync.

    This test locks on to a known Hub Capital USDC vault settlement block that
    historically emitted both ``SettleDeposit`` and ``SettleRedeem``. The
    window is exactly 1,000 inclusive Ethereum blocks, so it exercises the live
    event reader without creating a wide external dependency.
    """
    pytest.importorskip("hypersync")

    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    vault = create_vault_instance(
        web3,
        address=HUB_CAPITAL_USDC_VAULT,
        features={ERC4626Feature.lagoon_like, ERC4626Feature.erc_7540_like},
    )
    assert isinstance(vault, LagoonVault)
    vault = cast(LagoonVault, vault)

    start_block = HUB_SETTLEMENT_BLOCK - 499
    end_block = HUB_SETTLEMENT_BLOCK + 500
    assert end_block - start_block + 1 == 1_000

    rows = fetch_lagoon_settlements(
        vault=vault,
        start_block=start_block,
        end_block=end_block,
        use_hypersync=True,
    )

    matching_rows = [
        row
        for row in rows
        if row.block_number == HUB_SETTLEMENT_BLOCK and row.tx_hash == HUB_SETTLEMENT_TX_HASH
    ]

    assert {row.event_name for row in matching_rows} == {"SettleDeposit", "SettleRedeem"}
    assert all(row.address.lower() == HUB_CAPITAL_USDC_VAULT for row in matching_rows)
    assert all(row.chain_id == 1 for row in matching_rows)
    assert all(row.timestamp == datetime.datetime(2026, 2, 2, 23, 21, 11) for row in matching_rows)
