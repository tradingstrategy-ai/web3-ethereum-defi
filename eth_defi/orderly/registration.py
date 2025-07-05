from datetime import UTC, datetime

import requests
from base58 import b58encode
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from eth_account import messages
from eth_account.signers.local import LocalAccount

from eth_defi.orderly.constants import MESSAGE_TYPES, OFF_CHAIN_DOMAIN


def encode_key(key: bytes) -> str:
    return f"ed25519:{b58encode(key).decode('utf-8')}"


def register_orderly_account(
    *,
    account: LocalAccount,
    broker_id: str,
    chain_id: int,
    is_testnet: bool = False,
) -> str:
    """
    Register a new orderly account for the given account.

    See also: https://orderly.network/docs/build-on-omnichain/user-flows/accounts

    :param account:
        The local account to register the account for.
    :param broker_id:
        The broker ID to register the account for.
    :param chain_id:
        The chain ID to register the account for.
    :param is_testnet:
        Whether to use the testnet API endpoint.
    """
    if is_testnet:
        base_url = "https://testnet-api.orderly.org"
    else:
        base_url = "https://api.orderly.org"

    r = requests.get(f"{base_url}/v1/registration_nonce", timeout=5)
    r.raise_for_status()
    response = r.json()
    registration_nonce = response["data"]["registration_nonce"]

    timestamp = int(datetime.now(UTC).timestamp() * 1_000)

    register_message = {
        "brokerId": broker_id,
        "chainId": chain_id,
        "timestamp": timestamp,
        "registrationNonce": registration_nonce,
    }

    encoded_data = messages.encode_typed_data(
        domain_data=OFF_CHAIN_DOMAIN,
        message_types={"Registration": MESSAGE_TYPES["Registration"]},
        message_data=register_message,
    )
    signed_message = account.sign_message(encoded_data)

    r = requests.post(
        f"{base_url}/v1/register_account",
        json={
            "message": register_message,
            "signature": signed_message.signature.hex(),
            "userAddress": account.address,
        },
        timeout=5,
    )
    r.raise_for_status()
    response = r.json()

    return response["data"]["account_id"]


def register_orderly_key(
    *,
    account: LocalAccount,
    broker_id: str,
    chain_id: int,
    permissions: str = "read,trading",
    is_testnet: bool = False,
) -> dict:
    """
    Register a new orderly key for the given account.

    See also: https://orderly.network/docs/build-on-omnichain/user-flows/wallet-authentication

    :param account:
        The local account to register the key for.
    :param broker_id:
        The broker ID to register the key for.
    :param chain_id:
        The chain ID to register the key for.
    :param permissions:
        The permissions to register the key for.
    :param is_testnet:
        Whether to use the testnet API endpoint.
    """
    orderly_key = Ed25519PrivateKey.generate()
    encoded_public_key = encode_key(orderly_key.public_key().public_bytes_raw())

    timestamp = int(datetime.now(UTC).timestamp() * 1_000)
    expiration = timestamp + 1_000 * 60 * 60  # 1 hour

    add_key_message = {
        "brokerId": broker_id,
        "chainId": chain_id,
        "orderlyKey": encoded_public_key,
        "scope": permissions,
        "timestamp": timestamp,
        "expiration": expiration,
    }

    encoded_data = messages.encode_typed_data(
        domain_data=OFF_CHAIN_DOMAIN,
        message_types={"AddOrderlyKey": MESSAGE_TYPES["AddOrderlyKey"]},
        message_data=add_key_message,
    )
    signed_message = account.sign_message(encoded_data)

    if is_testnet:
        base_url = "https://testnet-api.orderly.org"
    else:
        base_url = "https://api.orderly.org"

    r = requests.post(
        f"{base_url}/v1/orderly_key",
        json={
            "message": add_key_message,
            "signature": signed_message.signature.hex(),
            "userAddress": account.address,
        },
        timeout=5,
    )
    r.raise_for_status()
    response = r.json()

    return response
