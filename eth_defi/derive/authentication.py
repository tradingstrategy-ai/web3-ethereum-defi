"""Derive API client with session key authentication.

This module provides the DeriveApiClient for authenticating with Derive.xyz API
using session keys and EIP-191 personal-sign authentication.

Authentication uses the ``encode_defunct(text=timestamp)`` pattern: the client
signs a millisecond timestamp string with either a session key or the owner
wallet key, and sends the signature in ``X-LYRASIGNATURE`` / ``X-LYRAWALLET``
/ ``X-LYRATIMESTAMP`` headers.

.. note::

    Header names use uppercase (``X-LYRAWALLET`` etc.) to match the canonical
    format used by the official ``derive_action_signing`` package.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from requests import Session

from eth_defi.derive.constants import (
    DERIVE_MAINNET_API_URL,
    DERIVE_TESTNET_API_URL,
)
from eth_defi.derive.session import create_derive_session

logger = logging.getLogger(__name__)


class DeriveApiClient:
    """Derive API client with session key authentication.

    This client handles:

    - Authenticated JSON-RPC 2.0 requests via HTTP
    - Signing requests with EIP-191 personal-sign (encode_defunct)

    Authentication headers follow the Derive convention::

        X-LYRAWALLET:    <smart contract wallet address>
        X-LYRATIMESTAMP: <UTC timestamp in milliseconds>
        X-LYRASIGNATURE: <EIP-191 signature of the timestamp string>

    Example::

        from eth_account import Account
        from eth_defi.derive.authentication import DeriveApiClient

        # Initialise with owner account
        owner = Account.from_key("0x...")
        client = DeriveApiClient(
            owner_account=owner,
            derive_wallet_address="0x...",
            is_testnet=True,
        )

        # Make authenticated request using owner key directly
        result = client._make_jsonrpc_request(
            method="private/get_subaccounts",
            params={"wallet": client.derive_wallet_address},
            authenticated=True,
        )
    """

    def __init__(
        self,
        owner_account: LocalAccount | None = None,
        derive_wallet_address: HexAddress | None = None,
        subaccount_id: int = 1,
        is_testnet: bool = False,
        session_key_private: str = "",
    ):
        """Initialise Derive API client.

        :param owner_account:
            Owner wallet (EOA) for signing session key registrations.
        :param derive_wallet_address:
            Derive wallet address (LightAccount smart contract wallet on Derive Chain).
            For manual onboarding this equals the counterfactual LightAccount
            address derived from the owner EOA.
        :param subaccount_id:
            Subaccount ID to use for requests (defaults to 1).
        :param is_testnet:
            Whether to use testnet API endpoint.
        :param session_key_private:
            Private key of registered session key (hex string starting with 0x).
        """
        self.owner_account = owner_account
        self.derive_wallet_address = derive_wallet_address
        self.subaccount_id = subaccount_id
        self.is_testnet = is_testnet
        self.session_key_private = session_key_private

        if is_testnet:
            self.base_url = DERIVE_TESTNET_API_URL
        else:
            self.base_url = DERIVE_MAINNET_API_URL

        self.session = create_derive_session()

    def _get_signing_key(self) -> str:
        """Return the best available private key for signing requests.

        Prefers session key if available, falls back to owner wallet key.

        :return:
            Private key hex string.
        :raises ValueError:
            If neither session key nor owner account is available.
        """
        if self.session_key_private:
            return self.session_key_private
        if self.owner_account:
            return self.owner_account.key.hex()
        raise ValueError("No signing key available (need session_key_private or owner_account)")

    def _sign_auth_headers(self, private_key: str | None = None) -> dict[str, str]:
        """Generate Derive authentication headers.

        Signs the current UTC timestamp (milliseconds) using EIP-191
        personal-sign (``encode_defunct``).

        :param private_key:
            Private key to sign with. If None, uses :meth:`_get_signing_key`.
        :return:
            Dict with ``X-LYRAWALLET``, ``X-LYRATIMESTAMP``, ``X-LYRASIGNATURE``.
        :raises ValueError:
            If derive_wallet_address is not set.
        """
        if not self.derive_wallet_address:
            raise ValueError("derive_wallet_address required for authenticated requests")

        if private_key is None:
            private_key = self._get_signing_key()

        timestamp = str(int(datetime.now(UTC).timestamp() * 1000))

        signer = Account.from_key(private_key)
        signed = signer.sign_message(encode_defunct(text=timestamp))

        logger.debug(
            "Signed auth header with %s for wallet %s",
            signer.address,
            self.derive_wallet_address,
        )

        return {
            "X-LYRAWALLET": self.derive_wallet_address,
            "X-LYRATIMESTAMP": timestamp,
            "X-LYRASIGNATURE": signed.signature.hex(),
        }

    def _make_jsonrpc_request(
        self,
        method: str,
        params: dict[str, Any],
        authenticated: bool = False,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Make JSON-RPC 2.0 request to Derive API.

        Derive uses method-specific URL paths with the params dict as POST body.
        For example, ``private/get_collaterals`` becomes::

            POST https://api-demo.lyra.finance/private/get_collaterals

        :param method:
            JSON-RPC method name (e.g., ``private/get_collaterals``).
        :param params:
            Method parameters as dictionary.
        :param authenticated:
            Whether to include authentication headers.
        :param timeout:
            HTTP request timeout in seconds.
        :return:
            Response result field (unwrapped from JSON-RPC envelope).
        :raises ValueError:
            If JSON-RPC returns error response or request fails.
        """
        url = f"{self.base_url}/{method}"

        headers = {"Content-Type": "application/json"}

        if authenticated:
            headers.update(self._sign_auth_headers())

        logger.debug("Making request to %s", url)

        response = self.session.post(
            url,
            json=params,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()

        data = response.json()

        # Check for JSON-RPC error
        if "error" in data:
            error = data["error"]
            error_data = error.get("data", "")
            error_msg = f"JSON-RPC error {error.get('code', 'unknown')}: {error.get('message', 'no message')}"
            if error_data:
                error_msg += f" (data: {error_data})"
            logger.error("API error for %s: %s", method, error_msg)
            raise ValueError(error_msg)

        return data.get("result", {})
