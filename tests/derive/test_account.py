"""Test Derive empty account handling with mocked API responses.

This test demonstrates that the implementation correctly handles empty accounts
without requiring actual API credentials.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account

from eth_defi.derive.account import (
    AccountSummary,
    CollateralBalance,
    fetch_account_collaterals,
    fetch_account_summary,
)
from eth_defi.derive.authentication import DeriveApiClient


@pytest.fixture
def mock_client():
    """Create a mock Derive client for testing."""
    client = DeriveApiClient(
        owner_account=Account.create(),
        derive_wallet_address="0x1234567890123456789012345678901234567890",
        is_testnet=True,
    )
    # Set a fake session key so auth checks pass
    client.session_key_private = "0x" + "a" * 64
    return client


def test_empty_account_returns_empty_collaterals(mock_client):
    """Test that empty account returns empty collateral list.

    This demonstrates the expected behavior when an account has no balance.
    """
    # Mock the API response for empty account
    with patch.object(mock_client, "_make_jsonrpc_request") as mock_request:
        # Empty account returns empty collaterals array
        mock_request.return_value = {"collaterals": []}

        # Fetch collaterals
        collaterals = fetch_account_collaterals(mock_client)

        # Verify empty list returned
        assert isinstance(collaterals, list)
        assert len(collaterals) == 0

        # Verify correct API call was made
        mock_request.assert_called_once_with(
            method="private/get_collaterals",
            params={"subaccount_id": 1},
            authenticated=True,
        )


def test_empty_account_summary_returns_zero_value(mock_client):
    """Test that empty account summary shows zero total value.

    This demonstrates complete account summary handling for empty accounts.
    """
    with patch.object(mock_client, "_make_jsonrpc_request") as mock_request:
        # Mock responses for different API calls
        def side_effect(method, *args, **kwargs):
            if method == "private/get_collaterals":
                return {"collaterals": []}
            elif method == "private/get_account":
                return {"total_value": "0"}
            elif method == "private/get_margin":
                return {"status": "healthy", "initial_margin": "0", "maintenance_margin": "0"}
            return {}

        mock_request.side_effect = side_effect

        # Fetch account summary
        summary = fetch_account_summary(mock_client)

        # Verify structure
        assert isinstance(summary, AccountSummary)
        assert summary.account_address == mock_client.derive_wallet_address
        assert summary.subaccount_id == 1

        # Verify empty account values
        assert len(summary.collaterals) == 0
        assert summary.total_value_usd == Decimal("0")
        assert summary.margin_status == "healthy"
        assert summary.initial_margin == Decimal("0")
        assert summary.maintenance_margin == Decimal("0")

        # Verify all necessary API calls were made
        assert mock_request.call_count == 3  # collaterals, account, margin


def test_funded_account_returns_collateral_data(mock_client):
    """Test that funded account returns proper collateral data.

    This demonstrates handling of accounts with actual balances.
    """
    with patch.object(mock_client, "_make_jsonrpc_request") as mock_request:
        # Mock response for funded account
        mock_request.return_value = {
            "collaterals": [
                {
                    "currency": "USDC",
                    "available": "100.50",
                    "total": "100.50",
                    "locked": "0",
                    "token_address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                },
                {
                    "currency": "WETH",
                    "available": "0.5",
                    "total": "1.0",
                    "locked": "0.5",
                    "token_address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                },
            ]
        }

        # Fetch collaterals
        collaterals = fetch_account_collaterals(mock_client)

        # Verify list structure
        assert isinstance(collaterals, list)
        assert len(collaterals) == 2

        # Verify USDC collateral
        usdc = collaterals[0]
        assert isinstance(usdc, CollateralBalance)
        assert usdc.token == "USDC"
        assert usdc.available == Decimal("100.50")
        assert usdc.total == Decimal("100.50")
        assert usdc.locked == Decimal("0")
        assert usdc.token_address == "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

        # Verify WETH collateral
        weth = collaterals[1]
        assert weth.token == "WETH"
        assert weth.available == Decimal("0.5")
        assert weth.total == Decimal("1.0")
        assert weth.locked == Decimal("0.5")

        # Verify locked calculation
        assert weth.total == weth.available + weth.locked


def test_api_error_handling(mock_client):
    """Test proper error handling when API returns errors."""
    with patch.object(mock_client, "_make_jsonrpc_request") as mock_request:
        # Mock API error
        mock_request.side_effect = ValueError("Authentication required")

        # Should raise the error
        with pytest.raises(ValueError, match="Authentication required"):
            fetch_account_collaterals(mock_client)


def test_missing_session_key_raises_error():
    """Test that missing session key raises appropriate error."""
    client = DeriveApiClient(
        owner_account=Account.create(),
        derive_wallet_address="0x1234567890123456789012345678901234567890",
        is_testnet=True,
    )
    # No session key set

    with pytest.raises(ValueError, match="Session key required"):
        fetch_account_collaterals(client)


def test_partial_collateral_data_handling(mock_client):
    """Test handling of partial or missing collateral fields."""
    with patch.object(mock_client, "_make_jsonrpc_request") as mock_request:
        # Mock response with missing optional fields
        mock_request.return_value = {
            "collaterals": [
                {
                    "token": "UNKNOWN",  # Using 'token' instead of 'currency'
                    "available": "10",
                    "total": "10",
                    # No 'locked' field
                    # No 'token_address'
                },
            ]
        }

        # Fetch collaterals
        collaterals = fetch_account_collaterals(mock_client)

        # Should handle missing fields gracefully
        assert len(collaterals) == 1
        col = collaterals[0]
        assert col.token == "UNKNOWN"
        assert col.available == Decimal("10")
        assert col.total == Decimal("10")
        assert col.locked == Decimal("0")  # Default to 0
        assert col.token_address is None  # Default to None
