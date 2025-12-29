"""Hyperliquid vault data extraction and analysis.

This module provides functionality for extracting historical Hyperliquid vault
events and serializing them into Pandas DataFrames for analysis.

Example::

    from eth_defi.hyperliquid.vault import HyperliquidVault

    # Initialize vault client
    vault = HyperliquidVault()

    # Get all vault summaries as DataFrame
    df = vault.get_vault_summaries_dataframe()

    # Get details for a specific vault
    details = vault.get_vault_details("0x1234...")

    # Get vault details with portfolio history as DataFrame
    df_history = vault.get_vault_portfolio_history_dataframe("0x1234...")

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
from typing import Any, Iterator

import pandas as pd
import requests
from eth_typing import HexAddress
from requests import Session
from requests.adapters import HTTPAdapter

from eth_defi.types import Percent
from eth_defi.velvet.logging_retry import LoggingRetry

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


#: Default number of retries for API requests
DEFAULT_RETRIES = 5

#: Default backoff factor for retries (seconds)
DEFAULT_BACKOFF_FACTOR = 0.5


def fetch_all_vaults(
    stats_url: str = HYPERLIQUID_STATS_URL,
    timeout: float = 30.0,
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
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

        from eth_defi.hyperliquid.vault import fetch_all_vaults, HYPERLIQUID_STATS_TESTNET_URL

        # Iterate over all vaults (mainnet)
        for vault in fetch_all_vaults():
            print(f"Vault: {vault.name}, TVL: ${vault.tvl:,.2f}")

        # Use testnet
        for vault in fetch_all_vaults(stats_url=HYPERLIQUID_STATS_TESTNET_URL):
            print(f"Testnet vault: {vault.name}")

        # Filter vaults with high TVL
        high_tvl_vaults = [v for v in fetch_all_vaults() if v.tvl > 1_000_000]

        # Convert to list
        all_vaults = list(fetch_all_vaults())

    :param stats_url:
        Hyperliquid stats-data API URL.
        Use ``HYPERLIQUID_STATS_URL`` for mainnet or ``HYPERLIQUID_STATS_TESTNET_URL`` for testnet.
    :param timeout:
        HTTP request timeout in seconds
    :param retries:
        Maximum number of retry attempts for failed requests
    :param backoff_factor:
        Backoff factor for exponential retry delays
    :return:
        Iterator yielding VaultSummary objects
    :raises requests.HTTPError:
        If the HTTP request fails after all retries
    """
    # Set up session with retry policy for API throttling
    session = Session()
    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        logger=logger,
    )
    session.mount("http://", HTTPAdapter(max_retries=retry_policy))
    session.mount("https://", HTTPAdapter(max_retries=retry_policy))

    logger.debug(f"Fetching all vaults from {stats_url}")

    # Stats-data endpoint uses GET, not POST
    response = session.get(
        stats_url,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    logger.info(f"Fetched {len(data)} vaults from Hyperliquid")

    for item in data:
        # Stats-data response has vault info nested under "summary" key
        summary = item.get("summary", {})
        relationship = summary.get("relationship", {})
        create_time_millis = summary.get("createTimeMillis")

        # Parse PNL history arrays from the response
        # Format: [["day", [...]], ["week", [...]], ["month", [...]], ["allTime", [...]]]
        pnls = {period: values for period, values in item.get("pnls", [])}

        yield VaultSummary(
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
