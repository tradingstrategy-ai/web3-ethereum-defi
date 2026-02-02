"""Test Derive API with real credentials.

This test uses actual Derive.xyz API with real credentials to verify
that account data reads work correctly.

Required environment variables:

- ``DERIVE_OWNER_PRIVATE_KEY``: Owner wallet private key (from web UI wallet)
- ``DERIVE_SESSION_PRIVATE_KEY``: Session key private key (from testnet developer page)
- ``DERIVE_WALLET_ADDRESS``: Derive wallet address (optional, auto-derived from owner key)

See tests/derive/derive-test-key-setup.md for detailed instructions.
"""

import os
from decimal import Decimal

import pytest
from eth_account import Account

from eth_defi.derive.account import fetch_account_collaterals, fetch_account_summary, fetch_subaccount_ids
from eth_defi.derive.authentication import DeriveApiClient
from eth_defi.derive.onboarding import fetch_derive_wallet_address

# Skip tests if not configured
pytestmark = pytest.mark.skipif(
    not os.environ.get("DERIVE_OWNER_PRIVATE_KEY") or not os.environ.get("DERIVE_SESSION_PRIVATE_KEY"),
    reason="Set DERIVE_OWNER_PRIVATE_KEY and DERIVE_SESSION_PRIVATE_KEY to run real API tests. See tests/derive/derive-test-key-setup.md",
)


@pytest.fixture(scope="module")
def owner_account():
    """Owner wallet from environment."""
    return Account.from_key(os.environ["DERIVE_OWNER_PRIVATE_KEY"])


@pytest.fixture(scope="module")
def derive_wallet_address(owner_account):
    """Derive wallet address from env, or auto-derived from owner key."""
    env_address = os.environ.get("DERIVE_WALLET_ADDRESS")
    if env_address:
        return env_address
    return fetch_derive_wallet_address(owner_account.address, is_testnet=True)


@pytest.fixture(scope="module")
def authenticated_client(owner_account, derive_wallet_address):
    """Derive API client authenticated with session key from web UI.

    Automatically resolves the first subaccount ID from the API.
    """
    client = DeriveApiClient(
        owner_account=owner_account,
        derive_wallet_address=derive_wallet_address,
        is_testnet=True,
        session_key_private=os.environ["DERIVE_SESSION_PRIVATE_KEY"],
    )
    # Resolve real subaccount ID from the API
    ids = fetch_subaccount_ids(client)
    if ids:
        client.subaccount_id = ids[0]
    return client


def test_real_account_collaterals(authenticated_client):
    """Test fetching collaterals from real Derive account.

    This test works whether your account is empty or has funds.
    If empty, it verifies the API returns [] without errors.
    """
    ids = fetch_subaccount_ids(authenticated_client)
    if not ids:
        pytest.skip("Account has no subaccounts yet")

    collaterals = fetch_account_collaterals(authenticated_client)

    assert isinstance(collaterals, list), "Collaterals should be a list"

    for col in collaterals:
        assert isinstance(col.available, Decimal), f"{col.token} available should be Decimal"
        assert isinstance(col.total, Decimal), f"{col.token} total should be Decimal"
        assert col.total >= col.available, f"{col.token} total should be >= available"


def test_real_account_summary(authenticated_client):
    """Test fetching complete account summary from real Derive account.

    This fetches collaterals, account info, and margin data.
    """
    ids = fetch_subaccount_ids(authenticated_client)
    if not ids:
        pytest.skip("Account has no subaccounts yet")

    summary = fetch_account_summary(authenticated_client)

    assert summary.account_address.lower() == authenticated_client.derive_wallet_address.lower()
    assert summary.subaccount_id == authenticated_client.subaccount_id
    assert isinstance(summary.collaterals, list)
    assert isinstance(summary.total_value_usd, Decimal)

    if len(summary.collaterals) == 0:
        assert summary.total_value_usd == Decimal("0"), "Empty account should have zero value"


def test_session_key_scope_read_only(authenticated_client):
    """Verify that session key can read account data."""
    ids = fetch_subaccount_ids(authenticated_client)
    if not ids:
        pytest.skip("Account has no subaccounts yet")

    collaterals = fetch_account_collaterals(authenticated_client)
    assert isinstance(collaterals, list)
