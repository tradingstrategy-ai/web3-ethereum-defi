"""Yearn V3 deposit preflight tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from web3.exceptions import ContractLogicError

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


def test_yearn_deposit_preflight_reports_rejected_call_without_revert_data() -> None:
    """Keep a confirmed Yearn revert typed when the node omits its payload.

    1. Prepare an approved deposit call that raises a revert exception without data.
    2. Run the exact Yearn deposit preflight.
    3. Verify the refusal does not claim a decoded custom error.
    """
    # 1. Prepare an approved deposit call that raises a revert exception without data.
    reverted_call = MagicMock()
    reverted_call.call.side_effect = ContractLogicError("execution reverted")
    deposit_call = MagicMock(return_value=reverted_call)
    manager = _create_manager(allowance=RAW_AMOUNT, deposit_call=deposit_call)

    # 2. Run the exact Yearn deposit preflight.
    rejection = manager.fetch_deposit_rejection(OWNER, raw_amount=RAW_AMOUNT)

    # 3. Verify the refusal does not claim a decoded custom error.
    assert isinstance(rejection, VaultFlowUnavailable)
    assert rejection.preflight_result == "deposit_admission_rejected"
    assert rejection.decoded_error is None
    assert rejection.error_selector is None
    assert rejection.raw_revert_data is None


def test_yearn_deposit_request_raises_admission_rejection_before_common_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop a Yearn request before the inherited flow when its exact call is rejected.

    1. Prepare a manager with no global closure and a typed admission rejection.
    2. Build a deposit request with the normal capacity preflight enabled.
    3. Verify the typed rejection is raised before the common request builder.
    """
    # 1. Prepare a manager with no global closure and a typed admission rejection.
    manager = _create_manager(allowance=RAW_AMOUNT, deposit_call=MagicMock())
    rejection = VaultFlowUnavailable(
        "Yearn vault rejected the approved deposit call",
        protocol="Yearn",
        vault_address=VAULT_ADDRESS,
        caller=OWNER,
        direction="deposit",
        phase="preflight",
        preflight_result="deposit_admission_rejected",
    )
    monkeypatch.setattr(manager, "check_deposit_whitelist", lambda _owner: None)
    monkeypatch.setattr(manager, "fetch_global_deposit_closure_reason", lambda _owner: None)
    monkeypatch.setattr(manager, "fetch_deposit_rejection", lambda _owner, _raw_amount: rejection)

    # 2. Build a deposit request with the normal capacity preflight enabled.
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_deposit_request(OWNER, raw_amount=RAW_AMOUNT)

    # 3. Verify the typed rejection is raised before the common request builder.
    assert exc_info.value is rejection
