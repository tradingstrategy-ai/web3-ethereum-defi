"""Test transaction confirmation timeout gas-price diagnostics."""

from unittest.mock import MagicMock

import pytest
from eth_account import Account
from hexbytes import HexBytes
from web3.providers import BaseProvider

from eth_defi import confirmation
from eth_defi.confirmation import format_confirmation_timeout_gas_diagnostics
from eth_defi.hotwallet import SignedTransactionWithNonce
from eth_defi.provider.fallback import FallbackProvider


def _create_signed_transaction(source: dict, hash_byte: str = "12") -> SignedTransactionWithNonce:
    """Create a minimal signed transaction carrying diagnostic source fields."""
    return SignedTransactionWithNonce(
        rawTransaction=HexBytes("0x01"),
        hash=HexBytes("0x" + hash_byte * 32),
        r=0,
        s=0,
        v=0,
        nonce=7,
        address="0x" + "34" * 20,
        source=source,
    )


def test_confirmation_timeout_reports_likely_underpriced_transaction() -> None:
    """Test a timeout explains when an EIP-1559 fee cap is below the network price."""
    provider = MagicMock(spec=BaseProvider)

    def make_request(method: str, params: list) -> dict:
        """Return gas pricing from one diagnostic provider."""
        assert params == []
        values = {
            "eth_gasPrice": hex(81_880_000_000),
            "eth_maxPriorityFeePerGas": hex(1_000_000_000),
        }
        return {"jsonrpc": "2.0", "id": 1, "result": values[method]}

    # Mock the provider to test diagnostic formatting without an external RPC.
    provider.make_request.side_effect = make_request
    signed_tx = _create_signed_transaction(
        {
            "maxFeePerGas": 4_000_000_000,
            "maxPriorityFeePerGas": 100_000_000,
        }
    )
    low_priority_tx = _create_signed_transaction(
        {
            "maxFeePerGas": 100_000_000_000,
            "maxPriorityFeePerGas": 100_000_000,
        },
        hash_byte="56",
    )

    diagnostics = format_confirmation_timeout_gas_diagnostics(
        provider,
        [signed_tx, low_priority_tx],
        [signed_tx.hash, low_priority_tx.hash],
    )

    assert "Current network gas price: 81880000000 wei (81.88 gwei)" in diagnostics
    assert "Current network priority fee: 1000000000 wei (1 gwei)" in diagnostics
    assert "maxFeePerGas=4000000000 wei (4 gwei)" in diagnostics
    assert "maxPriorityFeePerGas=100000000 wei (0.1 gwei)" in diagnostics
    assert "Likely transaction gas-price mispricing" in diagnostics
    assert "may remain deprioritised or be dropped" in diagnostics
    assert "maxPriorityFeePerGas 100000000 wei (0.1 gwei) is below" in diagnostics
    assert "may be deprioritised" in diagnostics


def test_confirmation_timeout_handles_network_gas_price_failure() -> None:
    """Test gas diagnostic RPC failures do not replace the original timeout."""
    provider = MagicMock(spec=BaseProvider)
    method_unavailable = ValueError("method unavailable")
    rpc_offline = ConnectionError("RPC offline")

    def make_request_with_base_fee_fallback(method: str, params: list) -> dict:
        """Fail gas suggestion calls and expose only the latest block base fee."""
        if method == "eth_gasPrice":
            raise method_unavailable
        if method == "eth_maxPriorityFeePerGas":
            assert params == []
            return {"jsonrpc": "2.0", "id": 1, "result": hex(2_000_000_000)}
        assert method == "eth_getBlockByNumber"
        assert params == ["latest", False]
        return {"jsonrpc": "2.0", "id": 1, "result": {"baseFeePerGas": hex(7_000_000_000)}}

    # Mock failures because timeout diagnostics must degrade gracefully when
    # optional RPC methods are unavailable.
    provider.make_request.side_effect = make_request_with_base_fee_fallback
    signed_tx = _create_signed_transaction({"gasPrice": "0x12a05f200"})
    decoded_tx = Account.create().sign_transaction(
        {
            "chainId": 1,
            "nonce": 0,
            "to": "0x" + "78" * 20,
            "value": 0,
            "gas": 21_000,
            "gasPrice": 6_000_000_000,
        }
    )
    constrained_tip_tx = _create_signed_transaction(
        {
            "maxFeePerGas": 7_500_000_000,
            "maxPriorityFeePerGas": 2_000_000_000,
        },
        hash_byte="90",
    )

    fallback_diagnostics = format_confirmation_timeout_gas_diagnostics(
        provider,
        [signed_tx, decoded_tx, constrained_tip_tx],
        [signed_tx.hash, decoded_tx.hash, constrained_tip_tx.hash],
    )

    assert "Latest block base fee: 7000000000 wei (7 gwei)" in fallback_diagnostics
    assert "latest block baseFeePerGas fallback" in fallback_diagnostics
    assert "Likely transaction gas-price mispricing" in fallback_diagnostics
    assert "cannot be included until the base fee falls" in fallback_diagnostics
    assert "gasPrice=6000000000 wei (6 gwei)" in fallback_diagnostics
    assert "effective priority fee 500000000 wei (0.5 gwei) is below" in fallback_diagnostics
    assert "limited by maxPriorityFeePerGas and by maxFeePerGas minus the base fee" in fallback_diagnostics

    def make_failed_request(method: str, params: list) -> dict:
        """Simulate an unavailable diagnostic provider."""
        if method == "eth_getBlockByNumber":
            assert params == ["latest", False]
            raise rpc_offline
        assert params == []
        raise method_unavailable

    provider.make_request.side_effect = make_failed_request
    unavailable_diagnostics = format_confirmation_timeout_gas_diagnostics(
        provider,
        [signed_tx],
        [signed_tx.hash],
    )

    assert "Current network gas price unavailable" in unavailable_diagnostics
    assert "eth_gasPrice failed with ValueError: method unavailable" in unavailable_diagnostics
    assert "latest block lookup failed with ConnectionError: RPC offline" in unavailable_diagnostics
    assert "gasPrice=5000000000 wei (5 gwei)" in unavailable_diagnostics
    assert "could not be assessed" in unavailable_diagnostics


