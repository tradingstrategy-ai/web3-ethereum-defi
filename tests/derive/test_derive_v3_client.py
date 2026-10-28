"""Test the Derive v3 API client against the live v3 API.

Uses a freshly generated wallet, so no credentials are needed. A fresh wallet
never has a Derive account, which gives a stable answer from both the public
and the authenticated endpoints.
"""

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from eth_defi.derive.account import fetch_subaccount_ids
from eth_defi.derive.authentication import DeriveApiClient
from eth_defi.derive.constants import DERIVE_V3_MAINNET_API_URL


@pytest.mark.timeout(60)
def test_derive_v3_client_public_and_signed_requests():
    """Check that the client discovers subaccounts and signs requests the way Derive v3 expects.

    Derive v3 renamed the authentication headers and treats the owner EOA as
    the wallet. A wrong header name or an unprefixed signature fails with an
    invalid params or authentication error, while correct headers for a
    wallet without an account fail with "Account not found".

    1. Create a client for a fresh owner EOA, which is its own v3 wallet.
    2. Check that public subaccount discovery returns an empty list.
    3. Check that the signed headers use the v3 names and recover to the owner.
    4. Check that a signed private request passes authentication and reports the missing account.
    """
    # 1. Create a client for a fresh owner EOA, which is its own v3 wallet.
    owner = Account.create()
    client = DeriveApiClient(owner_account=owner, derive_wallet_address=owner.address)
    assert client.base_url == DERIVE_V3_MAINNET_API_URL

    # 2. Check that public subaccount discovery returns an empty list.
    assert fetch_subaccount_ids(client) == []

    # 3. Check that the signed headers use the v3 names and recover to the owner.
    headers = client._sign_auth_headers()
    assert headers["X-DeriveWallet"] == owner.address
    assert headers["X-DeriveSignature"].startswith("0x")
    message = encode_defunct(text=headers["X-DeriveTimestamp"])
    assert Account.recover_message(message, signature=headers["X-DeriveSignature"]) == owner.address

    # 4. Check that a signed private request passes authentication and reports the missing account.
    with pytest.raises(ValueError, match="Account not found"):
        client._make_jsonrpc_request(
            method="private/get_subaccounts",
            params={"wallet": owner.address},
            authenticated=True,
        )
