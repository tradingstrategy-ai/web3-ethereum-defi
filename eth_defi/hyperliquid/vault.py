"""Hyperliquid vault data extraction and analysis.

This module provides functionality for extracting historical Hyperliquid vault
events and serializing them into Pandas DataFrames for analysis.

API Endpoints
-------------

Hyperliquid has two relevant API surfaces for vault data:

**Official Info API** (``https://api.hyperliquid.xyz/info``):

- ``vaultDetails`` - Get details for a specific vault (works)
- ``userVaultEquities`` - Get user's vault positions (works)
- ``vaultSummaries`` - Documented but returns empty array (non-functional)

**Stats Data API** (``https://stats-data.hyperliquid.xyz/Mainnet/vaults``):

- Undocumented internal endpoint that powers the Hyperliquid web UI
- Returns all vaults (~8,000+) with APR, TVL, and PNL history arrays
- Uses GET instead of POST - serves pre-aggregated/cached data
- More comprehensive than the official API but may change without notice

The ``vaultSummaries`` endpoint in the official API is documented but non-functional
(returns ``[]``). For bulk vault listing, use the stats-data endpoint or the
bash script at ``scripts/hyperliquid/fetch-vault-metadata.sh``.

For more information about Hyperliquid vaults see:

- https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
- https://app.hyperliquid.xyz/vaults (web UI using stats-data endpoint)
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Iterator

import pandas as pd
import requests
from eth_typing import HexAddress
from requests import Session

from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.types import Percent

logger = logging.getLogger(__name__)

#: Hyperliquid mainnet API URL
HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz"

#: Hyperliquid testnet API URL
HYPERLIQUID_TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"

#: Hyperliquid stats-data API URL for vault listing (mainnet).
#: This is an undocumented internal endpoint that powers the Hyperliquid web UI.
#: It must be used instead of the official ``vaultSummaries`` endpoint which is
#: documented but non-functional (returns empty array). This endpoint returns
#: all vaults (~8,000+) with APR, TVL, and PNL history via GET request.
#: See: https://app.hyperliquid.xyz/vaults
HYPERLIQUID_STATS_URL = "https://stats-data.hyperliquid.xyz/Mainnet/vaults"

#: Hyperliquid stats-data API URL for vault listing (testnet)
HYPERLIQUID_STATS_TESTNET_URL = "https://stats-data.hyperliquid-testnet.xyz/Mainnet/vaults"


@dataclass(slots=True)
class VaultFollower:
    """Represents a follower (depositor) in a Hyperliquid vault.

    :param user: Follower's wallet address
    :param vault_equity: Current equity in the vault (USD)
    :param pnl: Profit/loss since entry
    :param all_time_pnl: All-time profit/loss
    :param days_following: Number of days following this vault
    :param vault_entry_time: Timestamp when user entered the vault (milliseconds)
    :param lockup_until: Timestamp when lockup period ends (milliseconds), if applicable
    """
    user: HexAddress
    vault_equity: Decimal
    pnl: Decimal
    all_time_pnl: Decimal
    days_following: int
    vault_entry_time: int
    lockup_until: int | None = None


@dataclass(slots=True)
class VaultSummary:
    """Summary information for a Hyperliquid vault.

    Contains both basic vault metadata and performance metrics from the
    stats-data endpoint.
    """

    #: Vault display name
    name: str
    #: Vault's blockchain address
    vault_address: HexAddress
    #: Vault manager/operator address
    leader: HexAddress
    #: Total Value Locked (USD)
    tvl: Decimal
    #: Whether deposits are closed
    is_closed: bool
    #: Vault relationship type (normal, child, parent)
    relationship_type: str
    #: Vault creation timestamp
    create_time: datetime | None = None
    #: Annual Percentage Rate (as decimal, e.g., 0.15 = 15%)
    apr: Percent | None = None
    #: PNL history for daily period (list of decimal strings)
    pnl_day: list[str] | None = None
    #: PNL history for weekly period (list of decimal strings)
    pnl_week: list[str] | None = None
    #: PNL history for monthly period (list of decimal strings)
    pnl_month: list[str] | None = None
    #: PNL history for all-time period (list of decimal strings)
    pnl_all_time: list[str] | None = None


class HyperliquidVault:
    """Client for extracting historical Hyperliquid vault events and data.

    This class provides methods to fetch vault information from the Hyperliquid
    API and serialize the data into Pandas DataFrames for analysis.

    Example::

        # Initialize for mainnet
        vault = HyperliquidVault()

        # Or for testnet
        vault = HyperliquidVault(testnet=True)

        # Get all vaults summary
        summaries_df = vault.get_vault_summaries_dataframe()
        print(f"Found {len(summaries_df)} vaults")

        # Get specific vault details
        details = vault.get_vault_details("0x...")

        # Get portfolio history for a vault
        history_df = vault.get_vault_portfolio_history_dataframe("0x...")

    :param testnet: Use testnet API instead of mainnet
    :param timeout: HTTP request timeout in seconds
    """

    def __init__(
        self,
        testnet: bool = False,
        timeout: float = 30.0,
    ):
        """Initialize the Hyperliquid vault client.

        :param testnet:
            If True, use the testnet API URL instead of mainnet
        :param timeout:
            HTTP request timeout in seconds
        """
        self.base_url = HYPERLIQUID_TESTNET_API_URL if testnet else HYPERLIQUID_API_URL
        self.timeout = timeout
        self.testnet = testnet

    def _make_request(
        self,
        request_type: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make a POST request to the Hyperliquid info endpoint.

        :param request_type:
            The type of info request (e.g., "vaultSummaries", "vaultDetails")
        :param params:
            Additional parameters for the request
        :return:
            Parsed JSON response
        :raises requests.HTTPError:
            If the HTTP request fails
        :raises requests.Timeout:
            If the request times out
        """
        url = f"{self.base_url}/info"
        payload = {"type": request_type}
        if params:
            payload.update(params)

        logger.debug(f"Making request to {url} with payload: {payload}")

        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()


