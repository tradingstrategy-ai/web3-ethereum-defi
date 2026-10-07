"""Preserve position-close evidence when the executed order type is unknown.

Regression coverage for tradingstrategy-ai/web3-ethereum-defi#1554. A decoded
``PositionDecrease`` proves a close even when receipt retrieval or subsequent
``OrderExecuted`` decoding cannot establish its order type.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from eth_typing import HexAddress

from eth_defi.gmx.ccxt import exchange
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.events import GMXEventData


@pytest.fixture
def close_event_exchange(monkeypatch: pytest.MonkeyPatch) -> GMX:
    """Create an exchange with a real decoded position-close event.

    Replace the RPC, log acquisition and ABI decoding boundaries while keeping
    event getters and the position-close method real. The fixture does not
    initialise trading services or contact an RPC provider.

    :param monkeypatch:
        Restore the decoding and log acquisition boundaries after each test.
    :return:
        Exchange containing a confirmed long-position decrease.
    """
    gmx = GMX.__new__(GMX)
    gmx.web3 = MagicMock()
    gmx.web3.eth.chain_id = 42161
    gmx.web3.eth.block_number = 400_000_000
    gmx.web3.eth.get_block.return_value = {"timestamp": 1_780_000_000}
    gmx.config = GMXConfig(gmx.web3)

    log = {
        "address": "0x3333333333333333333333333333333333333333",
        "blockHash": "0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "blockNumber": 399_999_999,
        "data": "0x",
        "logIndex": 0,
        "removed": False,
        "topics": [],
        "transactionHash": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "transactionIndex": 0,
    }
    gmx.web3.eth.get_transaction_receipt.return_value = {
        "status": 1,
        "blockNumber": 399_999_999,
        "transactionHash": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "logs": [log],
    }
    position_decrease = GMXEventData(
        event_name="PositionDecrease",
        msg_sender=HexAddress("0x3333333333333333333333333333333333333333"),
        address_items={
            "account": HexAddress("0x1111111111111111111111111111111111111111"),
            "market": HexAddress("0x2222222222222222222222222222222222222222"),
        },
        uint_items={
            "executionPrice": 75_000_000_000_000,
            "sizeDeltaUsd": 250 * 10**30,
        },
        int_items={"basePnlUsd": -5 * 10**30},
        bool_items={"isLong": True},
        bytes32_items={"orderKey": bytes.fromhex("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")},
    )
    monkeypatch.setattr(exchange, "_get_event_emitter_contract", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(exchange, "_get_logs_adaptive", MagicMock(return_value=[log]))
    monkeypatch.setattr(exchange, "decode_gmx_event", MagicMock(return_value=position_decrease))
    return gmx


def _assert_close_evidence(
    result: dict[str, Any] | None,
    order_type_int: int | None,
    order_type: str | None,
) -> None:
    """Check the classification and independently established close evidence.

    Literal expected values distinguish retaining the close from dropping it
    after optional receipt evidence is unavailable or unrecognised.

    :param result:
        Result from the real event-log position-close method.
    :param order_type_int:
        Expected raw order type, including an unsupported integer when present.
    :param order_type:
        Expected known classification or ``None`` for unknown evidence.
    :return:
        ``None`` after all close fields have been checked.
    """
    assert result is not None
    # USD scaling is independent of order classification. Retain the nonzero
    # amounts without fixing their conversion behaviour in this regression.
    assert result["size_delta_usd"] > 0
    assert result["pnl_usd"] < 0
    close_evidence = {key: value for key, value in result.items() if key not in {"size_delta_usd", "pnl_usd"}}
    assert close_evidence == {
        "order_key": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "order_type_int": order_type_int,
        "order_type": order_type,
        "is_long": True,
        "execution_price_raw": 75_000_000_000_000,
        "fees_usd": 0.0,
        "timestamp_ms": 1_780_000_000_000,
        "tx_hash": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "block": 399_999_999,
    }


@pytest.mark.parametrize(
    ("scenario", "expected_order_type_int"),
    [
        ("receipt_failure", None),
        ("decode_failure", None),
        ("no_order_executed", None),
        ("missing_order_type", None),
        ("unsupported_order_type", 255),
    ],
)
def test_unknown_order_type_does_not_fabricate_market_decrease(
    close_event_exchange: GMX,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_order_type_int: int | None,
) -> None:
    """Keep a confirmed close without inventing its executed order type.

    Missing receipt evidence, failed decoding and incomplete or unsupported
    executed-order data must leave the classification unknown. They must not
    discard the independently decoded close or label it a market decrease.

    :param close_event_exchange:
        Exchange with a real matching ``PositionDecrease`` event.
    :param monkeypatch:
        Replace the receipt-event decoding boundary for this scenario.
    :param scenario:
        Failure or incomplete-evidence condition to exercise.
    :param expected_order_type_int:
        Literal expected raw order type after receipt inspection.
    :return:
        ``None`` after checking the retained close and unknown classification.
    """
    if scenario == "receipt_failure":
        close_event_exchange.web3.eth.get_transaction_receipt.side_effect = ConnectionError("RPC unavailable")
        receipt_events = MagicMock(return_value=iter(()))
    elif scenario == "decode_failure":
        receipt_events = MagicMock(side_effect=ValueError("Cannot decode receipt events"))
    elif scenario == "no_order_executed":
        receipt_events = MagicMock(return_value=iter((GMXEventData(event_name="PositionFeesCollected"),)))
    elif scenario == "missing_order_type":
        receipt_events = MagicMock(return_value=iter((GMXEventData(event_name="OrderExecuted"),)))
    else:
        receipt_events = MagicMock(return_value=iter((GMXEventData(event_name="OrderExecuted", uint_items={"orderType": 255}),)))
    monkeypatch.setattr(exchange, "decode_gmx_events", receipt_events)

    result = close_event_exchange._fetch_position_close_from_event_logs(
        market_address="0x2222222222222222222222222222222222222222",
        is_long=True,
        wallet_addr="0x1111111111111111111111111111111111111111",
    )

    _assert_close_evidence(result, expected_order_type_int, None)


@pytest.mark.parametrize(
    ("order_type_int", "expected_order_type"),
    [
        (4, "market_decrease"),
        (5, "limit_decrease"),
        (6, "stop_loss"),
        (7, "liquidation"),
    ],
)
def test_recognised_order_type_preserves_classification(
    close_event_exchange: GMX,
    monkeypatch: pytest.MonkeyPatch,
    order_type_int: int,
    expected_order_type: str,
) -> None:
    """Preserve each supported classification when receipt evidence is available.

    Literal integer/name pairs protect known market, limit, stop-loss and
    liquidation classifications while the unknown-evidence fallback changes.

    :param close_event_exchange:
        Exchange with a real matching ``PositionDecrease`` event.
    :param monkeypatch:
        Replace ABI decoding with a real executed-order event.
    :param order_type_int:
        Supported GMX order type supplied by the executed-order event.
    :param expected_order_type:
        Independently specified classification for the supplied integer.
    :return:
        ``None`` after checking the classification and retained close fields.
    """
    order_executed = GMXEventData(event_name="OrderExecuted", uint_items={"orderType": order_type_int})
    monkeypatch.setattr(exchange, "decode_gmx_events", MagicMock(return_value=iter((order_executed,))))

    result = close_event_exchange._fetch_position_close_from_event_logs(
        market_address="0x2222222222222222222222222222222222222222",
        is_long=True,
        wallet_addr="0x1111111111111111111111111111111111111111",
    )

    _assert_close_evidence(result, order_type_int, expected_order_type)
