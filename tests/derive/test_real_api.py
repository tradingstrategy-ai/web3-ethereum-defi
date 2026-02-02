"""Test Derive API with real credentials and empty account.

This test uses actual Derive.xyz API with real credentials to verify
that empty accounts work correctly.

Setup required:
1. Set DERIVE_OWNER_PRIVATE_KEY (wallet with Sepolia ETH)
2. Set DERIVE_WALLET_ADDRESS (from testnet.derive.xyz)
3. Set SEND_REAL_REQUESTS=true

See SETUP_REAL_API.md for detailed instructions.
"""

import os
from decimal import Decimal

import pytest
from eth_account import Account

from eth_defi.derive.account import fetch_account_collaterals, fetch_account_summary
from eth_defi.derive.authentication import DeriveApiClient
from eth_defi.derive.constants import SessionKeyScope

# Environment variables
DERIVE_OWNER_PRIVATE_KEY = os.environ.get("DERIVE_OWNER_PRIVATE_KEY")
DERIVE_WALLET_ADDRESS = os.environ.get("DERIVE_WALLET_ADDRESS")
DERIVE_SESSION_KEY_PRIVATE = os.environ.get("DERIVE_SESSION_KEY_PRIVATE")
SEND_REAL_REQUESTS = os.environ.get("SEND_REAL_REQUESTS", "false").lower() == "true"

# Skip tests if not configured
pytestmark = pytest.mark.skipif(
    not SEND_REAL_REQUESTS or not DERIVE_OWNER_PRIVATE_KEY or not DERIVE_WALLET_ADDRESS,
    reason="Set SEND_REAL_REQUESTS=true, DERIVE_OWNER_PRIVATE_KEY, and DERIVE_WALLET_ADDRESS to run real API tests",
)


@pytest.fixture(scope="module")
def owner_account():
    """Owner wallet from environment."""
    return Account.from_key(DERIVE_OWNER_PRIVATE_KEY)


@pytest.fixture(scope="module")
def derive_client(owner_account):
    """Derive API client with real credentials."""
    return DeriveApiClient(
        owner_account=owner_account,
        derive_wallet_address=DERIVE_WALLET_ADDRESS,
        is_testnet=True,
    )


@pytest.fixture(scope="module")
def authenticated_client(derive_client):
    """Client with registered session key.

    This will use existing session key if DERIVE_SESSION_KEY_PRIVATE is set,
    otherwise it will register a new one.
    """
    if DERIVE_SESSION_KEY_PRIVATE:
        # Use existing session key
        derive_client.session_key_private = DERIVE_SESSION_KEY_PRIVATE
    else:
        # Register new session key
        session_info = derive_client.register_session_key(
            scope=SessionKeyScope.read_only,
            label="pytest-empty-account-test",
            expiry_hours=24,
        )
        derive_client.session_key_private = session_info["session_key_private"]

    return derive_client


def test_real_empty_account_collaterals(authenticated_client):
    """Test fetching collaterals from real Derive account.

    This test works whether your account is empty or has funds.
    If empty, it verifies the API returns [] without errors.
    """
    # Fetch collaterals
    collaterals = fetch_account_collaterals(authenticated_client)

    # Verify structure
    assert isinstance(collaterals, list), "Collaterals should be a list"

    # Verify data types and invariants if account has funds
    for col in collaterals:
        assert isinstance(col.available, Decimal), f"{col.token} available should be Decimal"
        assert isinstance(col.total, Decimal), f"{col.token} total should be Decimal"
        assert col.total >= col.available, f"{col.token} total should be >= available"


def test_real_empty_account_summary(authenticated_client):
    """Test fetching complete account summary from real Derive account.

    This fetches collaterals, account info, and margin data.
    """
    # Fetch summary
    summary = fetch_account_summary(authenticated_client)

    # Verify structure
    assert summary.account_address.lower() == authenticated_client.derive_wallet_address.lower()
    assert summary.subaccount_id == authenticated_client.subaccount_id
    assert isinstance(summary.collaterals, list)
    assert isinstance(summary.total_value_usd, Decimal)

    # Empty account check
    if len(summary.collaterals) == 0:
        assert summary.total_value_usd == Decimal("0"), "Empty account should have zero value"


def test_session_key_scope_read_only(authenticated_client):
    """Verify that read-only session key can read but not modify."""
    # This test just verifies we can read data
    # In future, could test that write operations fail
    collaterals = fetch_account_collaterals(authenticated_client)
    assert isinstance(collaterals, list)