class VaultSortKey(Enum):
    """Supported sort keys for vault listing.

    These correspond to fields available in VaultSummary that provide
    stable, deterministic ordering.
    """

    #: Sort by vault address (hex string, stable and unique)
    VAULT_ADDRESS = "vault_address"
    #: Sort by vault name (alphabetical)
    NAME = "name"
    #: Sort by TVL (Total Value Locked)
    TVL = "tvl"
    #: Sort by APR (Annual Percentage Rate)
    APR = "apr"
    #: Sort by creation time
    CREATE_TIME = "create_time"


def fetch_all_vaults(
    session: Session | None = None,
    stats_url: str = HYPERLIQUID_STATS_URL,
    timeout: float = 30.0,
) -> Iterator[VaultSummary]:
    """Iterate over all Hyperliquid vaults.

    This function fetches all vault summaries from the Hyperliquid stats-data API
    and yields them one by one. It handles API throttling using
    exponential backoff retry logic.

    .. note::

        This uses the undocumented stats-data endpoint (``HYPERLIQUID_STATS_URL``)
        instead of the official ``vaultSummaries`` endpoint which is documented
        but non-functional (returns empty array).

    Example::

        from eth_defi.hyperliquid.session import create_hyperliquid_session
        from eth_defi.hyperliquid.vault import fetch_all_vaults, HYPERLIQUID_STATS_TESTNET_URL, VaultSortKey

        # Create a session for API requests
        session = create_hyperliquid_session()

        # Iterate over all vaults (mainnet)
        for vault in fetch_all_vaults(session):
            print(f"Vault: {vault.name}, TVL: ${vault.tvl:,.2f}")

        # Use testnet
        for vault in fetch_all_vaults(session, stats_url=HYPERLIQUID_STATS_TESTNET_URL):
            print(f"Testnet vault: {vault.name}")

        # Filter vaults with high TVL
        high_tvl_vaults = [v for v in fetch_all_vaults(session) if v.tvl > 1_000_000]

        # Convert to list
        all_vaults = list(fetch_all_vaults(session))

        # Get vaults sorted by address (stable, deterministic order)
        sorted_vaults = list(fetch_all_vaults(session, sort_by=VaultSortKey.VAULT_ADDRESS))

        # Get top 10 vaults by TVL
        top_tvl = list(fetch_all_vaults(session, sort_by=VaultSortKey.TVL, sort_descending=True))[:10]

    :param session:
        A requests Session configured for Hyperliquid API.
        Use :py:func:`eth_defi.hyperliquid.session.create_hyperliquid_session` to create one.
        If None, a default session will be created.
    :param stats_url:
        Hyperliquid stats-data API URL.
        Use ``HYPERLIQUID_STATS_URL`` for mainnet or ``HYPERLIQUID_STATS_TESTNET_URL`` for testnet.
    :param timeout:
        HTTP request timeout in seconds
    :return:
        Iterator yielding VaultSummary objects
    :raises requests.HTTPError:
        If the HTTP request fails after all retries
    """
    if session is None:
        session = create_hyperliquid_session()

    logger.debug(f"Fetching all vaults from {stats_url}")

    # Stats-data endpoint uses GET, not POST
    response = session.get(
        stats_url,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    logger.info(f"Fetched {len(data)} vaults from Hyperliquid")

    def _parse_vault(item: dict) -> VaultSummary:
        """Parse a single vault item from the API response."""
        # Stats-data response has vault info nested under "summary" key
        summary = item.get("summary", {})
        relationship = summary.get("relationship", {})
        create_time_millis = summary.get("createTimeMillis")

        # Parse PNL history arrays from the response
        # Format: [["day", [...]], ["week", [...]], ["month", [...]], ["allTime", [...]]]
        pnls = {period: values for period, values in item.get("pnls", [])}

        return VaultSummary(
            name=summary.get("name", ""),
            vault_address=summary.get("vaultAddress", ""),
            leader=summary.get("leader", ""),
            tvl=Decimal(str(summary.get("tvl", "0"))),
            is_closed=summary.get("isClosed", False),
            relationship_type=relationship.get("type", "normal"),
            create_time=datetime.fromtimestamp(create_time_millis / 1000) if create_time_millis else None,
            apr=item.get("apr"),
            pnl_day=pnls.get("day"),
            pnl_week=pnls.get("week"),
            pnl_month=pnls.get("month"),
            pnl_all_time=pnls.get("allTime"),
        )

    # No sorting - yield directly for memory efficiency
    for item in data:
        yield _parse_vault(item)
    