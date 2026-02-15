"""Fork-based integration tests for CCTP V2 depositForBurn.

Tests run against actual CCTP V2 contracts on Ethereum mainnet fork.
Verifies that the Python wrapper correctly interacts with the real
TokenMessengerV2 contract.
"""

import os

import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3
from web3.contract import Contract

from eth_defi.cctp.constants import TOKEN_MESSENGER_V2
from eth_defi.cctp.transfer import (
    encode_mint_recipient,
    get_token_messenger_v2,
    prepare_approve_for_burn,
    prepare_deposit_for_burn,
)
from eth_defi.trace import assert_transaction_success_with_explanation

pytestmark = pytest.mark.skipif(
    not os.environ.get("JSON_RPC_ETHEREUM"),
    reason="JSON_RPC_ETHEREUM environment variable not set",
)


def test_encode_mint_recipient():
    """Test that Ethereum addresses are correctly encoded to bytes32."""
    address = "0x1234567890abcdef1234567890abcdef12345678"
    result = encode_mint_recipient(address)
    assert len(result) == 32
    # Last 20 bytes should be the address
    assert result[-20:] == bytes.fromhex("1234567890abcdef1234567890abcdef12345678")
    # First 12 bytes should be zero padding
    assert result[:12] == b"\x00" * 12


def test_get_token_messenger(web3):
    """Test that TokenMessengerV2 contract can be loaded."""
    messenger = get_token_messenger_v2(web3)
    assert messenger.address == Web3.to_checksum_address(TOKEN_MESSENGER_V2)


def test_deposit_for_burn(
    web3: Web3,
    usdc: Contract,
    ethereum_usdc_whale: HexAddress,
):
    """Test a full depositForBurn transaction on Ethereum mainnet fork.

    Burns USDC on Ethereum targeting Arbitrum as destination.
    """
    amount = 100 * 10**6  # 100 USDC
    recipient = web3.eth.accounts[1]

    # Check whale has enough USDC
    whale_balance = usdc.functions.balanceOf(ethereum_usdc_whale).call()
    assert whale_balance >= amount, f"Whale balance too low: {whale_balance}"

    balance_before = usdc.functions.balanceOf(ethereum_usdc_whale).call()

    # Approve USDC spending by TokenMessengerV2
    approve_fn = prepare_approve_for_burn(web3, amount)
    tx_hash = approve_fn.transact({"from": ethereum_usdc_whale})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Execute depositForBurn
    burn_fn = prepare_deposit_for_burn(
        web3,
        amount=amount,
        destination_chain_id=42161,  # Arbitrum
        mint_recipient=recipient,
    )
    tx_hash = burn_fn.transact({"from": ethereum_usdc_whale})
    receipt = assert_transaction_success_with_explanation(web3, tx_hash)

    # Verify USDC was burned (balance decreased)
    balance_after = usdc.functions.balanceOf(ethereum_usdc_whale).call()
    assert balance_after == balance_before - amount

    # Verify transaction emitted logs (CCTP burn events)
    assert len(receipt["logs"]) > 0


def test_deposit_for_burn_unsupported_destination(
    web3: Web3,
):
    """Test that an unsupported destination chain raises ValueError."""
    with pytest.raises(ValueError, match="not supported by CCTP"):
        prepare_deposit_for_burn(
            web3,
            amount=100 * 10**6,
            destination_chain_id=999999,  # Invalid chain
            mint_recipient="0x0000000000000000000000000000000000000001",
        )
