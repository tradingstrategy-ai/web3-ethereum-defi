"""Fresh fee quotes for the activation funding envelope."""

# ruff: noqa: PLR2004

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eth_defi.cctp import receive
from eth_defi.cctp.fees import fetch_cctp_fee_quote


@pytest.mark.parametrize(("source", "threshold", "calls"), [(999, 2000, 1), (8453, 1000, 2), (42161, 1000, 2)])
def test_source_specific_quotes(source: int, threshold: int, calls: int) -> None:
    """Fee rounding uses the gross burn and Fast allowance is in whole USDC."""
    token = SimpleNamespace(convert_to_raw=lambda v: int(v * 10**6), convert_to_decimals=lambda v: Decimal(v) / 10**6)
    quote, allowance = Mock(), Mock()
    quote.json.return_value = [{"finalityThreshold": threshold, "minimumFee": "1.001"}]
    allowance.json.return_value = {"allowance": "1.01"}
    client = Mock()
    client.get.side_effect = [quote, allowance]
    result = fetch_cctp_fee_quote(token, source, 1, Decimal(1), session=client)
    assert result.amount_raw == 1_010_000 and result.max_fee_raw == 10_000
    assert result.fee_raw == 102
    assert result.finality == threshold and client.get.call_count == calls


@pytest.mark.parametrize("problem", ["fee", "allowance", "precision", "quote"])
def test_bad_quote_stops_before_burn(problem: str) -> None:
    """No insufficient quote silently falls back to standard finality."""
    token = SimpleNamespace(convert_to_raw=lambda v: int(v * 10**6), convert_to_decimals=lambda v: Decimal(v) / 10**6)
    quote, allowance = Mock(), Mock()
    quote.json.return_value = [{"finalityThreshold": 2000 if problem == "quote" else 1000, "minimumFee": 1000 if problem == "fee" else 1}]
    allowance.json.return_value = {"allowance": "0.99"}
    client = Mock()
    client.get.side_effect = [quote, allowance]
    with pytest.raises(AssertionError):
        fetch_cctp_fee_quote(token, 8453, 1, Decimal(1), max_fee=Decimal("0.0000001") if problem == "precision" else Decimal("0.01"), session=client)


def test_received_message_uses_original_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the exact V2 message nonce rather than infer a relay from balances."""
    nonce = (123).to_bytes(32, "big")
    message = (1).to_bytes(4, "big") + (19).to_bytes(4, "big") + bytes(4) + nonce + bytes(104)
    transmitter = Mock()
    transmitter.functions.usedNonces.return_value.call.return_value = 1
    monkeypatch.setattr(receive, "get_message_transmitter_v2", lambda _: transmitter)
    assert receive.fetch_message_received(Mock(), message)
    transmitter.functions.usedNonces.assert_called_once_with(nonce)
