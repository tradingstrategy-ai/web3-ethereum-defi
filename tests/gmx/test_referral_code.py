"""Test GMX referral code encoding and on-chain verification.

Verifies that our bytes32 encoding of referral code strings matches
what GMX has registered on-chain in the ReferralStorage contract.

See :ref:`GMX referral programme <https://docs.gmx.io/docs/referrals/>`.
"""

import json
import os

import pytest
from eth_typing import HexAddress

from eth_defi.event_reader.conversion import convert_string_to_bytes32
from eth_defi.gmx.order.base_order import ZERO_REFERRAL_CODE

#: ReferralStorage contract address on Arbitrum (shared between GMX V1 and V2).
#: See https://arbiscan.io/address/0xe6fab3F0c7199b0d34d7FbE83394fc0e0D06e99d
ARBITRUM_REFERRAL_STORAGE: HexAddress = "0xe6fab3F0c7199b0d34d7FbE83394fc0e0D06e99d"

#: Minimal ABI for ReferralStorage view functions
REFERRAL_STORAGE_ABI = json.load(open(os.path.join(os.path.dirname(__file__), "../../eth_defi/abi/gmx/ReferralStorage.json")))

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def test_encode_referral_code():
    """Test that convert_string_to_bytes32 produces correct bytes32 for GMX referral codes."""
    code = convert_string_to_bytes32("tano")
    assert len(code) == 32
    assert code[:4] == b"tano"
    assert code[4:] == b"\x00" * 28
    # Verify hex representation
    assert code.hex() == "74616e6f" + "00" * 28


def test_encode_referral_code_different_codes():
    """Test encoding of various referral code strings."""
    # Single character
    code = convert_string_to_bytes32("a")
    assert code[:1] == b"a"
    assert code[1:] == b"\x00" * 31

    # Longer code
    code = convert_string_to_bytes32("cutfees")
    assert code[:7] == b"cutfees"
    assert code[7:] == b"\x00" * 25

    # Max length (32 chars)
    code = convert_string_to_bytes32("a" * 32)
    assert code == b"a" * 32


def test_encode_referral_code_too_long():
    """Test that overly long strings are rejected."""
    with pytest.raises(AssertionError):
        convert_string_to_bytes32("a" * 33)


def test_zero_referral_code_constant():
    """Test that ZERO_REFERRAL_CODE is 32 zero bytes."""
    assert ZERO_REFERRAL_CODE == b"\x00" * 32
    assert len(ZERO_REFERRAL_CODE) == 32


@pytest.mark.skipif(
    not os.environ.get("JSON_RPC_ARBITRUM"),
    reason="JSON_RPC_ARBITRUM environment variable not set",
)
def test_referral_code_registered_on_chain():
    """Verify that our encoding of 'tano' matches the on-chain registered code.

    Calls ReferralStorage.codeOwners() on Arbitrum to confirm the code exists
    and our bytes32 encoding is correct.

    Requires JSON_RPC_ARBITRUM environment variable.
    """
    from eth_defi.provider.multi_provider import create_multi_provider_web3

    rpc_url = os.environ["JSON_RPC_ARBITRUM"]
    web3 = create_multi_provider_web3(rpc_url)

    contract = web3.eth.contract(
        address=ARBITRUM_REFERRAL_STORAGE,
        abi=REFERRAL_STORAGE_ABI,
    )

    code = convert_string_to_bytes32("tano")
    owner = contract.functions.codeOwners(code).call()
    assert owner != ZERO_ADDRESS, f"Referral code 'tano' not registered on-chain. codeOwners returned zero address. Encoded bytes32: 0x{code.hex()}"
