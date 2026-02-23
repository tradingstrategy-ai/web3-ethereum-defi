"""Hyperliquid info API client.

Typed wrappers for the `Hyperliquid info endpoint
<https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint>`__.

Uses :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session` for
HTTP connections with rate limiting and retry logic.

Example::

    from eth_defi.hyperliquid.api import fetch_user_vault_equities
    from eth_defi.hyperliquid.session import create_hyperliquid_session

    session = create_hyperliquid_session()
    equities = fetch_user_vault_equities(session, user="0xAbc...")
    for eq in equities:
        print(f"Vault {eq.vault_address}: {eq.equity} USDC")
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal

from eth_typing import HexAddress
from requests import Session

from eth_defi.hyperliquid.vault import HYPERLIQUID_API_URL, HYPERLIQUID_TESTNET_API_URL
from eth_defi.utils import from_unix_timestamp

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UserVaultEquity:
    """A user's equity position in a single Hypercore vault.

    Returned by :py:func:`fetch_user_vault_equities`.
    """

    #: Hypercore vault address
    vault_address: HexAddress

    #: USDC equity in the vault
    equity: Decimal

    #: UTC datetime until which withdrawals are locked.
    #:
    #: User-created vaults have a 1 day lock-up, protocol vaults (HLP) have 4 days.
    locked_until: datetime.datetime


def fetch_user_vault_equities(
    session: Session,
    user: HexAddress | str,
    server_url: str = HYPERLIQUID_API_URL,
    timeout: float = 10.0,
) -> list[UserVaultEquity]:
    """Fetch a user's equity positions across all Hypercore vaults.

    Calls the ``userVaultEquities`` info endpoint to retrieve the user's
    current vault deposits with equity and lock-up status.

    This is the recommended way to verify that a CoreWriter deposit
    landed on HyperCore — no EVM precompile needed.

    Example::

        from eth_defi.hyperliquid.api import fetch_user_vault_equities
        from eth_defi.hyperliquid.session import create_hyperliquid_session

        session = create_hyperliquid_session()

        # Mainnet
        equities = fetch_user_vault_equities(session, user="0xAbc...")

        # Testnet
        from eth_defi.hyperliquid.vault import HYPERLIQUID_TESTNET_API_URL

        equities = fetch_user_vault_equities(
            session,
            user="0xAbc...",
            server_url=HYPERLIQUID_TESTNET_API_URL,
        )

    :param session:
        HTTP session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`.

    :param user:
        On-chain address (the Safe address for Lagoon vaults).

    :param server_url:
        Hyperliquid API base URL.
        Use :py:data:`~eth_defi.hyperliquid.vault.HYPERLIQUID_TESTNET_API_URL` for testnet.

    :param timeout:
        HTTP request timeout in seconds.

    :return:
        List of vault equity positions. Empty list if the user has no vault deposits.
    """
    url = f"{server_url}/info"
    payload = {"type": "userVaultEquities", "user": user}

    logger.debug("Fetching userVaultEquities for %s from %s", user, url)

    response = session.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    results = []
    for entry in data:
        results.append(
            UserVaultEquity(
                vault_address=entry["vaultAddress"],
                equity=Decimal(entry["equity"]),
                locked_until=from_unix_timestamp(entry["lockedUntilTimestamp"] / 1000),
            )
        )

    logger.info(
        "User %s has %d vault position(s)",
        user,
        len(results),
    )

    return results
