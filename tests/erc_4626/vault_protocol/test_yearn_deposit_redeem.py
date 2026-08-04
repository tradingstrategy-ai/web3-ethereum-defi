"""Yearn V3 deposit preflight tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from eth_defi.erc_4626.vault_protocol.yearn.deposit_redeem import YearnV3DepositManager
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

OWNER = "0x0000000000000000000000000000000000000001"
VAULT_ADDRESS = "0x0000000000000000000000000000000000000002"
RAW_AMOUNT = 100


def _create_manager(allowance: int, deposit_call: MagicMock) -> YearnV3DepositManager:
    """Create a Yearn manager with an isolated ERC-20 allowance and deposit call."""
    token = SimpleNamespace(
        contract=SimpleNamespace(
            functions=SimpleNamespace(
                allowance=MagicMock(return_value=SimpleNamespace(call=MagicMock(return_value=allowance))),
            ),
        ),
    )
    vault = SimpleNamespace(
        address=VAULT_ADDRESS,
        denomination_token=token,
        vault_contract=SimpleNamespace(functions=SimpleNamespace(deposit=deposit_call)),
        get_protocol_name=lambda: "Yearn",
    )
    manager = object.__new__(YearnV3DepositManager)
    manager.vault = vault
    return manager


def test_yearn_deposit_preflight_skips_unapproved_call() -> None:
    """Do not simulate a Yearn deposit before its request approval exists.

    1. Prepare a deposit with an allowance below the requested amount.
    2. Run the Yearn approved-deposit preflight.
    3. Verify no false allowance-only vault simulation occurs.
    """
    # 1. Prepare a deposit with an allowance below the requested amount.
    deposit_call = MagicMock()
    manager = _create_manager(allowance=99, deposit_call=deposit_call)

    # 2. Run the Yearn approved-deposit preflight.
    rejection = manager.fetch_deposit_rejection(OWNER, raw_amount=RAW_AMOUNT)

    # 3. Verify no false allowance-only vault simulation occurs.
    assert rejection is None
    deposit_call.assert_not_called()


def test_yearn_deposit_preflight_reports_rejected_approved_call() -> None:
    """Convert a rejected approved Yearn deposit to a typed preflight result.

    1. Prepare a sufficient allowance and a deposit call returning revert data.
    2. Run the exact Yearn deposit preflight for the sender and amount.
    3. Verify the structured refusal retains the amount and error selector.
    """
    # 1. Prepare a sufficient allowance and a deposit call returning revert data.
    reverted_call = MagicMock()
    reverted_call.call.side_effect = ValueError({"data": "0x12345678"})
    deposit_call = MagicMock(return_value=reverted_call)
    manager = _create_manager(allowance=RAW_AMOUNT, deposit_call=deposit_call)

    # 2. Run the exact Yearn deposit preflight for the sender and amount.
    rejection = manager.fetch_deposit_rejection(OWNER, raw_amount=RAW_AMOUNT)

    # 3. Verify the structured refusal retains the amount and error selector.
    assert isinstance(rejection, VaultFlowUnavailable)
    assert rejection.preflight_result == "deposit_admission_rejected"
    assert rejection.requested_raw_amount == RAW_AMOUNT
    assert rejection.error_selector == b"\x12\x34\x56\x78"
    deposit_call.assert_called_once_with(RAW_AMOUNT, OWNER)
    reverted_call.call.assert_called_once_with({"from": OWNER})
