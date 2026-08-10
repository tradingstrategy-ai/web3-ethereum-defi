"""Tests for extracting ABI-encoded revert data from provider exceptions."""

from eth_defi.revert_reason import extract_revert_data


def test_extract_revert_data_handles_web3_and_json_rpc_error_shapes() -> None:
    """Extract a custom-error payload without depending on a live RPC provider.

    1. Prepare a representative Solidity custom-error payload.
    2. Recover it from Web3's direct exception attribute and a nested JSON-RPC response.
    3. Verify an exception without ABI data remains unclassified.
    """
    payload = bytes.fromhex("12345678" + "00" * 32)

    # 1. Prepare a representative Solidity custom-error payload.
    attribute_error = Exception("execution reverted")
    attribute_error.data = payload
    response_error = ValueError({"error": {"data": "0x" + payload.hex()}})

    # 2. Recover it from Web3's direct exception attribute and a nested JSON-RPC response.
    assert extract_revert_data(attribute_error) == payload
    assert extract_revert_data(response_error) == payload

    # 3. Verify an exception without ABI data remains unclassified.
    assert extract_revert_data(Exception("execution reverted")) is None
