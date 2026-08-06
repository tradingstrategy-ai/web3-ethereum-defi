"""Tests for common vault deposit and redemption flow types."""

from eth_typing import HexAddress
from hexbytes import HexBytes

from eth_defi.erc_4626.vault_protocol.plutus.deposit_redeem import PlutusRedemptionTicket
from eth_defi.vault.deposit_redeem import (
    AsyncVaultRequestStatus,
    UnsupportedVaultSimulation,
    VaultDirectPayoutEvidence,
    VaultFlowUnavailable,
    VaultForcedSettlementResult,
    create_synchronous_settlement_result,
    extract_revert_data,
)

REQUESTED_RAW_AMOUNT = 101
AVAILABLE_RAW_AMOUNT = 100
ACCESS_DELAY = 3600


def test_extract_revert_data_from_web3_exception_shapes() -> None:
    """Normalise provider-specific EVM revert payload locations.

    1. Extract a revert payload exposed through an exception ``data`` mapping.
    2. Return no payload for an ordinary exception without EVM data.
    """
    # 1. Extract a revert payload exposed through an exception data mapping.
    assert extract_revert_data(ValueError({"data": "0xace2a47e"})) == HexBytes("0xace2a47e")

    # 2. Return no payload for an ordinary exception without EVM data.
    assert extract_revert_data(ValueError("ordinary error")) is None


def test_vault_flow_unavailable_preserves_context() -> None:
    """Keep preflight diagnostic fields distinct from transaction failures."""
    error = VaultFlowUnavailable(
        "Immediate redemption unavailable",
        protocol="Example protocol",
        vault_address=HexAddress("0x0000000000000000000000000000000000000001"),
        caller=HexAddress("0x0000000000000000000000000000000000000002"),
        direction="redeem",
        phase="request",
        decoded_error="CapacityExceeded",
        preflight_result="redemption_capacity_limited",
        requested_raw_amount=REQUESTED_RAW_AMOUNT,
        available_raw_amount=AVAILABLE_RAW_AMOUNT,
    )

    assert error.reason == "Immediate redemption unavailable"
    assert error.decoded_error == "CapacityExceeded"
    assert error.preflight_result == "redemption_capacity_limited"
    assert error.requested_raw_amount == REQUESTED_RAW_AMOUNT
    assert error.available_raw_amount == AVAILABLE_RAW_AMOUNT
    assert str(error) == ("Immediate redemption unavailable (protocol=Example protocol, vault=0x0000000000000000000000000000000000000001, caller=0x0000000000000000000000000000000000000002, direction=redeem, phase=request, decoded_error=CapacityExceeded, preflight_result=redemption_capacity_limited, requested_raw_amount=101, available_raw_amount=100)")


def test_unsupported_vault_simulation_preserves_structured_context() -> None:
    """A settlement refusal exposes stable mapping data without prose parsing."""
    error = UnsupportedVaultSimulation(
        "Operator settlement cannot be reproduced",
        unsupported_reason="operator_role_not_available",
        protocol="Example protocol",
        vault_address="0x0000000000000000000000000000000000000001",
        direction="redeem",
    )

    assert error.unsupported_reason == "operator_role_not_available"
    assert error.protocol == "Example protocol"
    assert error.vault_address == "0x0000000000000000000000000000000000000001"
    assert error.direction == "redeem"
    assert error.phase == "settlement"


def test_vault_flow_unavailable_preserves_access_context() -> None:
    """Keep function, error, and access-delay diagnostics distinct."""
    error = VaultFlowUnavailable(
        "Access must be scheduled",
        caller=HexAddress("0x0000000000000000000000000000000000000002"),
        direction="deposit",
        function_selector=HexBytes("0x6e553f65"),
        error_selector=HexBytes("0x068ca9d8"),
        access_delay=ACCESS_DELAY,
    )

    assert error.function_selector == HexBytes("0x6e553f65")
    assert error.error_selector == HexBytes("0x068ca9d8")
    assert error.access_delay == ACCESS_DELAY
    assert str(error) == "Access must be scheduled (caller=0x0000000000000000000000000000000000000002, direction=deposit, function_selector=6e553f65, error_selector=068ca9d8, access_delay=3600)"


