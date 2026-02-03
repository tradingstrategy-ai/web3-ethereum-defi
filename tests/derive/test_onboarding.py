"""Test Derive onboarding and session key flow.

Integration tests need network access to the Derive testnet API
and require an existing account created via the Derive web interface.

Required environment variables for integration tests:

- ``DERIVE_OWNER_PRIVATE_KEY``: Owner wallet private key (from web UI wallet)
- ``DERIVE_SESSION_PRIVATE_KEY``: Session key private key (from testnet developer page)
"""

import os
import logging

import pytest
from eth_account import Account

from eth_defi.derive.authentication import DeriveApiClient
from eth_defi.derive.onboarding import (
    fetch_derive_wallet_address,
    verify_session_key,
)


logger = logging.getLogger(__name__)


pytestmark = pytest.mark.skipif(
    not os.environ.get("DERIVE_OWNER_PRIVATE_KEY") or not os.environ.get("DERIVE_SESSION_PRIVATE_KEY"),
    reason="Set DERIVE_OWNER_PRIVATE_KEY and DERIVE_SESSION_PRIVATE_KEY to run Derive integration tests. See tests/derive/derive-test-key-setup.md",
)


@pytest.fixture(scope="module")
def owner_private_key():
    return os.environ["DERIVE_OWNER_PRIVATE_KEY"]


@pytest.fixture(scope="module")
def session_key_private():
    return os.environ["DERIVE_SESSION_PRIVATE_KEY"]


@pytest.fixture(scope="module")
def owner_account(owner_private_key):
    return Account.from_key(owner_private_key)


@pytest.fixture(scope="module")
def derive_wallet_address(owner_account):
    """Resolve LightAccount wallet address from owner EOA."""
    return fetch_derive_wallet_address(owner_account.address, is_testnet=True)


@pytest.fixture(scope="module")
def authenticated_client(owner_account, derive_wallet_address, session_key_private):
    """Derive API client authenticated with the session key from web UI."""
    return DeriveApiClient(
        owner_account=owner_account,
        derive_wallet_address=derive_wallet_address,
        is_testnet=True,
        session_key_private=session_key_private,
    )


def test_derive_wallet_address_resolution(owner_account, derive_wallet_address):
    """Resolve the LightAccount wallet address for an owner EOA."""
    assert derive_wallet_address.startswith("0x")
    assert len(derive_wallet_address) == 42
    # LightAccount address should differ from owner EOA
    assert derive_wallet_address.lower() != owner_account.address.lower()
    logger.info("Owner %s -> LightAccount %s", owner_account.address, derive_wallet_address)



def test_session_key_authentication(authenticated_client):
    """Verify the session key from the web UI can authenticate API requests."""
    result = authenticated_client._make_jsonrpc_request(
        method="private/get_subaccounts",
        params={"wallet": authenticated_client.derive_wallet_address},
        authenticated=True,
    )

    assert isinstance(result, (dict, list))
    logger.info("get_subaccounts result: %s", result)


def test_session_key_verification(authenticated_client):
    """Verify the session key can read account collateral data."""
    assert verify_session_key(authenticated_client)