def test_confirmation_timeout_uses_single_provider_for_diagnostics() -> None:
    """Test timeout diagnostics bypass the fallback provider retry loop."""
    web3 = MagicMock()
    active_provider = MagicMock(spec=BaseProvider)
    fallback_provider = MagicMock(spec=FallbackProvider)
    web3.provider = active_provider

    assert confirmation._get_single_attempt_provider(web3) is active_provider

    web3.provider = fallback_provider
    fallback_provider.get_active_provider.return_value = active_provider

    assert confirmation._get_single_attempt_provider(web3) is active_provider


def test_confirmation_timeout_reports_priority_fee_when_network_price_is_unavailable() -> None:
    """Test a usable priority-fee reference is not hidden by other RPC failures."""
    provider = MagicMock(spec=BaseProvider)
    rpc_offline = ConnectionError("RPC offline")
    method_unavailable = ValueError("method unavailable")

    def make_request(method: str, params: list) -> dict:
        """Return only the node's priority-fee suggestion."""
        if method == "eth_maxPriorityFeePerGas":
            assert params == []
            return {"jsonrpc": "2.0", "id": 1, "result": hex(1_000_000_000)}
        if method == "eth_getBlockByNumber":
            assert params == ["latest", False]
            raise rpc_offline
        assert method == "eth_gasPrice"
        assert params == []
        raise method_unavailable

    provider.make_request.side_effect = make_request
    signed_tx = _create_signed_transaction(
        {
            "maxFeePerGas": 10_000_000_000,
            "maxPriorityFeePerGas": 100_000_000,
        }
    )

    diagnostics = format_confirmation_timeout_gas_diagnostics(
        provider,
        [signed_tx],
        [signed_tx.hash],
    )

    assert "Current network gas price unavailable" in diagnostics
    assert "Current network priority fee: 1000000000 wei (1 gwei)" in diagnostics
    assert "maxPriorityFeePerGas 100000000 wei (0.1 gwei) is below" in diagnostics
    assert "maxFeePerGas sufficiency could not be assessed" in diagnostics


def test_fetch_timed_out_transaction_uses_prefixed_hash_and_reports_rpc_errors() -> None:
    """Test raw transaction lookup separates a missing transaction from an RPC error."""
    provider = MagicMock(spec=BaseProvider)
    tx_hash = HexBytes("0x" + "ab" * 32)
    provider.make_request.return_value = {"jsonrpc": "2.0", "id": 1, "result": None}

    missing_transaction = confirmation._fetch_timed_out_transaction(provider, tx_hash)

    assert missing_transaction is None
    provider.make_request.assert_called_once_with(
        "eth_getTransactionByHash",
        [tx_hash.to_0x_hex()],
    )

    provider.make_request.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "invalid argument"},
    }
    with pytest.raises(ValueError, match="eth_getTransactionByHash returned RPC error"):
        confirmation._fetch_timed_out_transaction(provider, tx_hash)
