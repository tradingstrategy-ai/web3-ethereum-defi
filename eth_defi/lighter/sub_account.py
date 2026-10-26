"""Create Lighter sub-accounts for a Safe-owned master account.

A sub-account belongs to exactly one master account and has its own balances,
positions and API-key slots. Lighter creates it with the L2
``CreateSubAccount`` transaction (``tx_type = 9``), signed by an API key of the
master account. There is no L1 call for it, so a Safe-owned vault needs the
master account's delegated API key to create its sub-accounts.

After creation, register an API key on each sub-account with the L1
``changePubKey(subAccountIndex, apiKeyIndex, pubKey)`` Safe transaction from
:py:mod:`eth_defi.lighter.pubkey`; ``ZkLighter`` binds the call to the Safe's
master account and the sequencer checks that the sub-account belongs to it.

The number of sub-accounts depends on the master account's tier: Standard
allows 4, Plus 16 and Premium 64.

Authoritative Lighter documentation:

- Sub-accounts: https://docs.lighter.xyz/developers/integration-guides/create-accounts-programmatically.md
- Account types and limits: https://docs.lighter.xyz/developers/reference/account-types.md
"""

import asyncio
import logging
from typing import Any

from eth_typing import HexAddress

from eth_defi.lighter.api import fetch_lighter_accounts, wait_for_lighter_sub_accounts
from eth_defi.lighter.constants import LIGHTER_API_URL
from eth_defi.lighter.sdk import create_lighter_signer_client
from eth_defi.lighter.session import LighterSession

logger = logging.getLogger(__name__)

#: Highest sub-account count any Lighter account tier allows.
LIGHTER_MAX_SUB_ACCOUNTS = 64

#: Successful Lighter ``sendTx`` response code.
LIGHTER_RESPONSE_CODE_OK = 200


class LighterSubAccountError(RuntimeError):
    """Raised when Lighter rejects a sub-account creation."""


async def _submit_sub_account_creations(
    master_account_index: int,
    api_key_index: int,
    api_private_key: str,
    count: int,
    api_url: str,
) -> None:
    """Submit ``count`` sub-account creation transactions one at a time.

    The SDK client opens an ``aiohttp`` connector, which needs a running event
    loop, so it is created inside this coroutine.

    :param master_account_index:
        Master account signing the creations.
    :param api_key_index:
        Master API-key slot; the client holds only this key, so its nonce
        manager signs with it.
    :param api_private_key:
        Master API private key. Never logged.
    :param count:
        Number of sub-accounts to create.
    :param api_url:
        Lighter API base URL.
    """
    client = create_lighter_signer_client(master_account_index, api_key_index, api_private_key, api_url=api_url)
    try:
        for number in range(1, count + 1):
            # Let the SDK's nonce manager assign consecutive nonces locally. An
            # explicit key slot makes the signer fetch the next nonce from the
            # API for every transaction, which can repeat a nonce while the
            # previous creation is still queued.
            _tx_info, response, error = await client.create_sub_account()
            if error or response is None or getattr(response, "code", LIGHTER_RESPONSE_CODE_OK) != LIGHTER_RESPONSE_CODE_OK:
                raise LighterSubAccountError(f"Lighter rejected sub-account creation {number} of {count}")
            logger.info("Lighter sub-account creation %d of %d accepted, tx %s", number, count, response.tx_hash)
    finally:
        await client.close()


def create_lighter_sub_accounts(
    *,
    session: LighterSession,
    l1_address: HexAddress | str,
    master_account_index: int,
    api_key_index: int,
    api_private_key: str,
    count: int,
    timeout: int = 300,
    api_url: str = LIGHTER_API_URL,
) -> list[int]:
    """Create sub-accounts under a master account and return their indices.

    The function submits ``count`` ``CreateSubAccount`` transactions signed by
    the master account's API key, then waits until the public API lists the
    new sub-accounts. It is not idempotent: a rerun creates further
    sub-accounts. Check :py:func:`eth_defi.lighter.api.fetch_lighter_accounts`
    before retrying after a failure.

    :param session:
        Public Lighter HTTP session.
    :param l1_address:
        L1 address, normally a Lagoon Safe, owning the master account.
    :param master_account_index:
        Master account signing the creations.
    :param api_key_index:
        Registered master API-key slot.
    :param api_private_key:
        Master API private key. Never logged.
    :param count:
        Number of sub-accounts to create.
    :param timeout:
        Maximum seconds to wait for the sub-accounts to become visible.
    :param api_url:
        Lighter API base URL.
    :return:
        New sub-account indices in creation order.
    """
    if not 0 < count <= LIGHTER_MAX_SUB_ACCOUNTS:
        raise ValueError(f"Lighter sub-account count must be 1..{LIGHTER_MAX_SUB_ACCOUNTS}, got {count}")

    existing = fetch_lighter_accounts(session, l1_address)
    if existing is None or existing.master_index != master_account_index:
        raise LighterSubAccountError(f"L1 address {l1_address} does not own Lighter master account {master_account_index}")
    existing_indices = set(existing.sub_account_indices)

    asyncio.run(_submit_sub_account_creations(master_account_index, api_key_index, api_private_key, count, api_url))

    accounts = wait_for_lighter_sub_accounts(session, l1_address, len(existing_indices) + count, timeout=timeout)
    new_indices = [index for index in accounts.sub_account_indices if index not in existing_indices]
    if len(new_indices) != count:
        raise LighterSubAccountError(f"Expected {count} new Lighter sub-accounts, found {len(new_indices)}")
    logger.info("Created Lighter sub-accounts for master %d: %s", master_account_index, new_indices)
    return new_indices
