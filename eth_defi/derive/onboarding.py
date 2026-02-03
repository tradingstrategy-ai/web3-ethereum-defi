"""Derive.xyz account onboarding and session key management.

This module provides onboarding helpers for the Derive.xyz exchange.

.. important::

    **Account creation and initial session key** require the Derive web interface at
    https://testnet.derive.xyz/ (testnet) or https://derive.xyz/ (mainnet).
    The web UI deploys an ERC-4337 LightAccount smart contract wallet via a
    gas-sponsored UserOperation and registers the initial session key.
    This step **cannot** be automated programmatically due to bot detection
    on the SIWE (Sign-In with Ethereum) endpoint and paymaster access controls.

Once an account and session key exist, this module provides:

1. **Wallet address resolution** -- Derives the counterfactual LightAccount
   address from an owner EOA using ``LightAccountFactory.getAddress(owner, 0)``
2. **Session key verification** -- Confirms the session key works by reading account data

Typical workflow::

    from eth_account import Account
    from eth_defi.derive.authentication import DeriveApiClient
    from eth_defi.derive.account import fetch_account_collaterals

    # Step 1: Create account via web UI at https://testnet.derive.xyz/
    # Step 2: Get credentials from the developer page (Home → Developers):
    #   - Derive Wallet address
    #   - Session Key private key
    # Step 3: Export your wallet private key from MetaMask:
    #   Account menu → "Account details" → "Show private key"

    client = DeriveApiClient(
        owner_account=Account.from_key("0x..."),  # Owner wallet private key
        derive_wallet_address="0x...",  # From developer page
        session_key_private="0x...",  # From developer page
        is_testnet=True,
    )

    collaterals = fetch_account_collaterals(client)

See also:

- `Derive API documentation <https://docs.derive.xyz/reference/overview>`__
- `Manual onboarding <https://docs.derive.xyz/reference/onboard-manually>`__
"""

import logging

from eth_account import Account
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.derive.authentication import DeriveApiClient
from eth_defi.derive.constants import DERIVE_MAINNET_RPC_URL, DERIVE_TESTNET_RPC_URL, LIGHT_ACCOUNT_FACTORY_ABI, LIGHT_ACCOUNT_FACTORY_ADDRESS

logger = logging.getLogger(__name__)


def create_owner_key() -> tuple[str, HexAddress]:
    """Create a fresh Ethereum EOA for use as Derive owner wallet.

    :return:
        Tuple of (private_key_hex, address)
    """
    account = Account.create()
    private_key = account.key.hex()
    logger.info("Created owner key: %s", account.address)
    return private_key, account.address


def create_session_key() -> tuple[str, HexAddress]:
    """Create a fresh Ethereum EOA for use as Derive session key.

    :return:
        Tuple of (private_key_hex, address)
    """
    account = Account.create()
    private_key = account.key.hex()
    logger.info("Created session key: %s", account.address)
    return private_key, account.address


def fetch_derive_wallet_address(
    owner_address: HexAddress,
    is_testnet: bool = True,
    salt: int = 0,
) -> HexAddress:
    """Derive the counterfactual LightAccount wallet address for an owner EOA.

    Calls ``LightAccountFactory.getAddress(owner, salt)`` on Derive Chain to
    get the deterministic CREATE2 address.  The contract does not need to be
    deployed -- the address is computed from ``create2(factory, salt, initCodeHash)``.

    :param owner_address:
        Owner EOA address.
    :param is_testnet:
        Whether to use the Derive testnet RPC.
    :param salt:
        CREATE2 salt (defaults to 0, matching the web UI).
    :return:
        Counterfactual LightAccount address.
    """
    rpc_url = DERIVE_TESTNET_RPC_URL if is_testnet else DERIVE_MAINNET_RPC_URL
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    factory = w3.eth.contract(
        address=Web3.to_checksum_address(LIGHT_ACCOUNT_FACTORY_ADDRESS),
        abi=LIGHT_ACCOUNT_FACTORY_ABI,
    )

    wallet_address = factory.functions.getAddress(
        Web3.to_checksum_address(owner_address),
        salt,
    ).call()

    logger.info(
        "Derived wallet address %s for owner %s (salt=%d)",
        wallet_address,
        owner_address,
        salt,
    )
    return wallet_address


def verify_session_key(client: DeriveApiClient) -> bool:
    """Verify that the session key works by reading account data.

    Makes an authenticated API call to ``private/get_subaccounts``
    to confirm the session key is valid and can access the account.
    This endpoint works even when no subaccounts exist yet.

    :param client:
        Derive API client with session_key_private set.
    :return:
        True if the session key works, False otherwise.
    """
    try:
        result = client._make_jsonrpc_request(
            method="private/get_subaccounts",
            params={"wallet": client.derive_wallet_address},
            authenticated=True,
        )
        subaccount_ids = result.get("subaccount_ids", [])
        logger.info(
            "Session key verified, account has %d subaccount(s)",
            len(subaccount_ids),
        )
        return True
    except Exception as e:
        logger.warning("Session key verification failed: %s", e)
        return False