def test_forced_settlement_result_requires_terminal_evidence() -> None:
    """Accept claimable and verified direct payouts, never a pending ticket."""
    receiver = HexAddress("0x0000000000000000000000000000000000000002")
    ticket = PlutusRedemptionTicket(
        vault_address=HexAddress("0x0000000000000000000000000000000000000001"),
        owner=receiver,
        to=receiver,
        raw_shares=1,
        tx_hash=HexBytes("0x01"),
        request_id=7,
    )
    transaction_hash = HexBytes("0x02")

    assert create_synchronous_settlement_result().is_terminal_success() is True
    assert (
        VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=True,
            status_before=AsyncVaultRequestStatus.pending,
            status_after=AsyncVaultRequestStatus.pending,
            transaction_hashes=(transaction_hash,),
        ).is_terminal_success()
        is False
    )
    assert (
        VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=True,
            status_before=AsyncVaultRequestStatus.pending,
            status_after=AsyncVaultRequestStatus.claimable,
            transaction_hashes=(transaction_hash,),
        ).is_terminal_success()
        is True
    )
    assert (
        VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=False,
            status_before=AsyncVaultRequestStatus.claimable,
            status_after=AsyncVaultRequestStatus.claimable,
        ).is_terminal_success()
        is True
    )
    assert (
        VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=False,
            status_before=AsyncVaultRequestStatus.pending,
            status_after=AsyncVaultRequestStatus.claimable,
        ).is_terminal_success()
        is True
    )

    mismatched_request = VaultDirectPayoutEvidence(
        request_id=ticket.request_id + 1,
        receiver=receiver,
        denomination_token=HexAddress("0x0000000000000000000000000000000000000003"),
        raw_balance_before=100,
        raw_balance_after=101,
        event_name="RequestProcessed",
        transaction_hash=transaction_hash,
    )
    assert (
        VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=True,
            status_before=AsyncVaultRequestStatus.pending,
            status_after=AsyncVaultRequestStatus.none,
            transaction_hashes=(transaction_hash,),
            direct_payout_evidence=mismatched_request,
        ).is_terminal_success()
        is False
    )

    zero_delta = VaultDirectPayoutEvidence(
        request_id=ticket.request_id,
        receiver=receiver,
        denomination_token=HexAddress("0x0000000000000000000000000000000000000003"),
        raw_balance_before=100,
        raw_balance_after=100,
        event_name="RequestProcessed",
        transaction_hash=transaction_hash,
    )
    assert (
        VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=True,
            status_before=AsyncVaultRequestStatus.pending,
            status_after=AsyncVaultRequestStatus.none,
            transaction_hashes=(transaction_hash,),
            direct_payout_evidence=zero_delta,
        ).is_terminal_success()
        is False
    )

    verified_direct_payout = VaultDirectPayoutEvidence(
        request_id=ticket.request_id,
        receiver=receiver,
        denomination_token=HexAddress("0x0000000000000000000000000000000000000003"),
        raw_balance_before=100,
        raw_balance_after=101,
        event_name="RequestProcessed",
        transaction_hash=transaction_hash,
    )
    assert (
        VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=True,
            status_before=AsyncVaultRequestStatus.pending,
            status_after=AsyncVaultRequestStatus.none,
            transaction_hashes=(transaction_hash,),
            direct_payout_evidence=verified_direct_payout,
        ).is_terminal_success()
        is True
    )

    lower_case_receiver = VaultDirectPayoutEvidence(
        request_id=ticket.request_id,
        receiver=HexAddress(receiver.lower()),
        denomination_token=HexAddress("0x0000000000000000000000000000000000000003"),
        raw_balance_before=100,
        raw_balance_after=101,
        event_name="RequestProcessed",
        transaction_hash=transaction_hash,
    )
    assert (
        VaultForcedSettlementResult(
            ticket=ticket,
            settlement_required=True,
            status_before=AsyncVaultRequestStatus.pending,
            status_after=AsyncVaultRequestStatus.none,
            transaction_hashes=(transaction_hash,),
            direct_payout_evidence=lower_case_receiver,
        ).is_terminal_success()
        is True
    )
