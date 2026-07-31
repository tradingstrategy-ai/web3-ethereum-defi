"""Regression tests for ERC-4626 event amount normalisation."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from eth_defi.erc_4626.analysis import analyse_4626_flow_transaction
from eth_defi.trade import TradeSuccess


def test_redemption_analysis_accepts_signed_compatible_event_amount() -> None:
    """Test a compatible vault's signed redemption output is normalised.

    1. Build a successful redemption receipt with a negative decoded asset amount.
    2. Analyse it through the shared ERC-4626 receipt path.
    3. Confirm the successful result carries the absolute output amount.
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
    result = analyse_4626_flow_transaction(
        vault=vault,
        tx_hash="0x" + "00" * 32,
        tx_receipt=receipt,
        direction="redeem",
    )

    # 3. Confirm the successful result carries the absolute output amount.
    assert isinstance(result, TradeSuccess)
    assert result.amount_in == 10**18
    assert result.amount_out == 10**6
