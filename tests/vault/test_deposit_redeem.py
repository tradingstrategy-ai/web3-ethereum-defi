"""Tests for common vault deposit and redemption flow types."""

from eth_typing import HexAddress

from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

REQUESTED_RAW_AMOUNT = 101
AVAILABLE_RAW_AMOUNT = 100


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
        requested_raw_amount=REQUESTED_RAW_AMOUNT,
        available_raw_amount=AVAILABLE_RAW_AMOUNT,
    )

    assert error.reason == "Immediate redemption unavailable"
    assert error.decoded_error == "CapacityExceeded"
    assert error.requested_raw_amount == REQUESTED_RAW_AMOUNT
    assert error.available_raw_amount == AVAILABLE_RAW_AMOUNT
    assert str(error) == ("Immediate redemption unavailable (protocol=Example protocol, vault=0x0000000000000000000000000000000000000001, caller=0x0000000000000000000000000000000000000002, direction=redeem, phase=request, decoded_error=CapacityExceeded, requested_raw_amount=101, available_raw_amount=100)")
