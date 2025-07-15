from datetime import UTC, datetime

import requests
from base58 import b58encode
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from eth_account import messages
from eth_account.signers.local import LocalAccount

from eth_defi.orderly.constants import MESSAGE_TYPES, OFF_CHAIN_DOMAIN


class OrderlyApiClient:
    def __init__(
        self,
        *,
        account: LocalAccount,
        broker_id: str,
        chain_id: int,
        is_testnet: bool = False,
    ):
        """
        :param account:
            The local account to register the account for.
        :param broker_id:
            The broker ID to register the account for.
        :param chain_id:
            The chain ID to register the account for.
        :param is_testnet:
            Whether to use the testnet API endpoint.
        """
        self.account = account
        self.broker_id = broker_id
        self.chain_id = chain_id
        self.is_testnet = is_testnet

        if is_testnet:
            self.base_url = "https://testnet-api.orderly.org"
        else:
            self.base_url = "https://api.orderly.org"

    def register_account(
        self,
    ) -> str:
        """
        Register a new orderly account for the given account.

        See also: https://orderly.network/docs/build-on-omnichain/user-flows/accounts

        """
        registration_nonce = self._get_registration_nonce()

        timestamp = int(datetime.now(UTC).timestamp() * 1_000)

        register_message = {
            "brokerId": self.broker_id,
            "chainId": self.chain_id,
            "timestamp": timestamp,
            "registrationNonce": registration_nonce,
        }

        encoded_data = messages.encode_typed_data(
            domain_data=OFF_CHAIN_DOMAIN,
            message_types={"Registration": MESSAGE_TYPES["Registration"]},
            message_data=register_message,
        )
        signed_message = self.account.sign_message(encoded_data)

        r = requests.post(
            f"{self.base_url}/v1/register_account",
            json={
                "message": register_message,
                "signature": signed_message.signature.hex(),
                "userAddress": self.account.address,
            },
            timeout=5,
        )
        r.raise_for_status()
        response = r.json()

        return response["data"]["account_id"]

    def register_key(
        self,
        *,
        permissions: str = "read,trading",
        delegate_contract: str | None = None,
    ) -> dict:
        """
        Register a new orderly key for the given account.

        See also: https://orderly.network/docs/build-on-omnichain/user-flows/wallet-authentication

        :param permissions:
            The permissions to register the key for.
        :param delegate_contract:
            The contract (which delegated signing right to this account) to register the key for.
        """
        orderly_key = Ed25519PrivateKey.generate()
        encoded_public_key = self._encode_key(orderly_key.public_key().public_bytes_raw())

        timestamp = int(datetime.now(UTC).timestamp() * 1_000)
        expiration = timestamp + 1_000 * 60 * 60  # 1 hour

        add_key_message = {
            "brokerId": self.broker_id,
            "chainId": self.chain_id,
            "orderlyKey": encoded_public_key,
            "scope": permissions,
            "timestamp": timestamp,
            "expiration": expiration,
        }

        message_types = {"AddOrderlyKey": MESSAGE_TYPES["AddOrderlyKey"]}
        api_url = f"{self.base_url}/v1/orderly_key"
        if delegate_contract:
            add_key_message["delegateContract"] = delegate_contract
            message_types = {"DelegateAddOrderlyKey": MESSAGE_TYPES["DelegateAddOrderlyKey"]}
            api_url = f"{self.base_url}/v1/delegate_orderly_key"

        encoded_data = messages.encode_typed_data(
            domain_data=OFF_CHAIN_DOMAIN,
            message_types=message_types,
            message_data=add_key_message,
        )
        signed_message = self.account.sign_message(encoded_data)

        r = requests.post(
            api_url,
            json={
                "message": add_key_message,
                "signature": signed_message.signature.hex(),
                "userAddress": self.account.address,
            },
            timeout=5,
        )
        r.raise_for_status()
        response = r.json()

        return response

    def delegate_signer(
        self,
        *,
        delegate_contract: str,
        delegate_tx_hash: str,
    ) -> dict:
        """
        Register a new orderly key for the given account.

        See also: https://orderly.network/docs/build-on-omnichain/user-flows/wallet-authentication

        :param permissions:
            The permissions to register the key for.
        """
        registration_nonce = self._get_registration_nonce()

        timestamp = int(datetime.now(UTC).timestamp() * 1_000)
        expiration = timestamp + 1_000 * 60 * 60  # 1 hour

        delegate_message = {
            "delegateContract": delegate_contract,
            "brokerId": self.broker_id,
            "chainId": self.chain_id,
            "timestamp": timestamp,
            "registrationNonce": registration_nonce,
            "txHash": delegate_tx_hash,
        }

        encoded_data = messages.encode_typed_data(
            domain_data=OFF_CHAIN_DOMAIN,
            message_types={"DelegateSigner": MESSAGE_TYPES["DelegateSigner"]},
            message_data=delegate_message,
        )
        signed_message = self.account.sign_message(encoded_data)

        r = requests.post(
            f"{self.base_url}/v1/delegate_signer",
            json={
                "message": delegate_message,
                "signature": signed_message.signature.hex(),
                "userAddress": self.account.address,
            },
            timeout=5,
        )
        r.raise_for_status()
        response = r.json()

        return response

    def _get_registration_nonce(self) -> int:
        r = requests.get(f"{self.base_url}/v1/registration_nonce", timeout=5)
        r.raise_for_status()
        response = r.json()
        registration_nonce = response["data"]["registration_nonce"]
        return registration_nonce

    def _encode_key(self, key: bytes) -> str:
        return f"ed25519:{b58encode(key).decode('utf-8')}"
