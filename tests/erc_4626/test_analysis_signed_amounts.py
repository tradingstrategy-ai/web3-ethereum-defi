"""Regression tests for ERC-4626 zero-output receipt analysis."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from eth_defi.erc_4626.analysis import analyse_4626_flow_transaction
from eth_defi.trade import TradeSuccess


def test_redemption_analysis_rejects_negative_event_amount() -> None:
    """Reject a malformed negative value for an unsigned ERC-4626 event field.

    1. Build a successful redemption receipt with a negative decoded asset amount.
    2. Analyse it through the shared ERC-4626 receipt path.
    3. Confirm receipt analysis rejects the ABI-incompatible value.
    """
    # 1. Build a successful redemption receipt with a negative decoded asset amount.
    vault_address = "0x1111111111111111111111111111111111111111"
    withdraw_event = MagicMock()
    withdraw_event.process_receipt.return_value = [
        {
            "address": vault_address,
            "args": {
                "shares": 10**18,
                "assets": -(10**6),
            },
        }
    ]
    contract = MagicMock()
    contract.events.Withdraw.return_value = withdraw_event
    vault = SimpleNamespace(
        web3=MagicMock(),
        address=vault_address,
        vault_address=vault_address,
        vault_contract=contract,
        share_token=SimpleNamespace(
            address_lower="0x2222222222222222222222222222222222222222",
            decimals=18,
        ),
        denomination_token=SimpleNamespace(
            address_lower="0x3333333333333333333333333333333333333333",
            decimals=6,
        ),
    )
    receipt = {
        "to": vault_address,
        "status": 1,
        "gasUsed": 100_000,
        "effectiveGasPrice": 1,
    }

    # 2. Analyse it through the shared ERC-4626 receipt path.
    with pytest.raises(AssertionError, match="output amount must not be negative"):
        analyse_4626_flow_transaction(
            vault=vault,
            tx_hash="0x" + "00" * 32,
            tx_receipt=receipt,
            direction="redeem",
        )

    # 3. The ABI-incompatible value was not converted to a plausible trade.


def test_redemption_analysis_preserves_zero_output() -> None:
    """A successful zero-output Withdraw event remains analysable.

    1. Build a successful redemption whose Withdraw event reports zero assets.
    2. Analyse the receipt without inventing an output amount.
    3. Confirm the mined zero-value economic outcome is preserved.
    """
    # 1. Build a successful redemption whose Withdraw event reports zero assets.
    vault_address = "0x1111111111111111111111111111111111111111"
    receiver = "0x2222222222222222222222222222222222222222"
    withdraw_event = MagicMock()
    withdraw_event.process_receipt.return_value = [
        {
            "address": vault_address,
            "args": {
                "receiver": receiver,
                "shares": 10**18,
                "assets": 0,
            },
        }
    ]
    contract = MagicMock()
    contract.events.Withdraw.return_value = withdraw_event
    vault = SimpleNamespace(
        web3=MagicMock(),
        address=vault_address,
        vault_address=vault_address,
        vault_contract=contract,
        share_token=SimpleNamespace(
            address_lower="0x4444444444444444444444444444444444444444",
            decimals=18,
        ),
        denomination_token=SimpleNamespace(
            address_lower="0x3333333333333333333333333333333333333333",
            decimals=6,
        ),
    )
    receipt = {
        "to": vault_address,
        "status": 1,
        "gasUsed": 100_000,
        "effectiveGasPrice": 1,
    }

    # 2. Analyse the receipt without inventing an output amount.
    result = analyse_4626_flow_transaction(
        vault=vault,
        tx_hash="0x" + "00" * 32,
        tx_receipt=receipt,
        direction="redeem",
    )

    # 3. Confirm the mined zero-value economic outcome is preserved.
    assert isinstance(result, TradeSuccess)
    assert result.amount_in == 10**18
    assert result.amount_out == 0
    assert result.price == 0
    assert result.get_human_price(reverse_token_order=True) == Decimal("Infinity")
