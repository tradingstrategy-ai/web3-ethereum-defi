"""Shared pytest fixtures for Derive tests.

This module provides common fixtures used across all Derive test modules.
"""

import pytest
from eth_account import Account

from eth_defi.derive.authentication import DeriveApiClient


@pytest.fixture
def test_wallet():
    """Fresh wallet for testing."""
    return Account.create()


@pytest.fixture
def mock_derive_client(test_wallet):
    """Mock Derive client for testing without API credentials."""
    client = DeriveApiClient(
        owner_account=test_wallet,
        derive_wallet_address="0x1234567890123456789012345678901234567890",
        is_testnet=True,
    )
    # Set a fake session key so authentication checks pass
    client.session_key_private = "0x" + "a" * 64
    return client
