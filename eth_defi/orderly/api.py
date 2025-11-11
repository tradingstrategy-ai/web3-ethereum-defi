"""Orderly API client implementation."""

import json
import urllib
from base64 import urlsafe_b64encode
from datetime import UTC, datetime

import requests
from base58 import b58decode, b58encode
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from eth_account import messages
from eth_account.signers.local import LocalAccount
from requests import PreparedRequest, Request, Session

from eth_defi.orderly.constants import MESSAGE_TYPES


class OrderlyApiClient:
    """Orderly API client for deposit/withdrawals.

    - This client is responsible for registering accounts and keys with Orderly,
      so that we can delegate a vault to be a trading account on Orderly.
    """

    def __init__(
        self,
        *,
        account: LocalAccount | None = None,
        broker_id: str | None = None,
        chain_id: int | None = None,
        is_testnet: bool = False,
        orderly_account_id: str = "",
        orderly_secret: str = "",
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
        self.orderly_account_id = orderly_account_id
        self.orderly_secret = orderly_secret

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
        self._validate_account_requests()

        registration_nonce = self._get_registration_nonce()

        timestamp = int(datetime.now(UTC).timestamp() * 1_000)

        register_message = {
            "brokerId": self.broker_id,
            "chainId": self.chain_id,
            "timestamp": timestamp,
            "registrationNonce": registration_nonce,
        }

        encoded_data = messages.encode_typed_data(
            domain_data=self._get_off_chain_domain(),
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
        self._validate_account_requests()

        orderly_key = Ed25519PrivateKey.generate()
        encoded_public_key = encode_key(orderly_key.public_key().public_bytes_raw())
        encoded_secret = encode_key(orderly_key.private_bytes_raw(), with_prefix=False)

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
            domain_data=self._get_off_chain_domain(),
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

        response["data"]["secret"] = encoded_secret
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
        self._validate_account_requests()

        registration_nonce = self._get_registration_nonce()

        timestamp = int(datetime.now(UTC).timestamp() * 1_000)

        delegate_message = {
            "delegateContract": delegate_contract,
            "brokerId": self.broker_id,
            "chainId": self.chain_id,
            "timestamp": timestamp,
            "registrationNonce": registration_nonce,
            "txHash": delegate_tx_hash,
        }

        encoded_data = messages.encode_typed_data(
            domain_data=self._get_off_chain_domain(),
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

    def get_balance(self) -> dict:
        self._validate_secret_requests()

        key = b58decode(self.orderly_secret)
        orderly_key = Ed25519PrivateKey.from_private_bytes(key)

        session = Session()
        signer = Signer(self.orderly_account_id, orderly_key)

        req = signer.sign_request(Request("GET", f"{self.base_url}/v1/client/holding"))
        r = session.send(req)
        return r.json()

    def _get_registration_nonce(self) -> int:
        r = requests.get(f"{self.base_url}/v1/registration_nonce", timeout=5)
        r.raise_for_status()
        response = r.json()
        registration_nonce = response["data"]["registration_nonce"]
        return registration_nonce

    def _get_off_chain_domain(self) -> dict:
        return {
            "name": "Orderly",
            "version": "1",
            "chainId": self.chain_id,
            "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC",
        }

    def _validate_secret_requests(self) -> None:
        if not self.orderly_account_id or not self.orderly_secret:
            raise ValueError("Orderly account ID and secret are required")

    def _validate_account_requests(self) -> None:
        if not self.account or not self.broker_id or not self.chain_id:
            raise ValueError("Account, broker ID, and chain ID are required")


class Signer:
    def __init__(
        self,
        account_id: str,
        private_key: Ed25519PrivateKey,
    ) -> None:
        self._account_id = account_id
        self._private_key = private_key

    def sign_request(self, req: Request) -> PreparedRequest:
        # d = datetime.utcnow()
        # epoch = datetime(1970, 1, 1)
        # timestamp = math.trunc((d - epoch).total_seconds() * 1_000)
        timestamp = int(datetime.now(UTC).timestamp() * 1_000)

        json_str = ""
        if req.json is not None:
            json_str = json.dumps(req.json)

        url = urllib.parse.urlparse(req.url)
        message = str(timestamp) + req.method + url.path + json_str
        if len(url.query) > 0:
            message += f"?{url.query}"

        orderly_signature = urlsafe_b64encode(self._private_key.sign(message.encode())).decode("utf-8")

        req.headers = {
            "orderly-timestamp": str(timestamp),
            "orderly-account-id": self._account_id,
            "orderly-key": encode_key(self._private_key.public_key().public_bytes_raw()),
            "orderly-signature": orderly_signature,
        }
        req.headers["Content-Type"] = "application/json"
        # print(req.headers)
        # if req.method in {"GET", "DELETE"}:
        #     req.headers["Content-Type"] = "application/x-www-form-urlencoded"
        # elif req.method in {"POST", "PUT"}:
        #     req.headers["Content-Type"] = "application/json"

        return req.prepare()


def encode_key(key: bytes, with_prefix: bool = True) -> str:
    encoded = b58encode(key).decode("utf-8")
    return f"ed25519:{encoded}" if with_prefix else encoded
