"""Derive API client with session key authentication.

This module provides the DeriveApiClient for authenticating with Derive.xyz API
using session keys and EIP-712 signing.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from eth_account import messages
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress
from requests import Session
from web3 import Web3

from eth_defi.derive.constants import (
    DERIVE_CHAIN_ID,
    DERIVE_MAINNET_API_URL,
    DERIVE_TESTNET_API_URL,
    SessionKeyScope,
)
from eth_defi.derive.session import create_derive_session

logger = logging.getLogger(__name__)


class DeriveApiClient:
    """Derive API client with session key authentication.

    This client handles:

    - Session key registration with owner signature
    - Authenticated JSON-RPC 2.0 requests
    - Account and subaccount creation

    Example::

        from eth_account import Account
        from eth_defi.derive.authentication import DeriveApiClient, SessionKeyScope

        # Initialize with owner account
        owner = Account.from_key("0x...")
        client = DeriveApiClient(
            owner_account=owner,
            derive_wallet_address="0x...",
            is_testnet=True,
        )

        # Register session key
        session_info = client.register_session_key(
            scope=SessionKeyScope.read_only,
            expiry_hours=24,
        )

        # Use session key for authenticated requests
        client.session_key_private = session_info["session_key_private"]
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
            Derive wallet address (smart contract wallet on Derive Chain).
            This is NOT your EOA - find it at Derive.xyz → Developers.
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

    def register_session_key(
        self,
        scope: SessionKeyScope = SessionKeyScope.read_only,
        expiry_hours: int = 24,
        label: str = "python-sdk",
        ip_whitelist: list[str] | None = None,
    ) -> dict[str, str]:
        """Register a new session key with owner signature.

        This generates a new Ethereum wallet to use as a session key and registers it
        with the Derive API using an EIP-712 signature from the owner account.

        Example::

            session_info = client.register_session_key(
                scope=SessionKeyScope.read_only,
                expiry_hours=24,
            )

            # Save these credentials
            print(f"Session key address: {session_info['session_key_address']}")
            print(f"Session key private: {session_info['session_key_private']}")

            # Use for authenticated requests
            client.session_key_private = session_info["session_key_private"]

        :param scope:
            Permission level for the session key.
        :param expiry_hours:
            How many hours until the session key expires.
        :param label:
            Human-readable label for the session key.
        :param ip_whitelist:
            List of IP addresses allowed to use this key (empty = any IP).
        :return:
            Dict with 'session_key_address' and 'session_key_private'
        :raises ValueError:
            If owner_account or derive_wallet_address not set
        """
        if not self.owner_account:
            raise ValueError("owner_account is required to register session key")
        if not self.derive_wallet_address:
            raise ValueError("derive_wallet_address is required to register session key")

        # Generate new session key wallet
        from eth_account import Account

        session_key_account = Account.create()

        logger.info(
            "Registering session key %s for wallet %s",
            session_key_account.address,
            self.derive_wallet_address,
        )

        # Build registration message
        timestamp_sec = int(datetime.now(UTC).timestamp())
        expiry_sec = timestamp_sec + (expiry_hours * 3600)

        # Note: The exact message structure may need adjustment based on Derive's API documentation
        register_params = {
            "wallet": self.derive_wallet_address,
            "session_key": session_key_account.address,
            "scope": scope.value,
            "label": label,
            "expiry_sec": expiry_sec,
            "ip_whitelist": ip_whitelist or [],
        }

        # Create EIP-712 signature
        # Note: This structure is based on common patterns and may need adjustment
        domain_data = {
            "name": "Derive",
            "version": "1",
            "chainId": DERIVE_CHAIN_ID,
        }

        message_types = {
            "RegisterSessionKey": [
                {"name": "wallet", "type": "address"},
                {"name": "sessionKey", "type": "address"},
                {"name": "scope", "type": "string"},
                {"name": "expiry", "type": "uint256"},
            ]
        }

        message_data = {
            "wallet": self.derive_wallet_address,
            "sessionKey": session_key_account.address,
            "scope": scope.value,
            "expiry": expiry_sec,
        }

        encoded_data = messages.encode_typed_data(
            domain_data=domain_data,
            message_types=message_types,
            message_data=message_data,
        )

        signed_message = self.owner_account.sign_message(encoded_data)

        # Add signature to parameters
        register_params["signature"] = signed_message.signature.hex()
        register_params["owner_address"] = self.owner_account.address

        # Submit to API
        try:
            result = self._make_jsonrpc_request(
                method="private/register_session_key",
                params=register_params,
                authenticated=False,
            )
            logger.info("Session key registered successfully")
        except ValueError as e:
            logger.error("Failed to register session key: %s", e)
            raise

        return {
            "session_key_address": session_key_account.address,
            "session_key_private": session_key_account.key.hex(),
        }

    def _make_jsonrpc_request(
        self,
        method: str,
        params: dict[str, Any],
        authenticated: bool = False,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Make JSON-RPC 2.0 request to Derive API.

        :param method:
            JSON-RPC method name (e.g., "private/get_collaterals")
        :param params:
            Method parameters as dictionary
        :param authenticated:
            Whether to include authentication headers
        :param timeout:
            HTTP request timeout in seconds
        :return:
            Response result field (unwrapped from JSON-RPC envelope)
        :raises ValueError:
            If JSON-RPC returns error response or request fails
        """
        request_id = int(datetime.now(UTC).timestamp() * 1000)

        request_body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        headers = {"Content-Type": "application/json"}

        # Add authentication headers if needed
        if authenticated and self.session_key_private:
            if not self.derive_wallet_address:
                raise ValueError("derive_wallet_address required for authenticated requests")

            headers.update(self._sign_request_headers(method, params))
            headers["X-LyraWallet"] = self.derive_wallet_address

        # Make request
        url = f"{self.base_url}"
        logger.debug("Making JSON-RPC request to %s: method=%s", url, method)

        response = self.session.post(
            url,
            json=request_body,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()

        data = response.json()

        # Check for JSON-RPC error
        if "error" in data:
            error = data["error"]
            error_msg = f"JSON-RPC error {error.get('code', 'unknown')}: {error.get('message', 'no message')}"
            logger.error("API error: %s", error_msg)
            raise ValueError(error_msg)

        return data.get("result", {})

    def _sign_request_headers(self, method: str, params: dict[str, Any]) -> dict[str, str]:
        """Generate authentication headers for signed requests.

        Creates signature headers following Derive's authentication pattern.

        :param method:
            JSON-RPC method name
        :param params:
            Request parameters
        :return:
            Dictionary of authentication headers
        """
        from eth_account import Account

        timestamp = int(datetime.now(UTC).timestamp() * 1000)

        # Create message to sign
        # Format: timestamp + method + params (sorted JSON)
        params_str = json.dumps(params, separators=(",", ":"), sort_keys=True)
        message = f"{timestamp}{method}{params_str}"

        # Sign with session key
        session_account = Account.from_key(self.session_key_private)
        message_hash = Web3.keccak(text=message)
        signature = session_account.signHash(message_hash)

        logger.debug("Signing request with session key %s", session_account.address)

        return {
            "X-Timestamp": str(timestamp),
            "X-SessionKey": session_account.address,
            "X-Signature": signature.signature.hex(),
        }
